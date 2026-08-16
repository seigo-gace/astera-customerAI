import pytest
from pydantic import ValidationError

from evaluation.release import ReleaseGateConfig, evaluate_release
from evaluation.satisfaction import SatisfactionScore, wilson_lower_bound
from evaluation.scorer import ScenarioScore


def satisfaction(**overrides):
    values = {
        "evaluator_source": "external_judge",
        "evaluator_ref": "judge:independent@rev-1",
        "production_model_ref": "production:domain@rev-9",
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


def build_scores(count=220):
    classes = [f"c{i}" for i in range(11)]
    return [
        ScenarioScore(
            scenario_id=f"s{i}",
            scenario_class=classes[i % 11],
            critical=i < 35,
            multi_turn=35 <= i < 60,
            false_premise=60 <= i < 85,
            resolved=True,
            satisfaction=satisfaction(),
            false_premise_corrected=True,
        )
        for i in range(count)
    ]


def test_200_of_200_wilson_lower_bound_exceeds_98_percent():
    assert wilson_lower_bound(200, 200) > 0.98


def test_36_of_36_is_not_release_evidence():
    decision = evaluate_release(
        build_scores(36),
        ReleaseGateConfig(min_each_class=1, min_critical=1, min_multiturn=0, min_false_premise=0),
    )
    assert not decision.passed
    assert "insufficient_unseen_scenarios" in decision.failures


def test_sufficient_all_pass_corpus_can_pass_release_gate():
    decision = evaluate_release(build_scores(220))
    assert decision.passed
    assert decision.primary_metric == "answer_satisfaction"
    assert decision.satisfaction_rate == 1.0


def test_single_satisfied_boolean_is_not_valid_release_evidence():
    with pytest.raises(ValidationError):
        ScenarioScore(
            scenario_id="legacy",
            scenario_class="direct",
            resolved=True,
            satisfied=True,
        )


def test_structured_satisfaction_requires_user_value_dimensions():
    assert satisfaction().passed
    assert not satisfaction(required_depth_met=False).passed
    assert not satisfaction(constraints_respected=False).passed
    assert not satisfaction(conditions_exceptions_covered=False).passed
    assert not satisfaction(current_status_covered_when_required=False).passed
    assert not satisfaction(next_action_covered_when_required=False).passed
    assert not satisfaction(self_contained=False).passed


def test_production_model_cannot_be_its_own_external_judge():
    with pytest.raises(ValueError, match="external_judge_must_be_independent"):
        satisfaction(
            evaluator_ref="same-model@rev",
            production_model_ref="same-model@rev",
        )


def test_unknown_evaluator_source_is_rejected():
    with pytest.raises(ValidationError):
        satisfaction(evaluator_source="production_model")
