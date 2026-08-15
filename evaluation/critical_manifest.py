from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

POLICY_MINIMUM_CRITICAL = 30


@dataclass(frozen=True)
class CriticalManifest:
    scenario_ids: tuple[str, ...]
    decision_source: str
    minimum_required: int = POLICY_MINIMUM_CRITICAL

    def validate(self, available_scenario_ids: Iterable[str]) -> None:
        ids = tuple(item.strip() for item in self.scenario_ids if item and item.strip())
        if len(ids) != len(self.scenario_ids):
            raise ValueError("critical_manifest_contains_blank_id")
        if len(set(ids)) != len(ids):
            raise ValueError("critical_manifest_contains_duplicate_id")
        if self.minimum_required < POLICY_MINIMUM_CRITICAL:
            raise ValueError(
                f"critical_manifest_minimum_below_policy:{self.minimum_required}<{POLICY_MINIMUM_CRITICAL}"
            )
        if len(ids) < self.minimum_required:
            raise ValueError(f"critical_manifest_below_minimum:{len(ids)}<{self.minimum_required}")
        if not self.decision_source.strip():
            raise ValueError("critical_manifest_decision_source_required")

        available = set(available_scenario_ids)
        unknown = set(ids) - available
        if unknown:
            raise ValueError("critical_manifest_contains_unknown_scenario:" + ",".join(sorted(unknown)))


def load_critical_manifest(path: Path) -> CriticalManifest:
    """Load a test-only Critical manifest without inventing Critical policy.

    Expected JSON shape:
    {
      "decision_source": "<existing approved Notion/evidence source>",
      "minimum_required": 30,
      "scenario_ids": ["...", "..."]
    }

    The file records already-approved scenario IDs; this module never decides
    which scenarios are Critical. A manifest may raise the required volume, but
    it may never weaken the canonical minimum of 30 Critical scenarios.
    """

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("critical_manifest_must_be_json_object")
    scenario_ids = raw.get("scenario_ids")
    if not isinstance(scenario_ids, list):
        raise ValueError("critical_manifest_scenario_ids_must_be_list")
    return CriticalManifest(
        scenario_ids=tuple(str(item) for item in scenario_ids),
        decision_source=str(raw.get("decision_source") or ""),
        minimum_required=int(raw.get("minimum_required", POLICY_MINIMUM_CRITICAL)),
    )
