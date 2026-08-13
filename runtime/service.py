from __future__ import annotations

from .schemas import FinalResponse


class CustomerAIWork:
    """Public service boundary. Runtime ownership lives in CustomerAIInternalCore."""

    def __init__(self, core):
        self.core = core

    async def run(self, session_id: str, message: str) -> FinalResponse:
        return await self.core.run(session_id, message)

    def delete_session(self, session_id: str) -> bool:
        return self.core.state.delete(session_id)
