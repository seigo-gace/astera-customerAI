from __future__ import annotations

import json
import re
import unicodedata
from collections import OrderedDict, defaultdict
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .contracts import SearchPlan
from .schemas import GroundedFact, NeedTask

_TOKEN_RE = re.compile(r"[A-Za-z0-9_.:+/-]+|[一-龥ぁ-んァ-ンー]+")


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


def _tokens(text: str) -> frozenset[str]:
    return frozenset(_normalize(token) for token in _TOKEN_RE.findall(text) if token.strip())


def _trigrams(text: str) -> frozenset[str]:
    compact = re.sub(r"\s+", "", _normalize(text))
    if len(compact) < 3:
        return frozenset({compact}) if compact else frozenset()
    return frozenset(compact[i : i + 3] for i in range(len(compact) - 2))


def _dice(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return 2.0 * len(a & b) / (len(a) + len(b))


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass(frozen=True)
class KnowledgeRecord:
    fact_id: str
    value: str
    source_id: str
    source_ids: tuple[str, ...] = ()
    authority: str = "canonical"
    title: str = ""
    aliases: tuple[str, ...] = ()
    knowledge_key: str | None = None
    domain: str | None = None
    topic: str | None = None
    conditions: tuple[str, ...] = ()
    exceptions: tuple[str, ...] = ()
    relations: tuple[str, ...] = ()
    language: str = "ja"
    lifecycle_status: str = "active"
    public: bool = True
    legacy: bool = False
    undecided: bool = False
    access_scope: str = "FREE"
    metadata: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "KnowledgeRecord":
        return cls(
            fact_id=str(raw["fact_id"]),
            value=str(raw["value"]),
            source_id=str(raw.get("source_id") or (raw.get("source_ids") or ["snapshot"])[0]),
            source_ids=tuple(str(x) for x in raw.get("source_ids", [])),
            authority=str(raw.get("authority", "canonical")),
            title=str(raw.get("title", "")),
            aliases=tuple(str(x) for x in raw.get("aliases", [])),
            knowledge_key=str(raw["knowledge_key"]) if raw.get("knowledge_key") else None,
            domain=str(raw["domain"]) if raw.get("domain") else None,
            topic=str(raw["topic"]) if raw.get("topic") else None,
            conditions=tuple(str(x) for x in raw.get("conditions", [])),
            exceptions=tuple(str(x) for x in raw.get("exceptions", [])),
            relations=tuple(str(x) for x in raw.get("relations", [])),
            language=str(raw.get("language", "ja")),
            lifecycle_status=str(raw.get("lifecycle_status", "active")),
            public=bool(raw.get("public", True)),
            legacy=bool(raw.get("legacy", False)),
            undecided=bool(raw.get("undecided", False)),
            access_scope=str(raw.get("access_scope", "FREE")),
            metadata=dict(raw.get("metadata") or {}),
        )

    def as_fact(self) -> GroundedFact:
        return GroundedFact(
            fact_id=self.fact_id,
            value=self.value,
            source_id=self.source_id,
            source_ids=list(self.source_ids) or [self.source_id],
            authority=self.authority,
            public=self.public,
            legacy=self.legacy,
            undecided=self.undecided,
            conditions=list(self.conditions),
            exceptions=list(self.exceptions),
            relations=list(self.relations),
            knowledge_key=self.knowledge_key,
            domain=self.domain,
            topic=self.topic,
        )


VectorSearch = Callable[[str, int], Awaitable[Sequence[tuple[str, float]]]]


class LocalHybridKnowledgeStore:
    """Generation-pinned in-process retrieval for Stable/Current knowledge.

    Fast path: exact key/title/alias.
    Standard path: keyword + Japanese trigram + optional vector, fused by RRF.
    Deep path: bounded relation expansion for comparison/troubleshooting/dependency-shaped tasks.
    """

    SEARCH_POLICY = "customer-ai-kb-v1"

    def __init__(
        self,
        records: Iterable[KnowledgeRecord],
        *,
        generation_id: str,
        release_bundle_id: str = "local",
        allowed_scopes: Sequence[str] = ("FREE", "PAID"),
        vector_search: VectorSearch | None = None,
        cache_size: int = 512,
        top_k: int = 6,
        relation_limit: int = 3,
    ):
        self.generation_id = generation_id
        self.release_bundle_id = release_bundle_id
        self.allowed_scopes = frozenset(allowed_scopes)
        self.vector_search = vector_search
        self.cache_size = max(1, cache_size)
        self.top_k = max(1, top_k)
        self.relation_limit = max(0, relation_limit)
        self._records = {
            record.fact_id: record
            for record in records
            if self._eligible(record)
        }
        self._exact: dict[str, set[str]] = defaultdict(set)
        self._tokens: dict[str, frozenset[str]] = {}
        self._trigrams: dict[str, frozenset[str]] = {}
        self._cache: OrderedDict[tuple[str, ...], tuple[str, ...]] = OrderedDict()
        self._build_indexes()

    @classmethod
    def from_jsonl(
        cls,
        path: str | Path,
        *,
        generation_id: str | None = None,
        release_bundle_id: str = "local",
        allowed_scopes: Sequence[str] = ("FREE", "PAID"),
        vector_search: VectorSearch | None = None,
    ) -> "LocalHybridKnowledgeStore":
        source = Path(path)
        rows: list[KnowledgeRecord] = []
        with source.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(KnowledgeRecord.from_mapping(json.loads(line)))
        return cls(
            rows,
            generation_id=generation_id or source.stem,
            release_bundle_id=release_bundle_id,
            allowed_scopes=allowed_scopes,
            vector_search=vector_search,
        )

    def _eligible(self, record: KnowledgeRecord) -> bool:
        return (
            record.public
            and not record.legacy
            and not record.undecided
            and record.lifecycle_status == "active"
            and record.access_scope in self.allowed_scopes
        )

    def _build_indexes(self) -> None:
        for fact_id, record in self._records.items():
            texts = [record.title, record.knowledge_key or "", *record.aliases]
            for text in texts:
                normalized = _normalize(text)
                if normalized:
                    self._exact[normalized].add(fact_id)
            searchable = " ".join(
                part
                for part in (
                    record.title,
                    record.value,
                    record.knowledge_key or "",
                    record.domain or "",
                    record.topic or "",
                    *record.aliases,
                )
                if part
            )
            self._tokens[fact_id] = _tokens(searchable)
            self._trigrams[fact_id] = _trigrams(searchable)

    def replace_generation(
        self,
        records: Iterable[KnowledgeRecord],
        *,
        generation_id: str,
        release_bundle_id: str | None = None,
    ) -> None:
        self.generation_id = generation_id
        if release_bundle_id is not None:
            self.release_bundle_id = release_bundle_id
        self._records = {record.fact_id: record for record in records if self._eligible(record)}
        self._exact.clear()
        self._tokens.clear()
        self._trigrams.clear()
        self._cache.clear()
        self._build_indexes()

    @staticmethod
    def _task_terms(task: NeedTask, plan: SearchPlan | None) -> list[str]:
        normalized_task = _normalize(task.text)
        terms = [_normalize(token) for token in _TOKEN_RE.findall(task.text)]
        if plan is not None:
            terms.extend(
                _normalize(term)
                for term in plan.search_terms
                if _normalize(term) and _normalize(term) in normalized_task
            )
        return list(dict.fromkeys(term for term in terms if term))

    def _cache_key(self, task: NeedTask, plan: SearchPlan | None) -> tuple[str, ...]:
        return (
            self.generation_id,
            self.release_bundle_id,
            self.SEARCH_POLICY,
            _normalize(task.text),
            task.response_shape,
            str(plan.mode.value if plan else "runtime_grounding"),
        )

    def _cache_get(self, key: tuple[str, ...]) -> list[KnowledgeRecord] | None:
        ids = self._cache.get(key)
        if ids is None:
            return None
        self._cache.move_to_end(key)
        return [self._records[fact_id] for fact_id in ids if fact_id in self._records]

    def _cache_put(self, key: tuple[str, ...], records: Sequence[KnowledgeRecord]) -> None:
        self._cache[key] = tuple(record.fact_id for record in records)
        self._cache.move_to_end(key)
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)

    @staticmethod
    def _rrf(rankings: Sequence[Sequence[str]], *, k: int = 60) -> dict[str, float]:
        scores: dict[str, float] = defaultdict(float)
        for ranking in rankings:
            for rank, fact_id in enumerate(ranking, 1):
                scores[fact_id] += 1.0 / (k + rank)
        return scores

    def _fast(self, task: NeedTask, plan: SearchPlan | None) -> list[str]:
        query = _normalize(task.text)
        hit = set(self._exact.get(query, ()))
        for term in self._task_terms(task, plan):
            hit.update(self._exact.get(_normalize(term), ()))
        return sorted(hit)

    def _lexical_rank(self, task: NeedTask, plan: SearchPlan | None) -> tuple[list[str], list[str]]:
        query_text = " ".join([task.text, *self._task_terms(task, plan)])
        q_tokens = _tokens(query_text)
        q_tri = _trigrams(query_text)
        keyword_scores: list[tuple[float, str]] = []
        trigram_scores: list[tuple[float, str]] = []
        for fact_id in self._records:
            keyword = _jaccard(q_tokens, self._tokens[fact_id])
            trigram = _dice(q_tri, self._trigrams[fact_id])
            if keyword > 0:
                keyword_scores.append((keyword, fact_id))
            if trigram >= 0.05:
                trigram_scores.append((trigram, fact_id))
        keyword_scores.sort(key=lambda item: (-item[0], item[1]))
        trigram_scores.sort(key=lambda item: (-item[0], item[1]))
        return (
            [fact_id for _, fact_id in keyword_scores[: self.top_k * 3]],
            [fact_id for _, fact_id in trigram_scores[: self.top_k * 3]],
        )

    async def _vector_rank(self, query: str) -> list[str]:
        if self.vector_search is None:
            return []
        rows = await self.vector_search(query, self.top_k * 3)
        return [fact_id for fact_id, _ in rows if fact_id in self._records]

    def _needs_deep(self, task: NeedTask) -> bool:
        return task.response_shape in {"comparison", "troubleshooting"} or task.intent in {
            "comparison",
            "troubleshooting",
            "dependency",
        }

    def _expand_relations(self, ranked: list[str]) -> list[str]:
        if self.relation_limit <= 0:
            return ranked
        output = list(ranked)
        seen = set(output)
        added = 0
        for fact_id in list(ranked):
            for relation_id in self._records[fact_id].relations:
                if relation_id in self._records and relation_id not in seen:
                    output.append(relation_id)
                    seen.add(relation_id)
                    added += 1
                    if added >= self.relation_limit:
                        return output
        return output

    async def _search_task(self, task: NeedTask, plan: SearchPlan | None) -> list[KnowledgeRecord]:
        key = self._cache_key(task, plan)
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        fast = self._fast(task, plan)
        if fast:
            ranked = fast[: self.top_k]
        else:
            keyword, trigram = self._lexical_rank(task, plan)
            vector = await self._vector_rank(task.text)
            fused = self._rrf([keyword, trigram, vector])
            ranked = [
                fact_id
                for fact_id, _ in sorted(
                    fused.items(),
                    key=lambda item: (-item[1], item[0]),
                )[: self.top_k]
            ]
        if self._needs_deep(task):
            ranked = self._expand_relations(ranked)
        records = [self._records[fact_id] for fact_id in ranked if fact_id in self._records]
        self._cache_put(key, records)
        return records

    async def find_for_tasks(
        self,
        tasks: list[NeedTask],
        plan: SearchPlan | None = None,
    ) -> list[GroundedFact]:
        selected: OrderedDict[str, GroundedFact] = OrderedDict()
        for task in tasks:
            for record in await self._search_task(task, plan):
                selected.setdefault(record.fact_id, record.as_fact())
        return list(selected.values())
