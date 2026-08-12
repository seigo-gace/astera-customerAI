from __future__ import annotations

from dataclasses import dataclass

from .schemas import NeedTask, ResolutionMode, TaskResolution


@dataclass(frozen=True)
class IntegratedAnswerPlan:
    needs: tuple[NeedTask, ...]
    resolutions: tuple[TaskResolution, ...]
    blocked_task_ids: tuple[str, ...] = ()
    missing_evidence_task_ids: tuple[str, ...] = ()
    missing_user_inputs: tuple[str, ...] = ()
    safety_blocked: bool = False
    runtime_failure: bool = False


@dataclass(frozen=True)
class ComposedAnswer:
    mode: ResolutionMode
    answer: str | None
    resolved_task_ids: tuple[str, ...]
    unresolved_task_ids: tuple[str, ...]
    clarification_questions: tuple[str, ...] = ()


class FinalAnswerComposer:
    """Compose only validated task resolutions; never invent new domain facts."""

    def compose(self, plan: IntegratedAnswerPlan) -> ComposedAnswer:
        all_task_ids = tuple(need.task_id for need in plan.needs)
        if plan.runtime_failure:
            return ComposedAnswer(ResolutionMode.RUNTIME_FAILURE, None, (), all_task_ids)
        if plan.safety_blocked:
            return ComposedAnswer(ResolutionMode.SAFETY_BLOCKED, None, (), all_task_ids)

        by_task = {resolution.task_id: resolution for resolution in plan.resolutions}
        blocked = set(plan.blocked_task_ids) | set(plan.missing_evidence_task_ids)
        resolved: list[TaskResolution] = []
        unresolved: list[str] = []

        for need in plan.needs:
            item = by_task.get(need.task_id)
            if need.task_id in blocked or item is None or not item.resolved:
                unresolved.append(need.task_id)
            else:
                resolved.append(item)

        useful = "\n\n".join(item.public_text.strip() for item in resolved) or None
        resolved_ids = tuple(item.task_id for item in resolved)
        unresolved_ids = tuple(unresolved)

        if plan.missing_user_inputs:
            question = f"{plan.missing_user_inputs[0]}を確認してください。"
            return ComposedAnswer(
                ResolutionMode.NEEDS_USER_INPUT,
                useful,
                resolved_ids,
                unresolved_ids,
                (question,),
            )
        if unresolved:
            return ComposedAnswer(
                ResolutionMode.SAFE_PARTIAL,
                useful,
                resolved_ids,
                unresolved_ids,
            )
        return ComposedAnswer(
            ResolutionMode.RESOLVED,
            useful,
            resolved_ids,
            (),
        )


@dataclass(frozen=True)
class RuntimeSatisfactionSignals:
    all_major_needs_covered: bool
    evidence_complete: bool
    context_consistent: bool
    required_actionability_present: bool
    false_premise_corrected: bool
    unnecessary_clarification_count: int = 0
    unsupported_claim_count: int = 0
    stale_grounding_count: int = 0
    terminology_violation_count: int = 0


class RuntimeSatisfactionGate:
    """Structural gate only; natural-language satisfaction belongs to offline evaluation."""

    def evaluate(
        self,
        mode: ResolutionMode,
        signals: RuntimeSatisfactionSignals,
    ) -> tuple[bool, list[str]]:
        failures: list[str] = []
        if mode != ResolutionMode.RESOLVED:
            failures.append("conversation_not_resolved")
        if not signals.all_major_needs_covered:
            failures.append("major_need_missing")
        if not signals.evidence_complete:
            failures.append("evidence_incomplete")
        if not signals.context_consistent:
            failures.append("context_inconsistent")
        if not signals.required_actionability_present:
            failures.append("required_actionability_missing")
        if not signals.false_premise_corrected:
            failures.append("false_premise_uncorrected")
        if signals.unnecessary_clarification_count:
            failures.append("unnecessary_clarification")
        if signals.unsupported_claim_count:
            failures.append("unsupported_claim")
        if signals.stale_grounding_count:
            failures.append("stale_grounding")
        if signals.terminology_violation_count:
            failures.append("terminology_violation")
        return not failures, failures
