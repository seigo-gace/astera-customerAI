from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path

from .bootstrap import RuntimeDependencies, build_work
from .hf_client import HF_CHAT_API, HF_MODEL_4B, HF_MODEL_8B
from .kb_bucket import HF_KB_BUCKET_DEFAULT, HF_KB_FILE_DEFAULT, download_private_bucket_file
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


def _download_bucket_file(
    *,
    repo_id: str,
    revision: str,
    filename: str,
    token: str,
    blocker: str,
) -> Path:
    try:
        return download_private_bucket_file(
            repo_id=repo_id,
            revision=revision,
            filename=filename,
            token=token,
        )
    except Exception as exc:
        raise RuntimeNotReady(blocker) from exc


def _production_kb_files(values: Mapping[str, str], token: str) -> tuple[Path, Path | None, Path | None, str]:
    repo_id = values.get("CUSTOMER_AI_KB_REPO_ID", "").strip() or HF_KB_BUCKET_DEFAULT
    revision = _required_value(values, "CUSTOMER_AI_KB_REVISION", "kb_bucket_revision_missing")
    canonical_name = values.get("CUSTOMER_AI_KB_CANONICAL_FILE", "").strip() or HF_KB_FILE_DEFAULT

    canonical_path = _download_bucket_file(
        repo_id=repo_id,
        revision=revision,
        filename=canonical_name,
        token=token,
        blocker="kb_bucket_canonical_download_failed",
    )

    current_path: Path | None = None
    current_name = values.get("CUSTOMER_AI_KB_CURRENT_FILE", "").strip()
    if current_name:
        current_path = _download_bucket_file(
            repo_id=repo_id,
            revision=revision,
            filename=current_name,
            token=token,
            blocker="kb_bucket_current_download_failed",
        )

    alias_path: Path | None = None
    alias_name = values.get("CUSTOMER_AI_KB_ALIAS_FILE", "").strip()
    if alias_name:
        alias_path = _download_bucket_file(
            repo_id=repo_id,
            revision=revision,
            filename=alias_name,
            token=token,
            blocker="kb_bucket_alias_download_failed",
        )

    return canonical_path, current_path, alias_path, revision


def create_work_from_environment(
    env: Mapping[str, str] | None = None,
    *,
    role_pool: object | None = None,
) -> CustomerAIWork:
    values = os.environ if env is None else env
    token = (values.get("HF_TOKEN", "") or values.get("HF_KEY", "")).strip()

    if role_pool is None:
        if not token:
            raise RuntimeNotReady("hf_token_missing")
        kb_path, current_path, alias_path, bucket_revision = _production_kb_files(values, token)
        generation_id = values.get("CUSTOMER_AI_KB_GENERATION_ID", "").strip() or bucket_revision
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
            japanese_alias_registry=_load_alias_registry(str(alias_path) if alias_path else ""),
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
