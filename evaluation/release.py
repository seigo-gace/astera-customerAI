from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .satisfaction import wilson_lower_bound
from .scorer import ScenarioScore


@dataclass(frozen=True)
class ReleaseGateConfig:
    answer_satisfaction_min: float = 0.98
    satisfaction_confidence_lower_bound_min: float = 0.98
    min_unseen_scenarios: int = 200
    min_scenario_classes: int = 11
    min_each_class: int = 10
    min_critical: int = 30
    min_multiturn: int = 20
    min_false_premise: int = 20
    critical_satisfaction_min: float = 0.99
    false_premise_correction_min: float = 1.0


@dataclass(frozen=True)
class ReleaseDecision:
    passed: bool
    failures: tuple[str, ...]
    primary_metric: str
    runtime_revision: str | None
    model_revision: str | None
    corpus_revision: str | None
    resolution_rate: float
    satisfaction_rate: float
    satisfaction_lower_bound: float
    critical_satisfaction_rate: float


def _single_revision(scores: list[ScenarioScore], attr: str, failure: str, failures: list[str]) -> str | None:
    values = {getattr(score, attr) for score in scores if getattr(score, attr)}
    if len(values) != 1:
        failures.append(failure)
        return None
    return next(iter(values))


def evaluate_release(
    scores: list[ScenarioScore],
    config: ReleaseGateConfig = ReleaseGateConfig(),
) -> ReleaseDecision:
    total = len(scores)
    failures: list[str] = []

    runtime_revision = _single_revision(scores, "runtime_revision", "mixed_runtime_revision", failures)
    model_revision = _single_revision(scores, "model_revision", "mixed_model_revision", failures)
    corpus_revision = _single_revision(scores, "corpus_revision", "mixed_corpus_revision", failures)

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

    resolved_count = sum(item.resolution_pass for item in scores)
    satisfied_count = sum(item.satisfaction_pass for item in scores)
    resolution_rate = resolved_count / total if total else 0.0
    satisfaction_rate = satisfied_count / total if total else 0.0
    satisfaction_lower_bound = wilson_lower_bound(satisfied_count, total)

    if satisfaction_rate < config.answer_satisfaction_min:
        failures.append("answer_satisfaction_below_98")
    if satisfaction_lower_bound < config.satisfaction_confidence_lower_bound_min:
        failures.append("satisfaction_confidence_below_98")

    critical_satisfaction_rate = (
        sum(item.satisfaction_pass for item in critical) / len(critical) if critical else 0.0
    )
    if critical and critical_satisfaction_rate < config.critical_satisfaction_min:
        failures.append("critical_satisfaction_below_99")

    if false_premise:
        correction_rate = sum(item.false_premise_corrected for item in false_premise) / len(false_premise)
        if correction_rate < config.false_premise_correction_min:
            failures.append("false_premise_correction_below_100")

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
        primary_metric="answer_satisfaction",
        runtime_revision=runtime_revision,
        model_revision=model_revision,
        corpus_revision=corpus_revision,
        resolution_rate=resolution_rate,
        satisfaction_rate=satisfaction_rate,
        satisfaction_lower_bound=satisfaction_lower_bound,
        critical_satisfaction_rate=critical_satisfaction_rate,
    )
