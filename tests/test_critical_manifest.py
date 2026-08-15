import json

import pytest

from evaluation.critical_manifest import load_critical_manifest
from evaluation.scenarios import load_learning_eval_jsonl


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
