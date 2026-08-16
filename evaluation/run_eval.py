from __future__ import annotations

import json
import sys
from pathlib import Path

from .evidence import ScenarioEvaluationEvidence, score_evidence
from .release import evaluate_release
from .scenarios import load_scenarios


def _load_evidence(path: Path) -> dict[str, ScenarioEvaluationEvidence]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("evaluation_evidence_must_be_json_array")
    evidence = [ScenarioEvaluationEvidence.model_validate(item) for item in rows]
    by_id = {item.scenario_id: item for item in evidence}
    if len(by_id) != len(evidence):
        raise ValueError("duplicate_evaluation_evidence_scenario_id")
    return by_id


def evaluate_files(scenario_path: Path, evidence_path: Path):
    scenarios = load_scenarios(scenario_path)
    evidence_by_id = _load_evidence(evidence_path)
    scenario_ids = {scenario.scenario_id for scenario in scenarios}
    if set(evidence_by_id) != scenario_ids:
        missing = sorted(scenario_ids - set(evidence_by_id))
        extra = sorted(set(evidence_by_id) - scenario_ids)
        raise ValueError(f"scenario_evidence_coverage_mismatch missing={missing} extra={extra}")
    scores = [score_evidence(scenario, evidence_by_id[scenario.scenario_id]) for scenario in scenarios]
    return evaluate_release(scores)


def main(scenario_path: str, evidence_path: str) -> int:
    decision = evaluate_files(Path(scenario_path), Path(evidence_path))
    print(json.dumps(decision.__dict__, ensure_ascii=False, indent=2))
    return 0 if decision.passed else 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: python -m evaluation.run_eval <scenarios.json> <evidence.json>")
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
