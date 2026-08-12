from __future__ import annotations

from typing import Any, Protocol

from .schemas import RoleResult, SharedRolePacket


class KagrraAdapter(Protocol):
    async def preprocess(self, message: str, context: dict[str, Any]) -> dict[str, Any]: ...
    async def audit(self, packet: SharedRolePacket, results: list[RoleResult], integrated: dict[str, Any]) -> dict[str, Any]: ...


class KagrraBridge:
    def __init__(self, adapter: KagrraAdapter): self.adapter = adapter

    async def preprocess(self, message: str, context: dict[str, Any]) -> dict[str, Any]:
        raw = await self.adapter.preprocess(message, context)
        if not isinstance(raw, dict): raise TypeError("kagrra_preprocess_invalid")
        return raw

    async def audit(self, packet: SharedRolePacket, results: list[RoleResult], integrated: dict[str, Any]) -> dict[str, Any]:
        raw = await self.adapter.audit(packet, results, integrated)
        if not isinstance(raw, dict): raise TypeError("kagrra_audit_invalid")
        return raw
