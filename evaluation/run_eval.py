from __future__ import annotations

import json
import sys
from pathlib import Path

from .release import evaluate_release
from .scorer import ScenarioScore


def main(path: str) -> int:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("evaluation_input_must_be_list")
    scores = [ScenarioScore.model_validate(item) for item in rows]
    decision = evaluate_release(scores)
    print(json.dumps(decision.__dict__, ensure_ascii=False, indent=2))
    return 0 if decision.passed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
