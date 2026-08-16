import json

import pytest

from evaluation.evidence import RuntimeTurnEvidence, ScenarioEvaluationEvidence, score_evidence
from evaluation.run_eval import evaluate_files
from evaluation.satisfaction import SatisfactionScore
from evaluation.scenarios import EvaluationScenario, load_learning_eval_jsonl


RUNTIME_REV = "runtime@abc"
MODEL_REV = "model@def"
CORPUS_REV = "corpus@ghi"


def sat(**overrides):
    values = {
        "evaluator_source": "external_judge",
        "evaluator_ref": "judge@xyz",
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


def turn(user_text="料金は？", **overrides):
    values = {
        "user_text": user_text,
        "answer": "料金は根拠に基づき案内します。",
        "answered_task_ids": ["t1"],
        "unresolved_task_ids": [],
        "evidence_ids": ["fact-price"],
        "resolution_mode": "resolved",
        "passed": True,
        "clarification_questions": [],
        "violations": [],
        "latency_ms": 20.0,
        "retry_count": 0,
    }
    values.update(overrides)
    return RuntimeTurnEvidence(**values)


def scenario():
    return EvaluationScenario(
        scenario_id="s1",
        scenario_class="direct",
        expected_need_labels=["need-price"],
        required_fact_ids=["fact-price"],
        user_turns=["料金は？"],
    )


def evidence(**overrides):
    values = {
        "scenario_id": "s1",
        "runtime_revision": RUNTIME_REV,
        "model_revision": MODEL_REV,
        "corpus_revision": CORPUS_REV,
        "turns": [turn()],
        "observed_need_ids": ["need-price"],
        "grounded_fact_ids": ["fact-price"],
        "satisfaction": sat(),
    }
    values.update(overrides)
    return ScenarioEvaluationEvidence(**values)


def test_score_is_derived_from_runtime_evidence_not_prefilled_pass_boolean():
    score = score_evidence(scenario(), evidence())
    assert score.satisfaction_pass
    assert score.runtime_revision == RUNTIME_REV
    assert score.model_revision == MODEL_REV
    assert score.corpus_revision == CORPUS_REV


def test_missing_required_need_overrides_judge_claim_and_fails_satisfaction():
    ev = evidence(observed_need_ids=[])
    assert ev.satisfaction.all_major_needs_covered
    score = score_evidence(scenario(), ev)
    assert not score.satisfaction.all_major_needs_covered
    assert not score.satisfaction_pass


def test_missing_required_fact_overrides_judge_claim_and_fails_satisfaction():
    score = score_evidence(scenario(), evidence(grounded_fact_ids=[]))
    assert not score.satisfaction.evidence_complete
    assert not score.satisfaction_pass


def test_runtime_user_turns_must_exactly_match_approved_scenario():
    with pytest.raises(ValueError, match="user_turns"):
        score_evidence(scenario(), evidence(turns=[turn("別の質問")]))


def test_model_revision_in_judge_provenance_must_match_runtime_evidence():
    with pytest.raises(ValueError, match="production_model_revision_mismatch"):
        evidence(model_revision="model@other")


def test_zero_tolerance_is_derived_from_runtime_violations():
    score = score_evidence(
        scenario(),
        evidence(turns=[turn(violations=["unsupported_claim", "unexecuted_completion_claim"])]),
    )
    assert score.unsupported_claims == 1
    assert score.unexecuted_completion_claims == 1
    assert not score.satisfaction_pass


def test_learning_eval_loader_preserves_exact_user_turns_and_required_facts(tmp_path):
    path = tmp_path / "eval.jsonl"
    row = {
        "scenario_id": "s1",
        "scenario_class": "direct",
        "need_labels": ["need-price"],
        "grounding_required_fact_ids": ["fact-price"],
        "messages": [
            {"role": "user", "content": "料金は？"},
            {"role": "assistant", "content": "expected"},
        ],
    }
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    loaded = load_learning_eval_jsonl(path)
    assert loaded[0].user_turns == ["料金は？"]
    assert loaded[0].required_fact_ids == ["fact-price"]


def test_run_eval_requires_complete_scenario_evidence_pair(tmp_path):
    scenario_path = tmp_path / "scenarios.json"
    evidence_path = tmp_path / "evidence.json"
    scenario_path.write_text(json.dumps([scenario().model_dump(mode="json")], ensure_ascii=False), encoding="utf-8")
    evidence_path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="coverage_mismatch"):
        evaluate_files(scenario_path, evidence_path)
