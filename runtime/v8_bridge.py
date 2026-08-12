from __future__ import annotations

from typing import Any, Protocol

from .schemas import NeedTask, RoleResult, SharedRolePacket


class V8Adapter(Protocol):
    async def analyze_need(self, message: str, context: dict[str, Any]) -> dict[str, Any]: ...
    async def compare_results(self, packet: SharedRolePacket, results: list[RoleResult]) -> dict[str, Any]: ...


class V8Bridge:
    def __init__(self, adapter: V8Adapter): self.adapter = adapter

    async def analyze_need(self, message: str, context: dict[str, Any]) -> tuple[str, str, list[NeedTask]]:
        raw = await self.adapter.analyze_need(message, context)
        tasks = [NeedTask.model_validate(item) for item in raw.get("tasks", [])]
        if not tasks: raise ValueError("v8_preflight_missing_tasks")
        return str(raw.get("normalized_need") or message).strip(), str(raw.get("audience") or "general").strip() or "general", tasks

    async def compare(self, packet: SharedRolePacket, results: list[RoleResult]) -> dict[str, Any]:
        raw = await self.adapter.compare_results(packet, results)
        if not isinstance(raw, dict): raise TypeError("v8_compare_invalid")
        return raw
