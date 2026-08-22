from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

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


def load_mounted_kb_release(
    *,
    mount_path: str,
    expected_build_id: str,
    pointer_name: str = HF_KB_ACTIVE_POINTER_DEFAULT,
) -> MountedKBRelease:
    mount = Path(mount_path).expanduser()
    if not mount.is_dir():
        raise ValueError("kb_bucket_mount_missing")

    pointer = _resolve_file_inside_mount(
        mount,
        pointer_name,
        "kb_active_pointer_missing",
    )
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("kb_active_pointer_invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("kb_active_pointer_invalid")

    build_id = str(payload.get("build_id") or "").strip()
    if not build_id:
        raise ValueError("kb_active_build_id_missing")
    expected = expected_build_id.strip()
    if not expected:
        raise ValueError("kb_expected_build_id_missing")
    if build_id != expected:
        raise ValueError("kb_active_build_id_mismatch")

    canonical_path = _resolve_file_inside_mount(
        mount,
        str(payload.get("canonical_path") or ""),
        "kb_active_canonical_missing",
    )

    current_value = str(payload.get("current_facts_path") or "").strip()
    current_facts_path = (
        _resolve_file_inside_mount(mount, current_value, "kb_active_current_missing")
        if current_value
        else None
    )

    aliases_value = str(payload.get("aliases_path") or "").strip()
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
