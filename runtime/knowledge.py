from __future__ import annotations

from typing import Protocol

from .schemas import GroundedFact, NeedTask


class CanonicalKnowledgeStore(Protocol):
    async def find_for_tasks(self, tasks: list[NeedTask]) -> list[GroundedFact]: ...


class LiveStateProvider(Protocol):
    async def current_facts(self, tasks: list[NeedTask]) -> list[GroundedFact]: ...


class GroundingConflictError(ValueError):
    pass


class GroundingPlanner:
    PRIORITY = {"canonical": 0, "current": 1, "live": 2}

    def __init__(self, canonical: CanonicalKnowledgeStore, live: LiveStateProvider):
        self.canonical = canonical
        self.live = live

    async def build_shared_facts(self, tasks: list[NeedTask]) -> list[GroundedFact]:
        raw = [*await self.canonical.find_for_tasks(tasks), *await self.live.current_facts(tasks)]
        public = [f for f in raw if f.public and not f.legacy and not f.undecided]
        selected: dict[str, GroundedFact] = {}
        for fact in public:
            previous = selected.get(fact.fact_id)
            if previous is None:
                selected[fact.fact_id] = fact
                continue
            pp = self.PRIORITY[previous.authority]
            cp = self.PRIORITY[fact.authority]
            if cp > pp:
                selected[fact.fact_id] = fact
            elif cp == pp and previous.value != fact.value:
                raise GroundingConflictError(f"same-authority conflict for {fact.fact_id}: {previous.source_id} vs {fact.source_id}")
        return sorted(selected.values(), key=lambda item: item.fact_id)
