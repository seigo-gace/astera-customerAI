from __future__ import annotations

from dataclasses import dataclass

import httpx

from .hf_client import HFChatClient, HF_CHAT_API, HF_MODEL_4B, HF_MODEL_8B
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

LOCAL_EVIDENCE_API = "http://127.0.0.1:8083/v1/chat/completions"


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
    constructive_model_id: str = HF_MODEL_4B
    adversarial_model_id: str = HF_MODEL_4B
    evidence_model_id: str = HF_MODEL_8B
    hf_api_url: str = HF_CHAT_API
    evidence_api_url: str = LOCAL_EVIDENCE_API
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
    if deps.constructive_model_id != HF_MODEL_4B:
        raise ValueError("constructive_model_drift")
    if deps.adversarial_model_id != HF_MODEL_4B:
        raise ValueError("adversarial_model_drift")
    if deps.evidence_model_id != HF_MODEL_8B:
        raise ValueError("evidence_model_drift")

    token = deps.hf_token or ""
    if not token.strip():
        raise ValueError("hf_token_required")

    shared_http = httpx.AsyncClient(timeout=deps.timeout_seconds)
    clients = {
        RoleName.CONSTRUCTIVE: HFChatClient(
            token=token,
            model_id=deps.constructive_model_id,
            api_url=deps.hf_api_url,
            client=shared_http,
        ),
        RoleName.ADVERSARIAL: HFChatClient(
            token=token,
            model_id=deps.adversarial_model_id,
            api_url=deps.hf_api_url,
            client=shared_http,
        ),
        RoleName.EVIDENCE_BOUND: HFChatClient(
            token="",
            model_id=deps.evidence_model_id,
            api_url=deps.evidence_api_url,
            client=shared_http,
        ),
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
