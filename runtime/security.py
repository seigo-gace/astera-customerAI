from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .schemas import GroundedFact


@dataclass(frozen=True)
class SecurityCheck:
    passed: bool
    violations: list[str]


class PublicBoundary:
    def filter_facts(self, facts: Iterable[GroundedFact]) -> list[GroundedFact]:
        return [f for f in facts if f.public and not f.legacy and not f.undecided]

    def check_output(self, *, answer: str, forbidden_literals: Iterable[str], unexecuted_completion_claim: bool) -> SecurityCheck:
        violations: list[str] = []
        for literal in forbidden_literals:
            if literal and literal in answer:
                violations.append("forbidden_literal_exposed"); break
        if unexecuted_completion_claim:
            violations.append("unexecuted_completion_claim")
        return SecurityCheck(passed=not violations, violations=violations)
