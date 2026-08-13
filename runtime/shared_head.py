from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence

from .contracts import CapabilityCapsule, TaskContract
from .hf_client import HFChatClient
from .schemas import RoleName, RoleResult, SharedRolePacket

_ROLE_RULES = {
    RoleName.CONSTRUCTIVE: (
        "Resolve every task. Return public_text, evidence_ids, conditions, exceptions "
        "and required action_steps. Never invent Astera facts."
    ),
    RoleName.ADVERSARIAL: (
        "Inspect the constructive draft. Find missing needs, contradictions, false premises, "
        "failure conditions and unclear wording. Do not rewrite the whole answer."
    ),
    RoleName.EVIDENCE_BOUND: (
        "Inspect the constructive draft claim-by-claim against supplied facts. Flag unsupported, "
        "stale, conditional or conflicting claims. Preserve evidence conditions and exceptions."
    ),
}
_VALIDATOR_CAPS = {
    RoleName.ADVERSARIAL: {"review", "quality", "technical", "clarity", "audience", "terminology"},
    RoleName.EVIDENCE_BOUND: {"evidence", "knowledge", "claims", "terminology", "review", "quality"},
}


class ThreeRoleModelPool:
    """One Work, three role runtimes: Constructive 4B, Adversarial 4B, Evidence-bound 8B."""

    def __init__(self, clients: Mapping[RoleName, HFChatClient]):
        missing = set(RoleName) - set(clients)
        if missing:
            raise ValueError(f"missing_role_clients:{sorted(role.value for role in missing)}")
        self.clients = dict(clients)

    @staticmethod
    def _compact_packet(packet: SharedRolePacket) -> dict[str, object]:
        return {
            "request_id": packet.request_id,
            "need": packet.normalized_need,
            "audience": packet.audience,
            "tasks": [
                {
                    "id": t.task_id,
                    "text": t.text,
                    "intent": t.intent,
                    "shape": t.response_shape,
                    "facts_required": bool(t.required_facts),
                    "actionable": t.actionability_required,
                }
                for t in packet.tasks
            ],
            "conditions": packet.user_conditions,
            "facts": [
                {
                    "id": f.fact_id,
                    "value": f.value,
                    "authority": f.authority,
                    "source_ids": f.source_ids or [f.source_id],
                    "conditions": f.conditions,
                    "exceptions": f.exceptions,
                    "relations": f.relations,
                    "knowledge_key": f.knowledge_key,
                    "domain": f.domain,
                    "topic": f.topic,
                }
                for f in packet.facts
            ],
            "repair_targets": packet.repair_targets,
        }

    @staticmethod
    def _draft_view(draft: RoleResult | None) -> dict[str, object] | None:
        if draft is None:
            return None
        return {
            "claims": draft.claims,
            "evidence_ids": draft.evidence_ids,
            "task_resolutions": [
                {
                    "task_id": r.task_id,
                    "public_text": r.public_text,
                    "evidence_ids": r.evidence_ids,
                    "conditions": r.conditions,
                    "exceptions": r.exceptions,
                    "action_steps": r.action_steps,
                    "unresolved_reason": r.unresolved_reason,
                }
                for r in draft.task_resolutions
            ],
        }

    @staticmethod
    def _skills_for_role(role: RoleName, skills: Sequence[CapabilityCapsule]) -> list[str]:
        if role == RoleName.CONSTRUCTIVE:
            return [s.text for s in skills]
        wanted = _VALIDATOR_CAPS[role]
        return [s.text for s in skills if wanted.intersection(s.capabilities)]

    async def _run(
        self,
        role: RoleName,
        packet: SharedRolePacket,
        skills: Sequence[CapabilityCapsule],
        *,
        draft: RoleResult | None = None,
    ) -> RoleResult:
        prompt = {
            "role": role.value,
            "rule": _ROLE_RULES[role],
            "packet": self._compact_packet(packet),
            "constructive_draft": self._draft_view(draft),
            "capabilities": self._skills_for_role(role, skills),
            "output": (
                "RoleResult JSON: role, claims, evidence_ids, risks, uncertainties, "
                "missing_needs, contradictions, task_resolutions, completion_state"
            ),
        }
        result = await self.clients[role].chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You are one isolated role inside Astera Customer AI. Return JSON only. "
                        "Do not expose chain-of-thought. Do not claim external actions were executed."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(prompt, ensure_ascii=False, separators=(",", ":")),
                },
            ]
        )
        result["role"] = role.value
        parsed = RoleResult.model_validate(result)
        if parsed.role != role:
            raise ValueError("role_mismatch")
        return parsed

    async def run_all(
        self,
        packet: SharedRolePacket,
        skills: Sequence[CapabilityCapsule],
    ) -> list[RoleResult]:
        constructive = await self._run(RoleName.CONSTRUCTIVE, packet, skills)
        adversarial, evidence = await asyncio.gather(
            self._run(RoleName.ADVERSARIAL, packet, skills, draft=constructive),
            self._run(RoleName.EVIDENCE_BOUND, packet, skills, draft=constructive),
        )
        return [constructive, adversarial, evidence]

    async def validate_draft(
        self,
        packet: SharedRolePacket,
        skills: Sequence[CapabilityCapsule],
        draft: RoleResult,
    ) -> list[RoleResult]:
        adversarial, evidence = await asyncio.gather(
            self._run(RoleName.ADVERSARIAL, packet, skills, draft=draft),
            self._run(RoleName.EVIDENCE_BOUND, packet, skills, draft=draft),
        )
        return [adversarial, evidence]

    async def retry_role(
        self,
        role: RoleName,
        packet: SharedRolePacket,
        skills: Sequence[CapabilityCapsule],
    ) -> RoleResult:
        if role != RoleName.CONSTRUCTIVE:
            raise ValueError("targeted_repair_constructive_only")
        return await self._run(role, packet, skills)

    async def semantic_decompose(self, message: str, seed: TaskContract) -> TaskContract:
        prompt = {
            "request": message,
            "seed": seed.model_dump(mode="json"),
            "required_fields": [
                "purpose",
                "target",
                "conditions",
                "constraints",
                "premises",
                "missing_information",
                "success_criteria",
                "need_tasks",
                "completion_conditions",
            ],
            "rule": (
                "Refine only the task structure. Keep every user need and constraint. "
                "Do not invent product facts. need_tasks must use t1,t2... and valid response_shape values."
            ),
        }
        obj = await self.clients[RoleName.CONSTRUCTIVE].chat_json(
            [
                {"role": "system", "content": "Return only a compact TaskContract JSON object."},
                {
                    "role": "user",
                    "content": json.dumps(prompt, ensure_ascii=False, separators=(",", ":")),
                },
            ],
            max_tokens=1200,
        )
        contract = TaskContract.model_validate(obj)
        if not contract.need_tasks:
            raise ValueError("semantic_decomposition_empty")
        return contract
