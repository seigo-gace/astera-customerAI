from __future__ import annotations

import copy
import time
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .schemas import ConversationTurn, SessionContext
from .storage import AtomicStore


class ConversationCache:
    """Bounded session LRU backed by a persistent compact context file."""

    def __init__(self, root: Path, *, ttl_seconds: int, max_sessions: int, max_turns: int):
        self.root = root / "sessions"
        self.store = AtomicStore(root)
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self.max_turns = max_turns
        self._memory: OrderedDict[str, tuple[float, SessionContext]] = OrderedDict()

    def get(self, session_id: str) -> SessionContext:
        now = time.monotonic()
        cached = self._memory.get(session_id)
        if cached and cached[0] > now:
            self._memory.move_to_end(session_id)
            return self._copy(cached[1])
        if cached:
            self._memory.pop(session_id, None)

        path = self._path(session_id)
        if path.exists():
            context = SessionContext.model_validate(self.store.get_json(path))
        else:
            context = SessionContext(session_id=session_id)
        self._remember(context)
        return self._copy(context)

    def save(self, context: SessionContext) -> None:
        topic_evidence = {
            str(topic)[:160]: [str(item)[:200] for item in evidence_ids[-12:]]
            for topic, evidence_ids in list(context.topic_evidence.items())[-8:]
        }
        trimmed = context.model_copy(
            update={
                "turns": context.turns[-self.max_turns :],
                "unresolved_questions": context.unresolved_questions[-10:],
                "answered_questions": context.answered_questions[-24:],
                "last_kb_ids": context.last_kb_ids[-10:],
                "last_evidence_ids": context.last_evidence_ids[-12:],
                "topic_evidence": topic_evidence,
                "last_answer_summary": context.last_answer_summary[:1200],
                "updated_at": datetime.now(UTC),
            }
        )
        self.store.put_json(self._path(trimmed.session_id), trimmed.model_dump(mode="json"))
        self._remember(trimmed)

    def append_turns(
        self,
        context: SessionContext,
        *,
        user_text: str,
        assistant_text: str,
        message_id: str,
        kb_ids: list[str],
        evidence_ids: list[str] | None = None,
        question_ids: list[str] | None = None,
        answer_summary: str = "",
    ) -> SessionContext:
        evidence_ids = evidence_ids or [f"kb:{item}" for item in kb_ids]
        question_ids = question_ids or []
        turns = [
            *context.turns,
            ConversationTurn(role="user", text=user_text[:8000], message_id=message_id, question_ids=question_ids),
            ConversationTurn(
                role="assistant",
                text=assistant_text[:8000],
                message_id=message_id,
                kb_ids=kb_ids,
                evidence_ids=evidence_ids,
                question_ids=question_ids,
                answer_summary=answer_summary[:1200],
            ),
        ]
        return context.model_copy(update={"turns": turns[-self.max_turns :]})

    def compact(self, context: SessionContext, *, last_turns: int = 6) -> dict[str, Any]:
        turns = []
        for turn in context.turns[-last_turns:]:
            turns.append(
                {
                    "role": turn.role,
                    "text": turn.text[:1200],
                    "message_id": turn.message_id,
                    "kb_ids": turn.kb_ids[-4:],
                    "evidence_ids": turn.evidence_ids[-6:],
                    "question_ids": turn.question_ids[-8:],
                    "answer_summary": turn.answer_summary[:500],
                }
            )
        return {
            "user_goal": context.user_goal[:1000],
            "active_topic": context.active_topic[:160],
            "response_mode": context.response_mode[:80],
            "confirmed_details": context.confirmed_details,
            "user_state": context.user_state,
            "unresolved_questions": [item[:500] for item in context.unresolved_questions[-6:]],
            "answered_questions": [item[:300] for item in context.answered_questions[-12:]],
            "last_kb_ids": context.last_kb_ids[-6:],
            "last_evidence_ids": context.last_evidence_ids[-8:],
            "topic_evidence": context.topic_evidence,
            "last_answer_summary": context.last_answer_summary[:800],
            "turns": turns,
        }

    def prune_expired(self) -> int:
        now = time.monotonic()
        expired = [session_id for session_id, (expires_at, _) in self._memory.items() if expires_at <= now]
        for session_id in expired:
            self._memory.pop(session_id, None)
        return len(expired)

    def status(self) -> dict[str, int]:
        return {"memory_sessions": len(self._memory), "max_sessions": self.max_sessions, "max_turns": self.max_turns}

    def _path(self, session_id: str) -> Path:
        return self.root / session_id / "context.json"

    def _remember(self, context: SessionContext) -> None:
        self._memory[context.session_id] = (time.monotonic() + self.ttl_seconds, context)
        self._memory.move_to_end(context.session_id)
        while len(self._memory) > self.max_sessions:
            self._memory.popitem(last=False)

    @staticmethod
    def _copy(context: SessionContext) -> SessionContext:
        return SessionContext.model_validate(copy.deepcopy(context.model_dump(mode="json")))
