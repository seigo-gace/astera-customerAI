from __future__ import annotations

from dataclasses import dataclass

from .integration import DialogueIntegrator
from .japanese_skills import JapaneseShortQASkillPack
from .kagrra_bridge import KagrraBridge
from .knowledge import GroundingPlanner
from .model import ResidentRolePool
from .quality import CompletionGate
from .service import CustomerAIWork
from .state import StateStore
from .v8_bridge import V8Bridge

@dataclass(frozen=True)
class RuntimeDependencies:
    v8_adapter: object
    kagrra_adapter: object
    canonical_store: object
    live_state_provider: object
    backend_factory: object
    japanese_alias_registry: object
    japanese_fuzzy_threshold: float
    max_targeted_retry: int = 1

def build_work(deps: RuntimeDependencies) -> CustomerAIWork:
    return CustomerAIWork(v8=V8Bridge(deps.v8_adapter),kagrra=KagrraBridge(deps.kagrra_adapter),grounding=GroundingPlanner(deps.canonical_store,deps.live_state_provider),roles=ResidentRolePool(deps.backend_factory),integrator=DialogueIntegrator(),gate=CompletionGate(),state=StateStore(),japanese=JapaneseShortQASkillPack(alias_registry=deps.japanese_alias_registry,fuzzy_threshold=deps.japanese_fuzzy_threshold),max_targeted_retry=deps.max_targeted_retry)
