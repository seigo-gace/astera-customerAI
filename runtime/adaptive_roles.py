from __future__ import annotations

from collections.abc import Sequence

from .contracts import CapabilityCapsule, TaskContract
from .schemas import RoleName, RoleResult, SharedRolePacket, TaskResolution


_COMPLEX_MARKERS = (
    "なぜ", "どうして", "理由", "比較", "違い", "どちら", "どっち", "おすすめ", "推奨",
    "分析", "評価", "検討", "改善", "メリット", "デメリット", "リスク", "べき",
    "方法", "手順", "やり方", "設定", "登録", "作成", "実装", "エラー", "不具合",
    "失敗", "直ら", "できない", "困", "why", "compare", "recommend", "analy",
    "evaluate", "how to", "error", "troubleshoot",
)


class AdaptiveRoleRouter:
    """Preserve the existing three-role runtime while bypassing models for safe direct KB facts.

    A fast-path response is allowed only for one direct, non-actionable task with grounded facts
    and no complexity marker. Every other request delegates unchanged to the existing model pool.
    The returned fast-path objects still pass through the existing integrator, quality gate,
    state handling, public boundary, and final response composer in CustomerAIInternalCore.
    """

    def __init__(self, delegate):
        self.delegate = delegate

    @staticmethod
    def _eligible(packet: SharedRolePacket) -> bool:
        if packet.repair_targets or len(packet.tasks) != 1 or not packet.facts:
            return False
        task = packet.tasks[0]
        if task.response_shape != "direct" or task.intent != "general" or task.actionability_required:
            return False
        text = packet.normalized_need.casefold()
        if len(text) > 180 or any(marker in text for marker in _COMPLEX_MARKERS):
            return False
        return True

    @staticmethod
    def _public_text(packet: SharedRolePacket) -> str:
        values: list[str] = []
        conditions: list[str] = []
        exceptions: list[str] = []
        for fact in packet.facts:
            value = fact.value.strip()
            if value and value not in values:
                values.append(value)
            for item in fact.conditions:
                item = item.strip()
                if item and item not in conditions:
                    conditions.append(item)
            for item in fact.exceptions:
                item = item.strip()
                if item and item not in exceptions:
                    exceptions.append(item)
        if not values:
            return ""
        if len(values) == 1:
            answer = values[0]
        else:
            answer = "\n".join(f"- {value}" for value in values)
        if conditions:
            answer += "\n条件: " + " / ".join(conditions)
        if exceptions:
            answer += "\n例外: " + " / ".join(exceptions)
        return answer

    @classmethod
    def _fast_results(cls, packet: SharedRolePacket) -> list[RoleResult]:
        task = packet.tasks[0]
        evidence_ids = list(dict.fromkeys(fact.fact_id for fact in packet.facts))
        public_text = cls._public_text(packet)
        if not public_text:
            raise RuntimeError("fast_path_empty_public_text")
        constructive = RoleResult(
            role=RoleName.CONSTRUCTIVE,
            claims=[public_text],
            evidence_ids=evidence_ids,
            task_resolutions=[
                TaskResolution(
                    task_id=task.task_id,
                    public_text=public_text,
                    evidence_ids=evidence_ids,
                )
            ],
            completion_state="complete",
        )
        adversarial = RoleResult(
            role=RoleName.ADVERSARIAL,
            evidence_ids=evidence_ids,
            completion_state="complete",
        )
        evidence = RoleResult(
            role=RoleName.EVIDENCE_BOUND,
            evidence_ids=evidence_ids,
            completion_state="complete",
        )
        return [constructive, adversarial, evidence]

    async def run_all(
        self,
        packet: SharedRolePacket,
        skills: Sequence[CapabilityCapsule],
    ) -> list[RoleResult]:
        if self._eligible(packet):
            return self._fast_results(packet)
        return await self.delegate.run_all(packet, skills)

    async def validate_draft(
        self,
        packet: SharedRolePacket,
        skills: Sequence[CapabilityCapsule],
        draft: RoleResult,
    ) -> list[RoleResult]:
        return await self.delegate.validate_draft(packet, skills, draft)

    async def retry_role(
        self,
        role: RoleName,
        packet: SharedRolePacket,
        skills: Sequence[CapabilityCapsule],
    ) -> RoleResult:
        return await self.delegate.retry_role(role, packet, skills)

    async def semantic_decompose(self, message: str, seed: TaskContract) -> TaskContract:
        return await self.delegate.semantic_decompose(message, seed)
