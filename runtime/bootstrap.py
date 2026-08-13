from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .hf_client import HF_MODEL_ID
from .japanese_skills import JapaneseShortQASkillPack
from .runtime_factory import InternalRuntimeDependencies, build_internal_core
from .service import CustomerAIWork


@dataclass(frozen=True)
class RuntimeDependencies:
    canonical_store: object
    live_state_provider: object
    japanese_alias_registry: Mapping[str, Iterable[str]]
    japanese_fuzzy_threshold: float
    hf_token: str | None = None
    shared_head: object | None = None
    max_targeted_retry: int = 1
    model_id: str = HF_MODEL_ID
    hf_api_url: str = "https://router.huggingface.co/v1/chat/completions"
    timeout_seconds: float = 30.0


def build_work(deps: RuntimeDependencies) -> CustomerAIWork:
    if not (0.0 <= float(deps.japanese_fuzzy_threshold) <= 100.0):
        raise ValueError("japanese_fuzzy_threshold_out_of_range")
    if not isinstance(deps.max_targeted_retry, int) or deps.max_targeted_retry < 0:
        raise ValueError("max_targeted_retry_invalid")
    japanese=JapaneseShortQASkillPack(alias_registry=deps.japanese_alias_registry,fuzzy_threshold=float(deps.japanese_fuzzy_threshold))
    core=build_internal_core(InternalRuntimeDependencies(canonical_store=deps.canonical_store,live_state_provider=deps.live_state_provider,japanese_skill_pack=japanese,hf_token=deps.hf_token,shared_head=deps.shared_head,max_targeted_retry=deps.max_targeted_retry,model_id=deps.model_id,hf_api_url=deps.hf_api_url,timeout_seconds=deps.timeout_seconds))
    return CustomerAIWork(core)
