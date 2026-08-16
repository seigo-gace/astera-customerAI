from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .satisfaction import wilson_lower_bound
from .scorer import ScenarioScore


@dataclass(frozen=True)
class ReleaseGateConfig:
    user_need_resolution_min: float = 0.98
    answer_satisfaction_min: float = 0.98
    satisfaction_confidence_lower_bound_min: float = 0.98
    min_unseen_scenarios: int = 200
    min_scenario_classes: int = 11
    min_each_class: int = 10
    min_critical: int = 30
    min_multiturn: int = 20
    min_false_premise: int = 20
    critical_resolution_min: float = 0.99
    false_premise_correction_min: float = 1.0


@dataclass(frozen=True)
class ReleaseDecision:
    passed: bool
    failures: tuple[str, ...]
    resolution_rate: float
    satisfaction_rate: float
    satisfaction_lower_bound: float


def _behavioral_contract_ok(score: ScenarioScore) -> bool:
    return (
        score.false_premise_corrected
        and score.need_carryover_ok
        and score.non_regression_ok
        and score.delta_retrieval_ok
        and score.final_closure
        and score.unsupported_claims == 0
        and score.legacy_mixing == 0
        and score.secret_leaks == 0
        and score.unexecuted_completion_claims == 0
    )


def _qualified_resolution(score: ScenarioScore) -> bool:
    """Resolution is measured independently from answer satisfaction."""

    return score.resolved and _behavioral_contract_ok(score)


def _qualified_satisfaction(score: ScenarioScore) -> bool:
    """Satisfaction is measured independently from resolution."""

    return score.satisfied and _behavioral_contract_ok(score)


def evaluate_release(
    scores: list[ScenarioScore],
    config: ReleaseGateConfig = ReleaseGateConfig(),
) -> ReleaseDecision:
    total = len(scores)
    failures: list[str] = []

    if total < config.min_unseen_scenarios:
        failures.append("insufficient_unseen_scenarios")

    classes = Counter(item.scenario_class for item in scores)
    if len(classes) < config.min_scenario_classes:
        failures.append("insufficient_scenario_classes")
    if classes and min(classes.values()) < config.min_each_class:
        failures.append("insufficient_each_class")

    critical = [item for item in scores if item.critical]
    multiturn = [item for item in scores if item.multi_turn]
    false_premise = [item for item in scores if item.false_premise]

    if len(critical) < config.min_critical:
        failures.append("insufficient_critical")
    if len(multiturn) < config.min_multiturn:
        failures.append("insufficient_multiturn")
    if len(false_premise) < config.min_false_premise:
        failures.append("insufficient_false_premise")

    resolved_count = sum(_qualified_resolution(item) for item in scores)
    satisfied_count = sum(_qualified_satisfaction(item) for item in scores)
    resolution_rate = resolved_count / total if total else 0.0
    satisfaction_rate = satisfied_count / total if total else 0.0
    satisfaction_lower_bound = wilson_lower_bound(satisfied_count, total)

    if resolution_rate < config.user_need_resolution_min:
        failures.append("resolution_rate_below_gate")
    if satisfaction_rate < config.answer_satisfaction_min:
        failures.append("satisfaction_rate_below_gate")
    if satisfaction_lower_bound < config.satisfaction_confidence_lower_bound_min:
        failures.append("satisfaction_confidence_below_gate")

    if critical:
        critical_rate = sum(_qualified_resolution(item) for item in critical) / len(critical)
        if critical_rate < config.critical_resolution_min:
            failures.append("critical_resolution_below_gate")

    if false_premise:
        correction_rate = sum(item.false_premise_corrected for item in false_premise) / len(false_premise)
        if correction_rate < config.false_premise_correction_min:
            failures.append("false_premise_correction_below_gate")

    if any(
        item.unsupported_claims
        or item.legacy_mixing
        or item.secret_leaks
        or item.unexecuted_completion_claims
        for item in scores
    ):
        failures.append("zero_tolerance_violation")

    return ReleaseDecision(
        passed=not failures,
        failures=tuple(dict.fromkeys(failures)),
        resolution_rate=resolution_rate,
        satisfaction_rate=satisfaction_rate,
        satisfaction_lower_bound=satisfaction_lower_bound,
    )
