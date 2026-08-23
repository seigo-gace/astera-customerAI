from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from huggingface_hub import HfFileSystem

HF_KB_BUCKET_DEFAULT = "G-ACE/astera-customerai-kb"
HF_KB_MOUNT_DEFAULT = "/data/customer-ai"
HF_KB_ACTIVE_POINTER_DEFAULT = "active.json"


@dataclass(frozen=True)
class MountedKBRelease:
    build_id: str
    canonical_path: Path
    current_facts_path: Path | None
    aliases_path: Path | None


def _resolve_file_inside_mount(mount: Path, relative_path: str, code: str) -> Path:
    relative = relative_path.strip()
    if not relative:
        raise ValueError(code)
    mount_resolved = mount.resolve()
    candidate = (mount_resolved / relative).resolve()
    if candidate != mount_resolved and mount_resolved not in candidate.parents:
        raise ValueError("kb_pointer_path_escape")
    if not candidate.is_file() or candidate.stat().st_size == 0:
        raise ValueError(code)
    return candidate


def _safe_relative(value: str, code: str) -> str:
    relative = value.strip()
    if not relative:
        raise ValueError(code)
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("kb_pointer_path_escape")
    return path.as_posix()


def _validate_pointer(payload: object, expected_build_id: str) -> tuple[str, str, str | None, str | None, str]:
    if not isinstance(payload, dict):
        raise ValueError("kb_active_pointer_invalid")

    build_id = str(payload.get("build_id") or "").strip()
    expected = expected_build_id.strip()
    if not build_id:
        raise ValueError("kb_active_build_id_missing")
    if not expected:
        raise ValueError("kb_expected_build_id_missing")
    if build_id != expected:
        raise ValueError("kb_active_build_id_mismatch")

    release_prefix = f"releases/{expected}/"
    canonical = _safe_relative(str(payload.get("canonical_path") or ""), "kb_active_canonical_missing")
    manifest = _safe_relative(str(payload.get("manifest_path") or ""), "kb_active_manifest_missing")
    if not canonical.startswith(release_prefix) or not manifest.startswith(release_prefix):
        raise ValueError("kb_pointer_release_path_mismatch")

    current_raw = str(payload.get("current_facts_path") or "").strip()
    current = _safe_relative(current_raw, "kb_active_current_missing") if current_raw else None
    if current is not None and not current.startswith(release_prefix):
        raise ValueError("kb_pointer_release_path_mismatch")

    aliases_raw = str(payload.get("aliases_path") or "").strip()
    aliases = _safe_relative(aliases_raw, "kb_active_aliases_missing") if aliases_raw else None
    if aliases is not None and not aliases.startswith(release_prefix):
        raise ValueError("kb_pointer_release_path_mismatch")

    return build_id, canonical, current, aliases, manifest


def load_mounted_kb_release(
    *,
    mount_path: str,
    expected_build_id: str,
    pointer_name: str = HF_KB_ACTIVE_POINTER_DEFAULT,
) -> MountedKBRelease:
    mount = Path(mount_path).expanduser()
    if not mount.is_dir():
        raise ValueError("kb_bucket_mount_missing")

    pointer = _resolve_file_inside_mount(mount, pointer_name, "kb_active_pointer_missing")
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("kb_active_pointer_invalid") from exc

    build_id, canonical_value, current_value, aliases_value, manifest_value = _validate_pointer(
        payload,
        expected_build_id,
    )
    _resolve_file_inside_mount(mount, manifest_value, "kb_active_manifest_missing")

    canonical_path = _resolve_file_inside_mount(mount, canonical_value, "kb_active_canonical_missing")
    current_facts_path = (
        _resolve_file_inside_mount(mount, current_value, "kb_active_current_missing")
        if current_value
        else None
    )
    aliases_path = (
        _resolve_file_inside_mount(mount, aliases_value, "kb_active_aliases_missing")
        if aliases_value
        else None
    )

    return MountedKBRelease(
        build_id=build_id,
        canonical_path=canonical_path,
        current_facts_path=current_facts_path,
        aliases_path=aliases_path,
    )


def load_remote_kb_release(
    *,
    bucket_id: str,
    token: str,
    expected_build_id: str,
    pointer_name: str = HF_KB_ACTIVE_POINTER_DEFAULT,
    cache_root: str | None = None,
) -> MountedKBRelease:
    bucket = bucket_id.strip()
    auth = token.strip()
    if not bucket:
        raise ValueError("kb_bucket_id_missing")
    if not auth:
        raise ValueError("hf_token_missing")

    fs = HfFileSystem(token=auth)
    base = f"hf://buckets/{bucket}"

    def read_remote(relative_path: str, code: str) -> bytes:
        relative = _safe_relative(relative_path, code)
        try:
            with fs.open(f"{base}/{relative}", "rb") as handle:
                raw = handle.read()
        except Exception as exc:
            raise ValueError(code) from exc
        if not raw:
            raise ValueError(code)
        return raw

    pointer_name = _safe_relative(pointer_name, "kb_active_pointer_missing")
    try:
        pointer_payload = json.loads(read_remote(pointer_name, "kb_active_pointer_missing").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("kb_active_pointer_invalid") from exc

    build_id, canonical_value, current_value, aliases_value, manifest_value = _validate_pointer(
        pointer_payload,
        expected_build_id,
    )

    try:
        manifest_payload = json.loads(read_remote(manifest_value, "kb_active_manifest_missing").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("kb_active_manifest_invalid") from exc
    if not isinstance(manifest_payload, dict) or str(manifest_payload.get("build_id") or "") != build_id:
        raise ValueError("kb_active_manifest_invalid")

    manifest_files: dict[str, tuple[int, str]] = {}
    for item in manifest_payload.get("files") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("path") or "").strip()
        sha256 = str(item.get("sha256") or "").strip()
        try:
            size = int(item.get("bytes"))
        except (TypeError, ValueError):
            continue
        if name and sha256:
            manifest_files[name] = (size, sha256)

    cache = Path(cache_root).expanduser() if cache_root else Path(tempfile.gettempdir()) / "astera-customerai-kb"
    release_cache = cache / build_id
    release_cache.mkdir(parents=True, exist_ok=True)

    def materialize(relative_path: str, code: str) -> Path:
        relative = _safe_relative(relative_path, code)
        name = PurePosixPath(relative).name
        expected = manifest_files.get(name)
        if expected is None:
            raise ValueError("kb_manifest_file_missing")
        expected_size, expected_sha = expected
        destination = release_cache / name

        if destination.is_file():
            cached = destination.read_bytes()
            if len(cached) == expected_size and hashlib.sha256(cached).hexdigest() == expected_sha:
                return destination

        raw = read_remote(relative, code)
        if len(raw) != expected_size or hashlib.sha256(raw).hexdigest() != expected_sha:
            raise ValueError("kb_remote_integrity_mismatch")
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(raw)
        temporary.replace(destination)
        return destination

    canonical_path = materialize(canonical_value, "kb_active_canonical_missing")
    current_facts_path = materialize(current_value, "kb_active_current_missing") if current_value else None
    aliases_path = materialize(aliases_value, "kb_active_aliases_missing") if aliases_value else None

    return MountedKBRelease(
        build_id=build_id,
        canonical_path=canonical_path,
        current_facts_path=current_facts_path,
        aliases_path=aliases_path,
    )
