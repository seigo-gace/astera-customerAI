from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .satisfaction import SatisfactionScore
from .scenarios import EvaluationScenario
from .scorer import ScenarioScore

ResolutionModeValue = Literal[
    "resolved",
    "needs_user_input",
    "safe_partial",
    "blocked_current_fact",
    "safety_blocked",
    "runtime_failure",
]


class RuntimeTurnEvidence(BaseModel):
    user_text: str = Field(min_length=1)
    answer: str | None = None
    answered_task_ids: list[str] = Field(default_factory=list)
    unresolved_task_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    resolution_mode: ResolutionModeValue
    passed: bool
    clarification_questions: list[str] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)
    latency_ms: float = Field(ge=0.0)
    retry_count: int = Field(ge=0)


class ScenarioEvaluationEvidence(BaseModel):
    """Auditable evidence from actual runtime turns plus an independent assessment."""

    scenario_id: str
    runtime_revision: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    corpus_revision: str = Field(min_length=1)
    turns: list[RuntimeTurnEvidence] = Field(min_length=1)
    observed_need_ids: list[str] = Field(default_factory=list)
    grounded_fact_ids: list[str] = Field(default_factory=list)
    satisfaction: SatisfactionScore
    false_premise_corrected: bool = True
    need_carryover_ok: bool = True
    non_regression_ok: bool = True
    delta_retrieval_ok: bool = True
    final_closure: bool = True

    @model_validator(mode="after")
    def validate_provenance(self) -> "ScenarioEvaluationEvidence":
        if self.satisfaction.production_model_ref.strip() != self.model_revision.strip():
            raise ValueError("satisfaction_production_model_revision_mismatch")
        return self


def _count_violation(turns: Iterable[RuntimeTurnEvidence], names: set[str]) -> int:
    return sum(1 for turn in turns if names.intersection(turn.violations))


def score_evidence(
    scenario: EvaluationScenario,
    evidence: ScenarioEvaluationEvidence,
) -> ScenarioScore:
    """Derive release score from scenario authority + actual runtime evidence.

    Scenario class/criticality/user turns come from the approved scenario source,
    never from the judge payload. Need/fact coverage are also recomputed here so
    the judge cannot mark them true when required evidence is absent.
    """

    if scenario.scenario_id != evidence.scenario_id:
        raise ValueError("scenario_evidence_id_mismatch")

    actual_user_turns = [turn.user_text for turn in evidence.turns]
    if actual_user_turns != scenario.user_turns:
        raise ValueError("scenario_user_turns_do_not_match_runtime_evidence")

    required_needs = {item for item in scenario.expected_need_labels if item}
    observed_needs = {item for item in evidence.observed_need_ids if item}
    need_coverage_ok = required_needs.issubset(observed_needs)

    required_facts = {item for item in scenario.required_fact_ids if item}
    grounded_facts = {item for item in evidence.grounded_fact_ids if item}
    fact_coverage_ok = required_facts.issubset(grounded_facts)

    satisfaction = evidence.satisfaction.model_copy(
        update={
            "all_major_needs_covered": need_coverage_ok,
            "evidence_complete": fact_coverage_ok,
        }
    )

    final = evidence.turns[-1]
    resolved = bool(
        final.passed
        and final.resolution_mode == "resolved"
        and not final.unresolved_task_ids
        and final.answer
    )

    unsupported_claims = _count_violation(evidence.turns, {"unsupported_claim"})
    legacy_mixing = _count_violation(evidence.turns, {"legacy_mixing"})
    secret_leaks = _count_violation(evidence.turns, {"secret_leak", "forbidden_literal_exposed"})
    unexecuted_completion_claims = _count_violation(
        evidence.turns,
        {"unexecuted_completion_claim"},
    )

    return ScenarioScore(
        scenario_id=scenario.scenario_id,
        scenario_class=scenario.scenario_class,
        runtime_revision=evidence.runtime_revision,
        model_revision=evidence.model_revision,
        corpus_revision=evidence.corpus_revision,
        critical=scenario.critical,
        multi_turn=scenario.multi_turn,
        false_premise=scenario.false_premise,
        resolved=resolved,
        satisfaction=satisfaction,
        false_premise_corrected=(
            evidence.false_premise_corrected if scenario.false_premise else True
        ),
        unsupported_claims=unsupported_claims,
        legacy_mixing=legacy_mixing,
        secret_leaks=secret_leaks,
        unexecuted_completion_claims=unexecuted_completion_claims,
        followup_depth=scenario.followup_depth,
        need_carryover_ok=(
            evidence.need_carryover_ok if scenario.requires_need_carryover else True
        ),
        non_regression_ok=(
            evidence.non_regression_ok if scenario.requires_non_regression else True
        ),
        delta_retrieval_ok=(
            evidence.delta_retrieval_ok if scenario.requires_delta_retrieval else True
        ),
        final_closure=(
            evidence.final_closure if scenario.requires_final_closure else True
        ),
    )
