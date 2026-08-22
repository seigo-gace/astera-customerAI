from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from huggingface_hub import HfFileSystem, batch_bucket_files, bucket_info, create_bucket

DEFAULT_BUCKET_ID = "G-ACE/astera-customerai-kb"
DEFAULT_BUILD_ID = "kb-20260813T051036+0900"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_file(value: str, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.stat().st_size == 0:
        raise SystemExit(f"{label}_missing_or_empty={path}")
    return path


def _bucket_uri(bucket_id: str, path: str) -> str:
    return f"hf://buckets/{bucket_id}/{path}"


def _read_json(fs: HfFileSystem, uri: str) -> dict[str, object]:
    with fs.open(uri, "r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise SystemExit(f"remote_json_invalid={uri}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish a versioned Customer AI KB release to a private Hugging Face Storage Bucket."
    )
    parser.add_argument("--bucket-id", default=DEFAULT_BUCKET_ID)
    parser.add_argument("--build-id", default=DEFAULT_BUILD_ID)
    parser.add_argument("--canonical", required=True)
    parser.add_argument("--current-facts")
    parser.add_argument("--aliases")
    args = parser.parse_args()

    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        raise SystemExit("HF_TOKEN_missing")

    bucket_id = args.bucket_id.strip()
    build_id = args.build_id.strip()
    if not bucket_id:
        raise SystemExit("bucket_id_missing")
    if not build_id or "/" in build_id or ".." in build_id:
        raise SystemExit("build_id_invalid")

    canonical = _required_file(args.canonical, "canonical")
    current = _required_file(args.current_facts, "current_facts") if args.current_facts else None
    aliases = _required_file(args.aliases, "aliases") if args.aliases else None

    bucket = create_bucket(bucket_id, private=True, exist_ok=True, token=token)
    if str(bucket.bucket_id) != bucket_id:
        raise SystemExit(f"unexpected_bucket_id={bucket.bucket_id!r}")

    release_root = f"releases/{build_id}"
    canonical_remote = f"{release_root}/canonical.jsonl"
    current_remote = f"{release_root}/current-facts.jsonl" if current else ""
    aliases_remote = f"{release_root}/aliases.json" if aliases else ""
    manifest_remote = f"{release_root}/manifest.json"

    manifest: dict[str, object] = {
        "schema_version": 1,
        "build_id": build_id,
        "bucket_id": bucket_id,
        "files": {
            "canonical": {
                "path": canonical_remote,
                "sha256": _sha256(canonical),
                "bytes": canonical.stat().st_size,
            },
            "current_facts": (
                {
                    "path": current_remote,
                    "sha256": _sha256(current),
                    "bytes": current.stat().st_size,
                }
                if current
                else None
            ),
            "aliases": (
                {
                    "path": aliases_remote,
                    "sha256": _sha256(aliases),
                    "bytes": aliases.stat().st_size,
                }
                if aliases
                else None
            ),
        },
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    release_add: list[tuple[str | Path | bytes, str]] = [
        (canonical, canonical_remote),
        (manifest_bytes, manifest_remote),
    ]
    if current:
        release_add.append((current, current_remote))
    if aliases:
        release_add.append((aliases, aliases_remote))

    # Publish immutable release payload first. The active pointer is switched only
    # after every release file is remotely readable.
    batch_bucket_files(bucket_id, add=release_add, token=token)

    fs = HfFileSystem(token=token)
    for remote in [canonical_remote, manifest_remote, current_remote, aliases_remote]:
        if remote and not fs.exists(_bucket_uri(bucket_id, remote)):
            raise SystemExit(f"bucket_release_readback_missing={remote}")

    remote_manifest = _read_json(fs, _bucket_uri(bucket_id, manifest_remote))
    if remote_manifest.get("build_id") != build_id:
        raise SystemExit("bucket_manifest_build_id_mismatch")

    active: dict[str, object] = {
        "schema_version": 1,
        "build_id": build_id,
        "canonical_path": canonical_remote,
        "current_facts_path": current_remote or None,
        "aliases_path": aliases_remote or None,
        "manifest_path": manifest_remote,
    }
    active_bytes = (json.dumps(active, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    batch_bucket_files(bucket_id, add=[(active_bytes, "active.json")], token=token)

    remote_active = _read_json(fs, _bucket_uri(bucket_id, "active.json"))
    if remote_active != active:
        raise SystemExit("bucket_active_pointer_readback_mismatch")

    info = bucket_info(bucket_id, token=token)
    if not info.private:
        raise SystemExit("bucket_is_not_private")

    print(
        json.dumps(
            {
                "status": "success",
                "bucket_id": bucket_id,
                "bucket_uri": f"hf://buckets/{bucket_id}",
                "private": True,
                "build_id": build_id,
                "active_pointer": "active.json",
                "mount_path": "/data/customer-ai",
                "files": active,
                "rollback": "replace active.json with a previous release pointer",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
