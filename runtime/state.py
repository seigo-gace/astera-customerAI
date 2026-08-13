from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

from .schemas import FollowUpKind, GroundedFact, NeedLifecycle, NeedTask

try:
    from .japanese_skills import ConversationContext
except Exception:  # local isolated validation fallback
    @dataclass(frozen=True)
    class ConversationContext:
        active_topics: tuple[str, ...] = ()
        last_user_need: str = ""
        user_conditions: tuple[tuple[str, str], ...] = ()


_FOLLOW_CORRECTION = ("訂正", "違う", "ではなく", "さっきの回答", "前の回答", "修正")
_FOLLOW_CONDITION = ("の場合", "なら", "に変え", "条件", "ときは", "だったら")
_FOLLOW_CLARIFY = ("つまり", "ということ", "どういう意味", "何を意味", "具体的には", "もう少し")
_FOLLOW_CONTINUE = ("それ", "その", "これ", "続き", "さっきの", "前の", "同じ", "詳しく", "詳細")


def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def stable_need_id(text: str) -> str:
    digest = hashlib.sha256(_norm(text).encode("utf-8")).hexdigest()[:16]
    return f"need_{digest}"


def condition_signature(conditions: dict[str, str] | None) -> str:
    if not conditions:
        return ""
    raw = "|".join(f"{k}={v}" for k, v in sorted(conditions.items()))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class NeedLedgerEntry:
    stable_need_id: str
    text: str
    lifecycle: NeedLifecycle = NeedLifecycle.ACTIVE
    condition_signature: str = ""
    first_turn: int = 0
    last_turn: int = 0
    evidence_ids: set[str] = field(default_factory=set)
    evidence_gaps: set[str] = field(default_factory=set)
    satisfaction_blockers: set[str] = field(default_factory=set)


def evidence_ledger_key(fact: GroundedFact) -> str:
    scope = fact.conflict_group or fact.canonical_key or fact.fact_id
    cond = fact.condition_signature or "|".join([*sorted(fact.conditions), "--", *sorted(fact.exceptions)])
    validity = f"{fact.valid_from or ''}..{fact.valid_to or ''}"
    version = fact.fact_version or fact.generation_id or fact.freshness or ""
    raw = f"{scope}|{cond}|{validity}|{version}|{fact.authority}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


