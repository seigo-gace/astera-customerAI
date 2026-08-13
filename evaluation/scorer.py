from __future__ import annotations

from pydantic import BaseModel


class ScenarioScore(BaseModel):
    scenario_id: str
    scenario_class: str
    critical: bool = False
    multi_turn: bool = False
    false_premise: bool = False
    resolved: bool
    satisfied: bool
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
    def pass_all(self) -> bool:
        return (
            self.resolved
            and self.satisfied
            and self.false_premise_corrected
            and self.need_carryover_ok
            and self.non_regression_ok
            and self.delta_retrieval_ok
            and self.final_closure
            and self.unsupported_claims == 0
            and self.legacy_mixing == 0
            and self.secret_leaks == 0
            and self.unexecuted_completion_claims == 0
        )
