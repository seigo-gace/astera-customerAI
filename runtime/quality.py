from __future__ import annotations

from dataclasses import dataclass

from .integration import IntegratedRoleResult
from .schemas import SharedRolePacket


@dataclass(frozen=True)
class CompletionCheck:
    passed: bool
    resolution_score: float
    violations: tuple[str, ...]
    missing_evidence_task_ids: tuple[str, ...]


class CompletionGate:
    def evaluate(self, packet: SharedRolePacket, integrated: IntegratedRoleResult, *, external_violations=()) -> CompletionCheck:
        violations = list(external_violations)
        by_task = {item.task_id: item for item in integrated.resolutions}
        missing_evidence: list[str] = []
        resolved = 0
        for task in packet.tasks:
            resolution = by_task.get(task.task_id)
            if resolution is None or not resolution.resolved:
                violations.append("major_need_missing"); continue
            if task.required_facts and not resolution.evidence_ids:
                missing_evidence.append(task.task_id); violations.append("evidence_incomplete"); continue
            if task.actionability_required and not resolution.action_steps:
                violations.append("required_actionability_missing"); continue
            resolved += 1
        if integrated.missing_task_ids: violations.append("major_need_missing")
        if integrated.contradiction_task_ids: violations.append("contradiction")
        total = max(1, len(packet.tasks))
        deduped = tuple(dict.fromkeys(violations))
        return CompletionCheck(not deduped and resolved == len(packet.tasks), resolved / total, deduped, tuple(sorted(set(missing_evidence))))
