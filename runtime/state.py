from __future__ import annotations

from dataclasses import dataclass, field

from .japanese_skills import ConversationContext


@dataclass
class SessionState:
    active_topics: list[str] = field(default_factory=list)
    last_user_need: str = ""
    user_conditions: dict[str, str] = field(default_factory=dict)

    def as_japanese_context(self) -> ConversationContext:
        return ConversationContext(
            active_topics=tuple(self.active_topics),
            last_user_need=self.last_user_need,
            user_conditions=tuple(sorted(self.user_conditions.items())),
        )


class StateStore:
    def __init__(self):
        self._states: dict[str, SessionState] = {}

    def get(self, session_id: str) -> SessionState:
        return self._states.setdefault(session_id, SessionState())

    def update(self, session_id: str, *, active_topics: list[str] | None = None, last_user_need: str | None = None, user_conditions: dict[str, str] | None = None) -> SessionState:
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
