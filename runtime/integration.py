from __future__ import annotations

from dataclasses import dataclass

from .schemas import RoleName, RoleResult, TaskResolution


@dataclass(frozen=True)
class IntegratedRoleResult:
    resolutions: tuple[TaskResolution, ...]
    missing_task_ids: tuple[str, ...]
    contradiction_task_ids: tuple[str, ...]
    risks: tuple[str, ...]
    uncertainties: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {"resolutions":[r.model_dump(mode="json") for r in self.resolutions],"missing_task_ids":list(self.missing_task_ids),"contradiction_task_ids":list(self.contradiction_task_ids),"risks":list(self.risks),"uncertainties":list(self.uncertainties),"evidence_ids":list(self.evidence_ids)}


class DialogueIntegrator:
    def integrate(self, results: list[RoleResult]) -> IntegratedRoleResult:
        by_role = {result.role: result for result in results}
        if set(by_role) != set(RoleName):
            raise ValueError("three_role_result_required")
        constructive = by_role[RoleName.CONSTRUCTIVE]
        missing = set().union(*(r.missing_needs for r in results))
        contradictions = set().union(*(r.contradictions for r in results))
        return IntegratedRoleResult(
            resolutions=tuple(constructive.task_resolutions),
            missing_task_ids=tuple(sorted(missing)),
            contradiction_task_ids=tuple(sorted(contradictions)),
            risks=tuple(dict.fromkeys(item for r in results for item in r.risks)),
            uncertainties=tuple(dict.fromkeys(item for r in results for item in r.uncertainties)),
            evidence_ids=tuple(dict.fromkeys(item for r in results for item in r.evidence_ids)),
        )
