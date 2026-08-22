from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from huggingface_hub import HfApi

DEFAULT_REPO_ID = "G-ACE/astera-customerai-kb"


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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create/update the private Hugging Face Customer AI KB bucket without GitHub Actions."
    )
    parser.add_argument("--repo-id", default=DEFAULT_REPO_ID)
    parser.add_argument("--canonical", required=True)
    parser.add_argument("--current-facts")
    parser.add_argument("--aliases")
    parser.add_argument("--canonical-name", default="canonical.jsonl")
    parser.add_argument("--current-name", default="current-facts.jsonl")
    parser.add_argument("--aliases-name", default="aliases.json")
    args = parser.parse_args()

    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        raise SystemExit("HF_TOKEN_missing")

    canonical = _required_file(args.canonical, "canonical")
    current = _required_file(args.current_facts, "current_facts") if args.current_facts else None
    aliases = _required_file(args.aliases, "aliases") if args.aliases else None

    api = HfApi(token=token)
    who = api.whoami()
    if str(who.get("name") or "") != "G-ACE":
        raise SystemExit(f"unexpected_hf_identity={who.get('name')!r}")

    api.create_repo(
        repo_id=args.repo_id,
        repo_type="dataset",
        private=True,
        exist_ok=True,
    )

    operations: list[dict[str, object]] = []
    canonical_commit = api.upload_file(
        path_or_fileobj=str(canonical),
        path_in_repo=args.canonical_name,
        repo_id=args.repo_id,
        repo_type="dataset",
        commit_message=f"Publish Customer AI canonical KB {_sha256(canonical)[:16]}",
    )
    operations.append(
        {
            "file": args.canonical_name,
            "sha256": _sha256(canonical),
            "bytes": canonical.stat().st_size,
            "commit": str(canonical_commit.oid),
        }
    )

    if current is not None:
        current_commit = api.upload_file(
            path_or_fileobj=str(current),
            path_in_repo=args.current_name,
            repo_id=args.repo_id,
            repo_type="dataset",
            commit_message=f"Publish Customer AI current facts {_sha256(current)[:16]}",
        )
        operations.append(
            {
                "file": args.current_name,
                "sha256": _sha256(current),
                "bytes": current.stat().st_size,
                "commit": str(current_commit.oid),
            }
        )

    if aliases is not None:
        aliases_commit = api.upload_file(
            path_or_fileobj=str(aliases),
            path_in_repo=args.aliases_name,
            repo_id=args.repo_id,
            repo_type="dataset",
            commit_message=f"Publish Customer AI aliases {_sha256(aliases)[:16]}",
        )
        operations.append(
            {
                "file": args.aliases_name,
                "sha256": _sha256(aliases),
                "bytes": aliases.stat().st_size,
                "commit": str(aliases_commit.oid),
            }
        )

    info = api.repo_info(repo_id=args.repo_id, repo_type="dataset", files_metadata=True)
    remote_files = sorted(item.rfilename for item in (info.siblings or []))
    required_names = {args.canonical_name}
    if current is not None:
        required_names.add(args.current_name)
    if aliases is not None:
        required_names.add(args.aliases_name)
    missing = sorted(required_names - set(remote_files))
    if missing:
        raise SystemExit(f"bucket_readback_missing={missing}")

    evidence = {
        "status": "success",
        "repo_id": args.repo_id,
        "private": True,
        "revision": info.sha,
        "remote_files": remote_files,
        "uploads": operations,
        "runtime_env": {
            "CUSTOMER_AI_KB_REPO_ID": args.repo_id,
            "CUSTOMER_AI_KB_REVISION": info.sha,
            "CUSTOMER_AI_KB_CANONICAL_FILE": args.canonical_name,
            "CUSTOMER_AI_KB_CURRENT_FILE": args.current_name if current is not None else "",
            "CUSTOMER_AI_KB_ALIAS_FILE": args.aliases_name if aliases is not None else "",
        },
    }
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
