from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Protocol

import httpx

from .roles import role_rules
from .schemas import RoleName, RoleResult, SharedRolePacket


class ModelBackend(Protocol):
    async def generate_role(self, role: RoleName, packet: SharedRolePacket) -> RoleResult: ...


@dataclass(slots=True)
class ResidentRoleWorker:
    role: RoleName
    backend: ModelBackend

    async def run(self, packet: SharedRolePacket) -> RoleResult:
        result = await self.backend.generate_role(self.role, packet)
        if result.role != self.role:
            raise ValueError(f"role mismatch: expected={self.role} actual={result.role}")
        return result


class ResidentRolePool:
    def __init__(self, backend_factory):
        self._workers = {role: ResidentRoleWorker(role=role, backend=backend_factory(role)) for role in RoleName}

    async def run_all(self, packet: SharedRolePacket) -> list[RoleResult]:
        tasks = [asyncio.create_task(self._workers[role].run(packet)) for role in RoleName]
        return await asyncio.gather(*tasks)

    async def retry_role(self, role: RoleName, packet: SharedRolePacket) -> RoleResult:
        return await self._workers[role].run(packet)


class HuggingFaceRoleBackend:
    def __init__(self, *, role: RoleName, model_id: str, token: str, api_url: str = "https://router.huggingface.co/v1/chat/completions", timeout_seconds: float = 30.0):
        if not model_id.strip(): raise ValueError("model_id_required")
        if not token.strip(): raise ValueError("hf_token_required")
        self.role, self.model_id, self.token, self.api_url, self.timeout_seconds = role, model_id.strip(), token.strip(), api_url, timeout_seconds

    async def generate_role(self, role: RoleName, packet: SharedRolePacket) -> RoleResult:
        if role != self.role: raise ValueError("backend_role_mismatch")
        prompt = {
            "role": role.value,
            "rules": role_rules(role),
            "packet": packet.model_dump(mode="json"),
            "required_output_schema": RoleResult.model_json_schema(),
            "constraints": ["Use only supplied packet facts for Astera-specific claims.", "Return JSON only.", "Do not claim external actions were executed."],
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(self.api_url, headers={"authorization": f"Bearer {self.token}"}, json={"model": self.model_id, "messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}], "response_format": {"type": "json_object"}, "stream": False})
        response.raise_for_status()
        payload = response.json(); choices = payload.get("choices") or []
        if not choices: raise RuntimeError("model_empty_choices")
        content = choices[0].get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip(): raise RuntimeError("model_empty_content")
        return RoleResult.model_validate_json(content)
