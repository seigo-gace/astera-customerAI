from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

POLICY_MINIMUM_CRITICAL = 30
TEST_ONLY_STRICT_CRITICAL_CLASSES = frozenset({"false_premise", "negative_unsupported"})
TEST_ONLY_DECISION_SOURCE = (
    "Notion 03-03 Evaluation/Test canon: false-premise correction 100%, "
    "unsupported Astera-specific hallucination 0; test-only derivation, not Production critical canon"
)


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


def derive_test_only_strict_manifest(scenarios: Iterable[object]) -> CriticalManifest:
    """Derive a non-Production Critical manifest from already-strict canonical gates.

    This helper does not define new Critical policy. It marks only scenario classes
    that the existing Evaluation canon already treats as zero-tolerance/100% gates:
    false-premise correction and unsupported-claim prevention. The result is for
    test/evidence coverage only and must not be promoted to Production Critical
    canon without a separate explicit decision.
    """

    selected: list[str] = []
    available: list[str] = []
    for scenario in scenarios:
        scenario_id = str(getattr(scenario, "scenario_id", "") or "").strip()
        scenario_class = str(getattr(scenario, "scenario_class", "") or "").strip()
        if not scenario_id or not scenario_class:
            raise ValueError("test_only_critical_derivation_requires_identity")
        available.append(scenario_id)
        if scenario_class in TEST_ONLY_STRICT_CRITICAL_CLASSES:
            selected.append(scenario_id)

    manifest = CriticalManifest(
        scenario_ids=tuple(selected),
        decision_source=TEST_ONLY_DECISION_SOURCE,
        minimum_required=POLICY_MINIMUM_CRITICAL,
    )
    manifest.validate(available)
    return manifest


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
