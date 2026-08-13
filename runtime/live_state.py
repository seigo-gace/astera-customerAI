from __future__ import annotations

from pathlib import Path

from .kb_search import LocalHybridKnowledgeStore
from .schemas import GroundedFact, NeedTask


class EmptyLiveStateProvider:
    async def current_facts(self, tasks: list[NeedTask]) -> list[GroundedFact]:
        return []


class HybridLiveStateProvider:
    """NeedTask-scoped Current/Live fact retrieval using the same local hybrid index contract."""

    def __init__(self, store: LocalHybridKnowledgeStore):
        self.store = store

    @classmethod
    def from_jsonl(
        cls,
        path: str | Path,
        *,
        generation_id: str,
    ) -> "HybridLiveStateProvider":
        return cls(
            LocalHybridKnowledgeStore.from_jsonl(
                path,
                generation_id=generation_id,
            )
        )

    async def current_facts(self, tasks: list[NeedTask]) -> list[GroundedFact]:
        facts = await self.store.find_for_tasks(tasks)
        return [fact for fact in facts if fact.authority in {"current", "live"}]
