import json

import pytest

from evaluation.critical_manifest import derive_test_only_strict_manifest, load_critical_manifest
from evaluation.scenarios import EvaluationScenario, load_learning_eval_jsonl


def _write_eval_jsonl(path, count=30):
    rows = []
    for index in range(count):
        scenario_id = f"eval-{index:03d}"
        scenario_class = "multi_turn" if index == 0 else "direct"
        rows.append(
            {
                "scenario_id": scenario_id,
                "scenario_class": scenario_class,
                "need_labels": ["need-a"],
                "messages": [
                    {"role": "user", "content": "question"},
                    {"role": "assistant", "content": "answer"},
                ],
            }
        )
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    return rows


def test_approved_manifest_marks_only_approved_scenarios_critical(tmp_path):
    eval_path = tmp_path / "eval.jsonl"
    rows = _write_eval_jsonl(eval_path, count=31)
    critical_ids = [row["scenario_id"] for row in rows[:30]]

    manifest_path = tmp_path / "critical.json"
    manifest_path.write_text(
        json.dumps(
            {
                "decision_source": "approved-notion-evidence",
                "minimum_required": 30,
                "scenario_ids": critical_ids,
            }
        ),
        encoding="utf-8",
    )

    manifest = load_critical_manifest(manifest_path)
    available_ids = [row["scenario_id"] for row in rows]
    manifest.validate(available_ids)
    scenarios = load_learning_eval_jsonl(eval_path, critical_ids=manifest.scenario_ids)

    assert sum(scenario.critical for scenario in scenarios) == 30
    assert scenarios[0].multi_turn is True
    assert scenarios[-1].critical is False


def test_manifest_below_canonical_minimum_fails_closed(tmp_path):
    manifest_path = tmp_path / "critical.json"
    manifest_path.write_text(
        json.dumps(
            {
                "decision_source": "approved-notion-evidence",
                "minimum_required": 30,
                "scenario_ids": [f"eval-{index:03d}" for index in range(29)],
            }
        ),
        encoding="utf-8",
    )

    manifest = load_critical_manifest(manifest_path)
    with pytest.raises(ValueError, match="critical_manifest_below_minimum"):
        manifest.validate([f"eval-{index:03d}" for index in range(30)])


def test_unknown_critical_id_is_rejected_by_eval_loader(tmp_path):
    eval_path = tmp_path / "eval.jsonl"
    _write_eval_jsonl(eval_path, count=30)

    with pytest.raises(ValueError, match="critical_manifest_contains_unknown_scenario"):
        load_learning_eval_jsonl(eval_path, critical_ids={"not-present"})


def test_test_only_strict_derivation_uses_only_existing_zero_tolerance_classes():
    scenarios = [
        EvaluationScenario(scenario_id=f"fp-{index:02d}", scenario_class="false_premise")
        for index in range(20)
    ] + [
        EvaluationScenario(scenario_id=f"nu-{index:02d}", scenario_class="negative_unsupported")
        for index in range(20)
    ] + [
        EvaluationScenario(scenario_id=f"direct-{index:02d}", scenario_class="direct")
        for index in range(5)
    ]

    manifest = derive_test_only_strict_manifest(scenarios)

    assert len(manifest.scenario_ids) == 40
    assert all(scenario_id.startswith(("fp-", "nu-")) for scenario_id in manifest.scenario_ids)
    assert not any(scenario_id.startswith("direct-") for scenario_id in manifest.scenario_ids)
    assert "test-only" in manifest.decision_source


def test_test_only_strict_derivation_fails_if_canonical_strict_coverage_is_too_small():
    scenarios = [
        EvaluationScenario(scenario_id=f"fp-{index:02d}", scenario_class="false_premise")
        for index in range(20)
    ]

    with pytest.raises(ValueError, match="critical_manifest_below_minimum"):
        derive_test_only_strict_manifest(scenarios)
