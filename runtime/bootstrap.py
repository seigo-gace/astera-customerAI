from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .hf_client import HF_CHAT_API, HF_MODEL_8B
from .japanese_skills import JapaneseShortQASkillPack
from .runtime_factory import InternalRuntimeDependencies, build_internal_core
from .service import CustomerAIWork


@dataclass(frozen=True)
class RuntimeDependencies:
    live_state_provider: object
    japanese_alias_registry: Mapping[str, Iterable[str]]
    japanese_fuzzy_threshold: float
    canonical_store: object | None = None
    kb_snapshot_path: str | None = None
    kb_generation_id: str | None = None
    hf_token: str | None = None
    role_pool: object | None = None
    shared_head: object | None = None  # compatibility injection only; not a single-role contract
    max_targeted_retry: int = 1
    constructive_model_id: str = HF_MODEL_8B
    adversarial_model_id: str = HF_MODEL_8B
    evidence_model_id: str = HF_MODEL_8B
    hf_api_url: str = HF_CHAT_API
    timeout_seconds: float = 300.0


def build_work(deps: RuntimeDependencies) -> CustomerAIWork:
    if not (0.0 <= float(deps.japanese_fuzzy_threshold) <= 100.0):
        raise ValueError("japanese_fuzzy_threshold_out_of_range")
    if not isinstance(deps.max_targeted_retry, int) or deps.max_targeted_retry < 0:
        raise ValueError("max_targeted_retry_invalid")
    japanese = JapaneseShortQASkillPack(
        alias_registry=deps.japanese_alias_registry,
        fuzzy_threshold=float(deps.japanese_fuzzy_threshold),
    )
    injected_pool = deps.role_pool if deps.role_pool is not None else deps.shared_head
    core = build_internal_core(
        InternalRuntimeDependencies(
            canonical_store=deps.canonical_store,
            kb_snapshot_path=deps.kb_snapshot_path,
            kb_generation_id=deps.kb_generation_id,
            live_state_provider=deps.live_state_provider,
            japanese_skill_pack=japanese,
            hf_token=deps.hf_token,
            role_pool=injected_pool,
            max_targeted_retry=deps.max_targeted_retry,
            constructive_model_id=deps.constructive_model_id,
            adversarial_model_id=deps.adversarial_model_id,
            evidence_model_id=deps.evidence_model_id,
            hf_api_url=deps.hf_api_url,
            timeout_seconds=deps.timeout_seconds,
        )
    )
    return CustomerAIWork(core)
