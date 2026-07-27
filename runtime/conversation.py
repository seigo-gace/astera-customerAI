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
    """Small in-memory LRU backed by one persistent session context file."""

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
        trimmed = context.model_copy(
            update={
                "turns": context.turns[-self.max_turns :],
                "unresolved_questions": context.unresolved_questions[-8:],
                "last_kb_ids": context.last_kb_ids[-8:],
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
        return {
            "user_goal": context.user_goal[:1000],
            "active_topic": context.active_topic[:160],
            "confirmed_details": context.confirmed_details,
            "unresolved_questions": [item[:500] for item in context.unresolved_questions[-5:]],
            "last_kb_ids": context.last_kb_ids[-5:],
            "turns": turns,
        }

    def status(self) -> dict[str, int]:
        return {"memory_sessions": len(self._memory), "max_sessions": self.max_sessions, "max_turns": self.max_turns}

    def _path(self, session_id: str) -> Path:
        return self.root / session_id / "context.json"

    def _remember(self, context: SessionContext) -> None:
        self._memory[context.session_id] = (time.monotonic() + self.ttl_seconds, context)
        self._memory.move_to_end(context.session_id)
        while len(self._memory) > self.max_sessions:
            self._memory.popitem(last=False)
