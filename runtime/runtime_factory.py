from __future__ import annotations

from dataclasses import dataclass

import httpx

from .hf_client import HFChatClient, HF_CHAT_API, HF_MODEL_8B
from .integration import DialogueIntegrator
from .internal_core import CustomerAIInternalCore
from .kb_search import LocalHybridKnowledgeStore
from .knowledge import GroundingPlanner
from .quality import CompletionGate
from .schemas import RoleName
from .search_planner import SearchPlanner
from .shared_head import ThreeRoleModelPool
from .skill_runtime import SkillRegistry
from .state import StateStore
from .task_decomposition import TaskDecomposer
from .writing_skills import default_writing_skills


@dataclass(frozen=True)
class InternalRuntimeDependencies:
    live_state_provider: object
    japanese_skill_pack: object
    canonical_store: object | None = None
    kb_snapshot_path: str | None = None
    kb_generation_id: str | None = None
    hf_token: str | None = None
    role_pool: object | None = None
    max_targeted_retry: int = 1
    constructive_model_id: str = HF_MODEL_8B
    adversarial_model_id: str = HF_MODEL_8B
    evidence_model_id: str = HF_MODEL_8B
    hf_api_url: str = HF_CHAT_API
    timeout_seconds: float = 300.0


def _canonical_store(deps: InternalRuntimeDependencies):
    if deps.canonical_store is not None:
        return deps.canonical_store
    if deps.kb_snapshot_path:
        return LocalHybridKnowledgeStore.from_jsonl(
            deps.kb_snapshot_path,
            generation_id=deps.kb_generation_id,
        )
    raise ValueError("canonical_store_or_kb_snapshot_required")


def _role_pool(deps: InternalRuntimeDependencies):
    if deps.role_pool is not None:
        return deps.role_pool
    expected = HF_MODEL_8B
    configured = {
        "constructive": deps.constructive_model_id,
        "adversarial": deps.adversarial_model_id,
        "evidence_bound": deps.evidence_model_id,
    }
    drift = {key: value for key, value in configured.items() if value != expected}
    if drift:
        raise ValueError(f"local_8b_model_drift:{drift}")

    # One local 8B model instance, three logical role clients, one shared HTTP pool.
    shared_http = httpx.AsyncClient(timeout=deps.timeout_seconds)
    clients = {
        role: HFChatClient(
            token="",
            model_id=expected,
            api_url=deps.hf_api_url,
            client=shared_http,
        )
        for role in RoleName
    }
    return ThreeRoleModelPool(clients)


def build_internal_core(deps: InternalRuntimeDependencies) -> CustomerAIInternalCore:
    return CustomerAIInternalCore(
        decomposer=TaskDecomposer(),
        search=SearchPlanner(),
        grounding=GroundingPlanner(_canonical_store(deps), deps.live_state_provider),
        skills=SkillRegistry(default_writing_skills()),
        roles=_role_pool(deps),
        integrator=DialogueIntegrator(),
        gate=CompletionGate(),
        state=StateStore(),
        japanese=deps.japanese_skill_pack,
        max_targeted_retry=deps.max_targeted_retry,
    )
