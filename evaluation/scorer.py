from __future__ import annotations

from pydantic import BaseModel, Field

from .satisfaction import SatisfactionScore


class ScenarioScore(BaseModel):
    scenario_id: str
    scenario_class: str
    runtime_revision: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    corpus_revision: str = Field(min_length=1)
    critical: bool = False
    multi_turn: bool = False
    false_premise: bool = False
    resolved: bool
    satisfaction: SatisfactionScore
    false_premise_corrected: bool = True
    unsupported_claims: int = 0
    legacy_mixing: int = 0
    secret_leaks: int = 0
    unexecuted_completion_claims: int = 0
    followup_depth: int = 0
    need_carryover_ok: bool = True
    non_regression_ok: bool = True
    delta_retrieval_ok: bool = True
    final_closure: bool = True

    @property
    def behavioral_contract_ok(self) -> bool:
        return (
            self.false_premise_corrected
            and self.need_carryover_ok
            and self.non_regression_ok
            and self.delta_retrieval_ok
            and self.final_closure
            and self.unsupported_claims == 0
            and self.legacy_mixing == 0
            and self.secret_leaks == 0
            and self.unexecuted_completion_claims == 0
        )

    @property
    def resolution_pass(self) -> bool:
        """Diagnostic resolution signal. It is not the primary completion KPI."""
        return self.resolved and self.behavioral_contract_ok

    @property
    def satisfaction_pass(self) -> bool:
        """Primary completion signal for one evidence-bound user scenario."""
        return self.resolved and self.satisfaction.passed and self.behavioral_contract_ok

    @property
    def pass_all(self) -> bool:
        return self.satisfaction_pass
