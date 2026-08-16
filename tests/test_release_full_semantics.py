from evaluation.release import evaluate_release
from evaluation.satisfaction import SatisfactionScore
from evaluation.scorer import ScenarioScore


RUNTIME_REV = "runtime@abc"
MODEL_REV = "model@def"
CORPUS_REV = "corpus@ghi"
SCENARIO_CLASSES = (
    "direct",
    "paraphrase",
    "compound",
    "multi_turn",
    "false_premise",
    "comparison",
    "condition_change",
    "procedure",
    "troubleshooting",
    "audience_adaptation",
    "negative_unsupported",
)


def _satisfaction(**overrides) -> SatisfactionScore:
    values = {
        "evaluator_source": "external_judge",
        "evaluator_ref": "judge:independent@rev-1",
        "production_model_ref": MODEL_REV,
        "purpose_fulfilled": True,
        "preflight_correct": True,
        "intent_correct": True,
        "all_major_needs_covered": True,
        "required_depth_met": True,
        "factual": True,
        "evidence_complete": True,
        "constraints_respected": True,
        "conditions_exceptions_covered": True,
        "current_status_covered_when_required": True,
        "next_action_covered_when_required": True,
        "relevant": True,
        "direct": True,
        "clear": True,
        "appropriately_concise": True,
        "actionable_when_required": True,
        "context_consistent": True,
        "clarification_efficient": True,
        "resolution_mode_correct": True,
        "self_contained": True,
        "turns_to_resolution": 1,
    }
    values.update(overrides)
    return SatisfactionScore(**values)


def _passing_scores() -> list[ScenarioScore]:
    scores: list[ScenarioScore] = []
    for scenario_class in SCENARIO_CLASSES:
        for index in range(20):
            scores.append(
                ScenarioScore(
                    scenario_id=f"{scenario_class}-{index:02d}",
                    scenario_class=scenario_class,
                    runtime_revision=RUNTIME_REV,
                    model_revision=MODEL_REV,
                    corpus_revision=CORPUS_REV,
                    critical=scenario_class in {"false_premise", "negative_unsupported"},
                    multi_turn=scenario_class == "multi_turn",
                    false_premise=scenario_class == "false_premise",
                    resolved=True,
                    satisfaction=_satisfaction(),
                )
            )
    return scores


def test_complete_220_scenario_evidence_can_pass():
    decision = evaluate_release(_passing_scores())
    assert decision.passed is True
    assert decision.failures == ()
    assert decision.primary_metric == "answer_satisfaction"


def test_any_user_value_dimension_failure_is_not_counted_as_satisfied():
    scores = _passing_scores()
    scores[0].satisfaction = _satisfaction(required_depth_met=False)
    decision = evaluate_release(scores)
    assert decision.satisfaction_rate < 1.0
    assert "satisfaction_confidence_below_98" in decision.failures


def test_multiturn_carryover_failure_is_not_counted_as_satisfied():
    scores = _passing_scores()
    target = next(item for item in scores if item.scenario_class == "multi_turn")
    target.need_carryover_ok = False
    decision = evaluate_release(scores)
    assert decision.passed is False
    assert decision.satisfaction_rate < 1.0
    assert "satisfaction_confidence_below_98" in decision.failures


def test_critical_contract_failure_reduces_critical_satisfaction():
    scores = _passing_scores()
    target = next(item for item in scores if item.critical)
    target.final_closure = False
    decision = evaluate_release(scores)
    assert decision.passed is False
    assert "critical_satisfaction_below_99" in decision.failures


def test_resolution_is_diagnostic_but_is_also_required_for_satisfaction():
    scores = _passing_scores()
    scores[0].resolved = False
    decision = evaluate_release(scores)
    assert decision.resolution_rate < 1.0
    assert decision.satisfaction_rate < 1.0
    assert "resolution_rate_below_gate" not in decision.failures
    assert "satisfaction_confidence_below_98" in decision.failures


def test_zero_tolerance_violation_blocks_completion_even_if_other_dimensions_pass():
    scores = _passing_scores()
    scores[0].unsupported_claims = 1
    decision = evaluate_release(scores)
    assert decision.passed is False
    assert "zero_tolerance_violation" in decision.failures
    assert decision.satisfaction_rate < 1.0
