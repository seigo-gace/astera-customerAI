from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, model_validator


EvaluatorSource = Literal["rules", "human", "external_judge"]


class SatisfactionScore(BaseModel):
    """Structured evidence that a user-facing answer was actually satisfactory.

    A single boolean is intentionally insufficient. Every required dimension must
    be evidenced, and a production model may not judge its own answer.
    """

    evaluator_source: EvaluatorSource
    evaluator_ref: str = Field(min_length=1)
    production_model_ref: str = Field(min_length=1)

    purpose_fulfilled: bool
    preflight_correct: bool
    intent_correct: bool
    all_major_needs_covered: bool
    required_depth_met: bool
    factual: bool
    evidence_complete: bool
    constraints_respected: bool
    conditions_exceptions_covered: bool
    current_status_covered_when_required: bool
    next_action_covered_when_required: bool
    relevant: bool
    direct: bool
    clear: bool
    appropriately_concise: bool
    actionable_when_required: bool
    context_consistent: bool
    clarification_efficient: bool
    resolution_mode_correct: bool
    self_contained: bool
    turns_to_resolution: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_evaluator_independence(self) -> "SatisfactionScore":
        if (
            self.evaluator_source == "external_judge"
            and self.evaluator_ref.strip() == self.production_model_ref.strip()
        ):
            raise ValueError("external_judge_must_be_independent_from_production_model")
        return self

    @property
    def passed(self) -> bool:
        return all(
            (
                self.purpose_fulfilled,
                self.preflight_correct,
                self.intent_correct,
                self.all_major_needs_covered,
                self.required_depth_met,
                self.factual,
                self.evidence_complete,
                self.constraints_respected,
                self.conditions_exceptions_covered,
                self.current_status_covered_when_required,
                self.next_action_covered_when_required,
                self.relevant,
                self.direct,
                self.clear,
                self.appropriately_concise,
                self.actionable_when_required,
                self.context_consistent,
                self.clarification_efficient,
                self.resolution_mode_correct,
                self.self_contained,
            )
        )


def wilson_lower_bound(successes: int, total: int, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    p = successes / total
    z2 = z * z
    denominator = 1 + z2 / total
    centre = p + z2 / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z2 / (4 * total)) / total)
    return (centre - margin) / denominator
