from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path

from .bootstrap import RuntimeDependencies, build_work
from .hf_client import HF_CHAT_API
from .kb_bucket import (
    HF_KB_ACTIVE_POINTER_DEFAULT,
    HF_KB_MOUNT_DEFAULT,
    load_mounted_kb_release,
)
from .live_state import EmptyLiveStateProvider, HybridLiveStateProvider
from .service import CustomerAIWork


class RuntimeNotReady(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _required_file(value: str, code: str) -> Path:
    path = Path(value).expanduser() if value else None
    if path is None or not path.is_file():
        raise RuntimeNotReady(code)
    return path


def _required_value(values: Mapping[str, str], key: str, code: str) -> str:
    value = values.get(key, "").strip()
    if not value:
        raise RuntimeNotReady(code)
    return value


def _load_alias_registry(path_value: str) -> Mapping[str, Iterable[str]]:
    if not path_value.strip():
        return {}
    path = _required_file(path_value.strip(), "alias_registry_missing")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeNotReady("alias_registry_invalid") from exc
    if not isinstance(raw, dict):
        raise RuntimeNotReady("alias_registry_invalid")
    normalized: dict[str, list[str]] = {}
    for canonical, aliases in raw.items():
        if not isinstance(canonical, str) or not isinstance(aliases, list):
            raise RuntimeNotReady("alias_registry_invalid")
        normalized[canonical] = [str(alias) for alias in aliases if str(alias).strip()]
    return normalized


def _production_kb_files(values: Mapping[str, str]) -> tuple[Path, Path | None, Path | None, str]:
    build_id = _required_value(values, "CUSTOMER_AI_KB_BUILD_ID", "kb_build_id_missing")
    mount_path = values.get("CUSTOMER_AI_KB_MOUNT_PATH", "").strip() or HF_KB_MOUNT_DEFAULT
    pointer_name = (
        values.get("CUSTOMER_AI_KB_ACTIVE_POINTER", "").strip()
        or HF_KB_ACTIVE_POINTER_DEFAULT
    )
    try:
        release = load_mounted_kb_release(
            mount_path=mount_path,
            expected_build_id=build_id,
            pointer_name=pointer_name,
        )
    except ValueError as exc:
        raise RuntimeNotReady(str(exc)) from exc
    return (
        release.canonical_path,
        release.current_facts_path,
        release.aliases_path,
        release.build_id,
    )


def create_work_from_environment(
    env: Mapping[str, str] | None = None,
    *,
    role_pool: object | None = None,
) -> CustomerAIWork:
    values = os.environ if env is None else env

    if role_pool is None:
        kb_path, current_path, alias_path, build_id = _production_kb_files(values)
        generation_id = values.get("CUSTOMER_AI_KB_GENERATION_ID", "").strip() or build_id
    else:
        kb_path = _required_file(
            values.get("CUSTOMER_AI_KB_SNAPSHOT_PATH", "").strip(),
            "kb_snapshot_missing",
        )
        generation_id = values.get("CUSTOMER_AI_KB_GENERATION_ID", "").strip() or kb_path.stem
        current_value = values.get("CUSTOMER_AI_CURRENT_FACTS_PATH", "").strip()
        current_path = _required_file(current_value, "current_facts_missing") if current_value else None
        alias_value = values.get("CUSTOMER_AI_ALIAS_REGISTRY_PATH", "").strip()
        alias_path = _required_file(alias_value, "alias_registry_missing") if alias_value else None

    if current_path is not None:
        live_provider = HybridLiveStateProvider.from_jsonl(
            current_path,
            generation_id=f"{generation_id}:current",
        )
    else:
        live_provider = EmptyLiveStateProvider()

    token = (values.get("HF_TOKEN", "") or values.get("HF_KEY", "")).strip()
    if role_pool is None and not token:
        raise RuntimeNotReady("hf_token_missing")

    try:
        fuzzy_threshold = float(values.get("CUSTOMER_AI_JA_FUZZY_THRESHOLD", "90"))
    except ValueError as exc:
        raise RuntimeNotReady("japanese_fuzzy_threshold_invalid") from exc

    return build_work(
        RuntimeDependencies(
            live_state_provider=live_provider,
            japanese_alias_registry=_load_alias_registry(str(alias_path) if alias_path else ""),
            japanese_fuzzy_threshold=fuzzy_threshold,
            kb_snapshot_path=str(kb_path),
            kb_generation_id=generation_id,
            hf_token=token or None,
            role_pool=role_pool,
            hf_api_url=values.get("CUSTOMER_AI_HF_API_URL", "").strip() or HF_CHAT_API,
            timeout_seconds=30.0,
        )
    )
