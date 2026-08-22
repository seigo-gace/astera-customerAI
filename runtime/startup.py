from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path

from .bootstrap import RuntimeDependencies, build_work
from .hf_client import HF_CHAT_API, HF_MODEL_4B, HF_MODEL_8B
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


def _required_https_endpoint(values: Mapping[str, str], key: str, code: str) -> str:
    value = _required_value(values, key, code)
    if not value.startswith("https://"):
        raise RuntimeNotReady(f"{code}_invalid")
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


def create_work_from_environment(
    env: Mapping[str, str] | None = None,
    *,
    role_pool: object | None = None,
) -> CustomerAIWork:
    values = os.environ if env is None else env
    kb_path = _required_file(values.get("CUSTOMER_AI_KB_SNAPSHOT_PATH", "").strip(), "kb_snapshot_missing")
    generation_id = values.get("CUSTOMER_AI_KB_GENERATION_ID", "").strip() or kb_path.stem

    current_value = values.get("CUSTOMER_AI_CURRENT_FACTS_PATH", "").strip()
    if current_value:
        current_path = _required_file(current_value, "current_facts_missing")
        live_provider = HybridLiveStateProvider.from_jsonl(
            current_path,
            generation_id=f"{generation_id}:current",
        )
    else:
        live_provider = EmptyLiveStateProvider()

    token = (values.get("HF_TOKEN", "") or values.get("HF_KEY", "")).strip()
    if role_pool is None and not token:
        raise RuntimeNotReady("hf_token_missing")

    if role_pool is None:
        model_4b_id = _required_value(values, "CUSTOMER_AI_MODEL_4B_ID", "trained_model_4b_id_missing")
        _required_value(values, "CUSTOMER_AI_MODEL_4B_REVISION", "trained_model_4b_revision_missing")
        model_4b_api_url = _required_https_endpoint(
            values,
            "CUSTOMER_AI_MODEL_4B_API_URL",
            "trained_model_4b_endpoint_missing",
        )
        model_8b_id = _required_value(values, "CUSTOMER_AI_MODEL_8B_ID", "trained_model_8b_id_missing")
        _required_value(values, "CUSTOMER_AI_MODEL_8B_REVISION", "trained_model_8b_revision_missing")
        model_8b_api_url = _required_https_endpoint(
            values,
            "CUSTOMER_AI_MODEL_8B_API_URL",
            "trained_model_8b_endpoint_missing",
        )
        if model_4b_id == HF_MODEL_4B or model_8b_id == HF_MODEL_8B:
            raise RuntimeNotReady("trained_domain_model_required")
    else:
        model_4b_id = HF_MODEL_4B
        model_8b_id = HF_MODEL_8B
        model_4b_api_url = HF_CHAT_API
        model_8b_api_url = HF_CHAT_API

    try:
        fuzzy_threshold = float(values.get("CUSTOMER_AI_JA_FUZZY_THRESHOLD", "90"))
    except ValueError as exc:
        raise RuntimeNotReady("japanese_fuzzy_threshold_invalid") from exc

    return build_work(
        RuntimeDependencies(
            live_state_provider=live_provider,
            japanese_alias_registry=_load_alias_registry(values.get("CUSTOMER_AI_ALIAS_REGISTRY_PATH", "")),
            japanese_fuzzy_threshold=fuzzy_threshold,
            kb_snapshot_path=str(kb_path),
            kb_generation_id=generation_id,
            hf_token=token or None,
            role_pool=role_pool,
            constructive_model_id=model_4b_id,
            adversarial_model_id=model_4b_id,
            evidence_model_id=model_8b_id,
            constructive_api_url=model_4b_api_url,
            adversarial_api_url=model_4b_api_url,
            evidence_api_url=model_8b_api_url,
            timeout_seconds=30.0,
        )
    )
