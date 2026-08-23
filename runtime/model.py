from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Protocol

import httpx

from .hf_client import (
    HF_CHAT_API_ADVERSARIAL,
    HF_CHAT_API_CONSTRUCTIVE,
    HF_CHAT_API_EVIDENCE,
    HF_MODEL_4B,
    HF_MODEL_8B,
)
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


_LOCAL_ROLE_CONFIG = {
    RoleName.CONSTRUCTIVE: (HF_MODEL_4B, HF_CHAT_API_CONSTRUCTIVE),
    RoleName.ADVERSARIAL: (HF_MODEL_4B, HF_CHAT_API_ADVERSARIAL),
    RoleName.EVIDENCE_BOUND: (HF_MODEL_8B, HF_CHAT_API_EVIDENCE),
}


class HuggingFaceRoleBackend:
    """Compatibility backend name retained; inference itself is local-only.

    No Hugging Face Inference Provider endpoint is accepted. The HF token is
    intentionally ignored for inference and exists only for old constructor compatibility.
    """

    def __init__(
        self,
        *,
        role: RoleName,
        model_id: str | None = None,
        token: str = "",
        api_url: str | None = None,
        timeout_seconds: float = 600.0,
    ):
        expected_model, expected_url = _LOCAL_ROLE_CONFIG[role]
        chosen_model = (model_id or expected_model).strip()
        chosen_url = (api_url or expected_url).strip()
        if chosen_model != expected_model:
            raise ValueError(f"model_drift:{role.value}:{chosen_model}")
        if chosen_url != expected_url:
            raise ValueError(f"remote_or_wrong_endpoint_forbidden:{role.value}:{chosen_url}")
        self.role = role
        self.model_id = chosen_model
        self.api_url = chosen_url
        self.timeout_seconds = timeout_seconds

    async def generate_role(self, role: RoleName, packet: SharedRolePacket) -> RoleResult:
        if role != self.role:
            raise ValueError("backend_role_mismatch")
        prompt = {
            "role": role.value,
            "rules": role_rules(role),
            "packet": packet.model_dump(mode="json"),
            "required_output_schema": RoleResult.model_json_schema(),
            "constraints": [
                "Use only supplied packet facts for Astera-specific claims.",
                "Return JSON only.",
                "Do not claim external actions were executed.",
            ],
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                self.api_url,
                json={
                    "model": self.model_id,
                    "messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
                    "response_format": {"type": "json_object"},
                    "stream": False,
                },
            )
        response.raise_for_status()
        payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError("model_empty_choices")
        content = choices[0].get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("model_empty_content")
        text = content.strip()
        first = text.find("{")
        last = text.rfind("}")
        if first >= 0 and last >= first:
            text = text[first : last + 1]
        return RoleResult.model_validate_json(text)
