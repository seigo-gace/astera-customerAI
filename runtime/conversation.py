from __future__ import annotations

import copy
import shutil
import time
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .schemas import ConversationTurn, SessionContext
from .storage import AtomicStore


class ConversationCache:
    """Bounded in-memory LRU backed by one persistent session context file."""

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
            return SessionContext.model_validate(copy.deepcopy(cached[1].model_dump(mode="json")))
        if cached:
            self._memory.pop(session_id, None)

        path = self._path(session_id)
        if path.exists():
            context = SessionContext.model_validate(self.store.get_json(path))
        else:
            context = SessionContext(session_id=session_id)
        self._remember(context)
        return SessionContext.model_validate(copy.deepcopy(context.model_dump(mode="json")))

    def save(self, context: SessionContext) -> None:
        evidence_items = list(context.evidence_cache.items())[-16:]
        trimmed = context.model_copy(
            update={
                "turns": context.turns[-self.max_turns :],
                "unresolved_questions": context.unresolved_questions[-16:],
                "last_kb_ids": context.last_kb_ids[-16:],
                "answered_question_ids": context.answered_question_ids[-32:],
                "question_ledger": context.question_ledger[-24:],
                "evidence_cache": dict(evidence_items),
                "last_blueprint": _bound_structure(context.last_blueprint, max_chars=16000),
                "updated_at": datetime.now(UTC),
            }
        )
        self.store.put_json(self._path(trimmed.session_id), trimmed.model_dump(mode="json"))
        self._remember(trimmed)

    def delete(self, session_id: str) -> bool:
        """Delete one bounded session from memory and persistent runtime state."""
        existed = session_id in self._memory or (self.root / session_id).exists()
        self._memory.pop(session_id, None)
        shutil.rmtree(self.root / session_id, ignore_errors=True)
        return existed

    def append_turns(
        self,
        context: SessionContext,
        *,
        user_text: str,
        assistant_text: str,
        message_id: str,
        kb_ids: list[str],
    ) -> SessionContext:
        turns = [
            *context.turns,
            ConversationTurn(role="user", text=user_text[:8000], message_id=message_id),
            ConversationTurn(role="assistant", text=assistant_text[:8000], message_id=message_id, kb_ids=kb_ids),
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
                }
            )
        evidence = {
            key: _bound_structure(value, max_chars=1600)
            for key, value in list(context.evidence_cache.items())[-8:]
        }
        return {
            "user_goal": context.user_goal[:1000],
            "active_topic": context.active_topic[:160],
            "confirmed_details": _bound_structure(context.confirmed_details, max_chars=4000),
            "unresolved_questions": [item[:500] for item in context.unresolved_questions[-8:]],
            "last_kb_ids": context.last_kb_ids[-8:],
            "answered_question_ids": context.answered_question_ids[-16:],
            "question_ledger": [_bound_structure(item, max_chars=1000) for item in context.question_ledger[-12:]],
            "evidence_cache": evidence,
            "last_blueprint": _bound_structure(context.last_blueprint, max_chars=5000),
            "turns": turns,
        }

    def status(self) -> dict[str, int]:
        return {
            "memory_sessions": len(self._memory),
            "max_sessions": self.max_sessions,
            "max_turns": self.max_turns,
        }

    def _path(self, session_id: str) -> Path:
        return self.root / session_id / "context.json"

    def _remember(self, context: SessionContext) -> None:
        self._memory[context.session_id] = (time.monotonic() + self.ttl_seconds, context)
        self._memory.move_to_end(context.session_id)
        while len(self._memory) > self.max_sessions:
            self._memory.popitem(last=False)


def _bound_structure(value: Any, *, max_chars: int) -> Any:
    """Bound persistent helper state without converting it into an unbounded transcript."""
    if isinstance(value, str):
        return value[:max_chars]
    if isinstance(value, list):
        return [_bound_structure(item, max_chars=max(128, max_chars // max(1, min(len(value), 16)))) for item in value[-32:]]
    if isinstance(value, dict):
        bounded: dict[str, Any] = {}
        per_item = max(128, max_chars // max(1, min(len(value), 32)))
        for key, item in list(value.items())[-32:]:
            bounded[str(key)[:160]] = _bound_structure(item, max_chars=per_item)
        return bounded
    return value
