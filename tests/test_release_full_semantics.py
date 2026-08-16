from evaluation.release import evaluate_release
from evaluation.scorer import ScenarioScore


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


def _passing_scores() -> list[ScenarioScore]:
    scores: list[ScenarioScore] = []
    for scenario_class in SCENARIO_CLASSES:
        for index in range(20):
            scores.append(
                ScenarioScore(
                    scenario_id=f"{scenario_class}-{index:02d}",
                    scenario_class=scenario_class,
                    critical=scenario_class in {"false_premise", "negative_unsupported"},
                    multi_turn=scenario_class == "multi_turn",
                    false_premise=scenario_class == "false_premise",
                    resolved=True,
                    satisfied=True,
                )
            )
    return scores


def test_complete_220_scenario_evidence_can_pass() -> None:
    decision = evaluate_release(_passing_scores())
    assert decision.passed is True
    assert decision.failures == ()


def test_multiturn_carryover_failure_is_not_counted_as_satisfied() -> None:
    scores = _passing_scores()
    target = next(item for item in scores if item.scenario_class == "multi_turn")
    target.need_carryover_ok = False

    decision = evaluate_release(scores)

    assert decision.passed is False
    assert decision.satisfaction_rate < 1.0
    assert "satisfaction_confidence_below_gate" in decision.failures


def test_critical_contract_failure_reduces_critical_resolution() -> None:
    scores = _passing_scores()
    target = next(item for item in scores if item.critical)
    target.final_closure = False

    decision = evaluate_release(scores)

    assert decision.passed is False
    assert "critical_resolution_below_gate" in decision.failures


def test_resolution_and_satisfaction_are_measured_independently() -> None:
    scores = _passing_scores()
    scores[0].satisfied = False

    decision = evaluate_release(scores)

    assert decision.resolution_rate == 1.0
    assert decision.satisfaction_rate < 1.0
    assert decision.resolution_rate != decision.satisfaction_rate


def test_resolution_failure_does_not_rewrite_satisfaction_measurement() -> None:
    scores = _passing_scores()
    scores[0].resolved = False

    decision = evaluate_release(scores)

    assert decision.resolution_rate < 1.0
    assert decision.satisfaction_rate == 1.0
    assert decision.resolution_rate != decision.satisfaction_rate