@dataclass
class EvidenceLedgerEntry:
    fact: GroundedFact
    stable_need_ids: set[str] = field(default_factory=set)
    retrieved_turn: int = 0
    invalidated: bool = False

    @property
    def version(self) -> str:
        if self.fact.fact_version:
            return self.fact.fact_version
        raw = "|".join(
            [
                self.fact.fact_id,
                self.fact.value,
                self.fact.generation_id or "",
                self.fact.freshness or "",
                self.fact.valid_from or "",
                self.fact.valid_to or "",
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class SessionState:
    active_topics: list[str] = field(default_factory=list)
    last_user_need: str = ""
    user_conditions: dict[str, str] = field(default_factory=dict)
    need_ledger: dict[str, NeedLedgerEntry] = field(default_factory=dict)
    evidence_ledger: dict[str, EvidenceLedgerEntry] = field(default_factory=dict)
    last_need_ids: list[str] = field(default_factory=list)
    last_follow_up_kind: FollowUpKind = FollowUpKind.NEW_NEED
    turn_index: int = 0

    def as_japanese_context(self) -> ConversationContext:
        return ConversationContext(
            active_topics=tuple(self.active_topics),
            last_user_need=self.last_user_need,
            user_conditions=tuple(sorted(self.user_conditions.items())),
        )

    @property
    def unresolved_need_ids(self) -> list[str]:
        return [
            need_id
            for need_id, entry in self.need_ledger.items()
            if entry.lifecycle in {NeedLifecycle.ACTIVE, NeedLifecycle.UNRESOLVED, NeedLifecycle.REOPENED}
        ]


class StateStore:
    def __init__(self):
        self._states: dict[str, SessionState] = {}

    def get(self, session_id: str) -> SessionState:
        return self._states.setdefault(session_id, SessionState())

    @staticmethod
    def classify_follow_up(message: str, state: SessionState) -> FollowUpKind:
        text = _norm(message)
        if not state.need_ledger:
            return FollowUpKind.NEW_NEED
        if any(marker in text for marker in _FOLLOW_CORRECTION):
            return FollowUpKind.CORRECTION
        if any(marker in text for marker in _FOLLOW_CONDITION):
            return FollowUpKind.CONDITION_CHANGE
        if any(marker in text for marker in _FOLLOW_CLARIFY):
            return FollowUpKind.CLARIFICATION
        if any(marker in text for marker in _FOLLOW_CONTINUE):
            return FollowUpKind.CONTINUE
        return FollowUpKind.NEW_NEED

    def begin_turn(self, session_id: str, message: str) -> FollowUpKind:
        state = self.get(session_id)
        state.turn_index += 1
        kind = self.classify_follow_up(message, state)
        state.last_follow_up_kind = kind
        if kind == FollowUpKind.CONDITION_CHANGE:
            state.user_conditions["follow_up_condition"] = message.strip()
        if kind in {FollowUpKind.CORRECTION, FollowUpKind.CONDITION_CHANGE}:
            for need_id in state.last_need_ids:
                entry = state.need_ledger.get(need_id)
                if entry and entry.lifecycle == NeedLifecycle.RESOLVED:
                    entry.lifecycle = NeedLifecycle.REOPENED
        return kind

    def bind_tasks(self, session_id: str, tasks: list[NeedTask], kind: FollowUpKind) -> list[NeedTask]:
        state = self.get(session_id)
        previous = [need_id for need_id in state.last_need_ids if need_id in state.need_ledger]
        current_sig = condition_signature(state.user_conditions)
        bound: list[NeedTask] = []
        for idx, task in enumerate(tasks):
            reuse_previous = (
                idx == 0
                and len(previous) == 1
                and kind in {
                    FollowUpKind.CONTINUE,
                    FollowUpKind.CLARIFICATION,
                    FollowUpKind.CONDITION_CHANGE,
                    FollowUpKind.CORRECTION,
                }
            )
            need_id = previous[0] if reuse_previous else (task.stable_need_id or stable_need_id(task.text))
            entry = state.need_ledger.get(need_id)
            if entry is None:
                entry = NeedLedgerEntry(
                    stable_need_id=need_id,
                    text=task.text,
                    lifecycle=NeedLifecycle.ACTIVE,
                    condition_signature=current_sig,
                    first_turn=state.turn_index,
                    last_turn=state.turn_index,
                )
                state.need_ledger[need_id] = entry
            else:
                entry.text = task.text
                entry.last_turn = state.turn_index
                if kind in {FollowUpKind.CONDITION_CHANGE, FollowUpKind.CORRECTION}:
                    entry.lifecycle = NeedLifecycle.REOPENED
                elif entry.lifecycle != NeedLifecycle.RESOLVED:
                    entry.lifecycle = NeedLifecycle.ACTIVE
                if kind == FollowUpKind.CONDITION_CHANGE:
                    entry.condition_signature = current_sig
            bound.append(
                task.model_copy(
                    update={
                        "stable_need_id": need_id,
                        "condition_signature": entry.condition_signature,
                    }
                )
            )
        state.last_need_ids = [task.stable_need_id for task in bound if task.stable_need_id]
        return bound

    def reusable_facts(self, session_id: str, tasks: list[NeedTask], kind: FollowUpKind) -> list[GroundedFact]:
        state = self.get(session_id)
        target_need_ids = {task.stable_need_id for task in tasks if task.stable_need_id}
        output: dict[str, GroundedFact] = {}
        for ledger_key, entry in state.evidence_ledger.items():
            fact = entry.fact
            if entry.invalidated or not (entry.stable_need_ids & target_need_ids):
                continue
            if fact.volatile or fact.lifecycle_status != "active":
                continue
            if kind == FollowUpKind.CONDITION_CHANGE and (fact.conditions or fact.exceptions or fact.condition_signature):
                continue
            output[ledger_key] = fact
        return list(output.values())

    def record_evidence(self, session_id: str, tasks: list[NeedTask], facts: list[GroundedFact]) -> None:
        state = self.get(session_id)
        need_ids = {task.stable_need_id for task in tasks if task.stable_need_id}
        for fact in facts:
            ledger_key = evidence_ledger_key(fact)
            entry = state.evidence_ledger.get(ledger_key)
            if entry is None or entry.fact.value != fact.value:
                entry = EvidenceLedgerEntry(fact=fact, retrieved_turn=state.turn_index)
                state.evidence_ledger[ledger_key] = entry
            entry.stable_need_ids.update(need_ids)
            entry.invalidated = False
            for need_id in need_ids:
                if need_id in state.need_ledger:
                    state.need_ledger[need_id].evidence_ids.add(fact.fact_id)

    def complete_turn(
        self,
        session_id: str,
        tasks: list[NeedTask],
        *,
        resolved_task_ids: set[str],
        unresolved_task_ids: set[str],
        evidence_gaps: set[str] | None = None,
        satisfaction_blockers: set[str] | None = None,
    ) -> None:
        state = self.get(session_id)
        evidence_gaps = evidence_gaps or set()
        satisfaction_blockers = satisfaction_blockers or set()
        for task in tasks:
            if not task.stable_need_id:
                continue
            entry = state.need_ledger[task.stable_need_id]
            entry.last_turn = state.turn_index
            if task.task_id in resolved_task_ids:
                entry.lifecycle = NeedLifecycle.RESOLVED
                entry.evidence_gaps.clear()
                entry.satisfaction_blockers.clear()
            elif task.task_id in unresolved_task_ids or evidence_gaps or satisfaction_blockers:
                entry.lifecycle = NeedLifecycle.UNRESOLVED
                entry.evidence_gaps.update(evidence_gaps)
                entry.satisfaction_blockers.update(satisfaction_blockers)
            else:
                entry.lifecycle = NeedLifecycle.ACTIVE

    def update(
        self,
        session_id: str,
        *,
        active_topics: list[str] | None = None,
        last_user_need: str | None = None,
        user_conditions: dict[str, str] | None = None,
    ) -> SessionState:
        state = self.get(session_id)
        if active_topics is not None:
            state.active_topics = list(active_topics)
        if last_user_need is not None:
            state.last_user_need = last_user_need
        if user_conditions is not None:
            state.user_conditions = dict(user_conditions)
        return state

    def delete(self, session_id: str) -> bool:
        return self._states.pop(session_id, None) is not None
