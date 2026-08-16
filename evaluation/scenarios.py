from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, Field


class EvaluationScenario(BaseModel):
    scenario_id: str
    scenario_class: str
    critical: bool = False
    multi_turn: bool = False
    false_premise: bool = False
    expected_need_labels: list[str] = Field(default_factory=list)
    required_fact_ids: list[str] = Field(default_factory=list)
    user_turns: list[str] = Field(default_factory=list, min_length=1)
    followup_depth: int = 0
    requires_need_carryover: bool = False
    requires_non_regression: bool = False
    requires_delta_retrieval: bool = False
    requires_final_closure: bool = False


def load_scenarios(path: Path) -> list[EvaluationScenario]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("scenario_file_must_be_json_array")
    return [EvaluationScenario.model_validate(item) for item in rows]


def load_learning_eval_jsonl(
    path: Path,
    *,
    critical_ids: Iterable[str] = (),
) -> list[EvaluationScenario]:
    """Convert validated Learning Corpus JSONL into evidence-bound evaluation scenarios.

    The scenario keeps the exact user turns and required facts so later answer
    satisfaction evidence can be tied to what the runtime actually received.
    `critical` is never inferred; callers provide independently approved IDs.
    """

    approved_critical_ids = set(critical_ids)
    rows: list[EvaluationScenario] = []
    seen: set[str] = set()

    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        raw = json.loads(line)
        if not isinstance(raw, dict):
            raise ValueError(f"evaluation_jsonl_row_must_be_object:{line_no}")

        scenario_id = str(raw.get("scenario_id") or "").strip()
        scenario_class = str(raw.get("scenario_class") or "").strip()
        if not scenario_id or not scenario_class:
            raise ValueError(f"evaluation_jsonl_identity_missing:{line_no}")
        if scenario_id in seen:
            raise ValueError(f"duplicate_evaluation_scenario_id:{scenario_id}")
        seen.add(scenario_id)

        need_labels = raw.get("need_labels") or []
        if not isinstance(need_labels, list):
            raise ValueError(f"evaluation_need_labels_must_be_list:{scenario_id}")

        fact_ids = raw.get("grounding_required_fact_ids") or raw.get("fact_ids") or []
        if not isinstance(fact_ids, list):
            raise ValueError(f"evaluation_fact_ids_must_be_list:{scenario_id}")

        messages = raw.get("messages") or []
        if not isinstance(messages, list):
            raise ValueError(f"evaluation_messages_must_be_list:{scenario_id}")
        user_turns = [
            str(message.get("content") or "")
            for message in messages
            if isinstance(message, dict) and message.get("role") == "user"
        ]
        if not user_turns or any(not turn.strip() for turn in user_turns):
            raise ValueError(f"evaluation_user_turns_missing:{scenario_id}")
        followup_depth = max(0, len(user_turns) - 1)

        rows.append(
            EvaluationScenario(
                scenario_id=scenario_id,
                scenario_class=scenario_class,
                critical=scenario_id in approved_critical_ids,
                multi_turn=scenario_class == "multi_turn",
                false_premise=scenario_class == "false_premise",
                expected_need_labels=[str(item) for item in need_labels if str(item).strip()],
                required_fact_ids=[str(item) for item in fact_ids if str(item).strip()],
                user_turns=user_turns,
                followup_depth=followup_depth,
                requires_need_carryover=scenario_class == "multi_turn" and followup_depth > 0,
                requires_non_regression=scenario_class in {"multi_turn", "condition_change"},
                requires_delta_retrieval=scenario_class == "condition_change",
                requires_final_closure=scenario_class in {"procedure", "troubleshooting"},
            )
        )

    unknown_critical = approved_critical_ids - seen
    if unknown_critical:
        raise ValueError("critical_manifest_contains_unknown_scenario:" + ",".join(sorted(unknown_critical)))
    return rows
