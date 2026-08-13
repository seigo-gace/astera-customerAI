from __future__ import annotations

import inspect
from typing import Protocol

from .contracts import SearchPlan
from .schemas import GroundedFact, NeedTask


class CanonicalKnowledgeStore(Protocol):
    async def find_for_tasks(self, tasks: list[NeedTask], plan: SearchPlan | None = None) -> list[GroundedFact]: ...


class LiveStateProvider(Protocol):
    async def current_facts(self, tasks: list[NeedTask]) -> list[GroundedFact]: ...


class GroundingConflictError(ValueError):
    pass


def _condition_signature(fact: GroundedFact) -> str:
    if fact.condition_signature:
        return fact.condition_signature
    return "|".join([*sorted(fact.conditions), "--", *sorted(fact.exceptions)])


def _validity_overlap(a: GroundedFact, b: GroundedFact) -> bool:
    if a.valid_to and b.valid_from and a.valid_to < b.valid_from:
        return False
    if b.valid_to and a.valid_from and b.valid_to < a.valid_from:
        return False
    return True


def _same_scope(a: GroundedFact, b: GroundedFact) -> bool:
    ga = a.conflict_group or a.canonical_key or a.fact_id
    gb = b.conflict_group or b.canonical_key or b.fact_id
    return ga == gb and _condition_signature(a) == _condition_signature(b) and _validity_overlap(a, b)


class GroundingPlanner:
    PRIORITY = {"canonical": 0, "current": 1, "live": 2}

    def __init__(self, canonical: CanonicalKnowledgeStore, live: LiveStateProvider):
        self.canonical = canonical
        self.live = live

    async def _canonical_facts(self, tasks: list[NeedTask], plan: SearchPlan | None) -> list[GroundedFact]:
        method = self.canonical.find_for_tasks
        parameters = inspect.signature(method).parameters
        if "plan" in parameters:
            return await method(tasks, plan=plan)
        return await method(tasks)

    @staticmethod
    def _merge_same_value(previous: GroundedFact, current: GroundedFact) -> GroundedFact:
        sources = list(dict.fromkeys([previous.source_id, *previous.source_ids, current.source_id, *current.source_ids]))
        chosen = current if GroundingPlanner.PRIORITY[current.authority] >= GroundingPlanner.PRIORITY[previous.authority] else previous
        return chosen.model_copy(update={"source_ids": sources})

    async def build_shared_facts(
        self,
        tasks: list[NeedTask],
        plan: SearchPlan | None = None,
        *,
        reusable_facts: list[GroundedFact] | None = None,
    ) -> list[GroundedFact]:
        raw: list[GroundedFact] = list(reusable_facts or [])
        if tasks:
            raw.extend(await self._canonical_facts(tasks, plan))
            raw.extend(await self.live.current_facts(tasks))
        public = [f for f in raw if f.public and not f.legacy and not f.undecided and f.lifecycle_status == "active"]
        selected: list[GroundedFact] = []
        for fact in public:
            matched_index = None
            for idx, previous in enumerate(selected):
                if _same_scope(previous, fact):
                    matched_index = idx
                    break
            if matched_index is None:
                selected.append(fact)
                continue
            previous = selected[matched_index]
            if previous.value == fact.value:
                selected[matched_index] = self._merge_same_value(previous, fact)
                continue
            pp = self.PRIORITY[previous.authority]
            cp = self.PRIORITY[fact.authority]
            if cp > pp:
                selected[matched_index] = fact
            elif cp == pp:
                raise GroundingConflictError(
                    f"same-scope same-authority conflict for {fact.fact_id}: {previous.source_id} vs {fact.source_id}"
                )
        return sorted(
            selected,
            key=lambda item: (
                item.fact_id,
                _condition_signature(item),
                item.valid_from or "",
                item.valid_to or "",
            ),
        )
