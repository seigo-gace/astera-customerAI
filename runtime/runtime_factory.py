from __future__ import annotations

from dataclasses import dataclass
from .hf_client import HFChatClient, HF_MODEL_ID
from .integration import DialogueIntegrator
from .internal_core import CustomerAIInternalCore
from .knowledge import GroundingPlanner
from .quality import CompletionGate
from .search_planner import SearchPlanner
from .shared_head import SharedHeadRolePool
from .skill_runtime import SkillRegistry
from .state import StateStore
from .task_decomposition import TaskDecomposer
from .writing_skills import default_writing_skills


@dataclass(frozen=True)
class InternalRuntimeDependencies:
    canonical_store: object
    live_state_provider: object
    japanese_skill_pack: object
    hf_token: str | None = None
    shared_head: object | None = None
    max_targeted_retry: int = 1
    model_id: str = HF_MODEL_ID
    hf_api_url: str = "https://router.huggingface.co/v1/chat/completions"
    timeout_seconds: float = 30.0


def build_internal_core(deps: InternalRuntimeDependencies) -> CustomerAIInternalCore:
    if deps.model_id != HF_MODEL_ID:
        raise ValueError("model_drift")
    roles=deps.shared_head
    if roles is None:
        client=HFChatClient(token=deps.hf_token or "",model_id=deps.model_id,api_url=deps.hf_api_url,timeout_seconds=deps.timeout_seconds)
        roles=SharedHeadRolePool(client)
    return CustomerAIInternalCore(decomposer=TaskDecomposer(),search=SearchPlanner(),grounding=GroundingPlanner(deps.canonical_store,deps.live_state_provider),skills=SkillRegistry(default_writing_skills()),roles=roles,integrator=DialogueIntegrator(),gate=CompletionGate(),state=StateStore(),japanese=deps.japanese_skill_pack,max_targeted_retry=deps.max_targeted_retry)
