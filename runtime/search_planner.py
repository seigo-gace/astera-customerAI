from __future__ import annotations

import re

from .contracts import SearchMode, SearchPlan, TaskContract

_TOKEN = re.compile(r"[A-Za-z0-9_.+-]{2,}|[一-龥ぁ-んァ-ン]{2,}")


def _dedupe(items):
    return list(dict.fromkeys(item for item in items if item))


class SearchPlanner:
    def plan(self, contract: TaskContract, mode: SearchMode) -> SearchPlan:
        targets = [t.text for t in contract.need_tasks]
        terms = _dedupe(token.casefold() for target in targets for token in _TOKEN.findall(target))
        required = _dedupe(fact for task in contract.need_tasks for fact in task.required_facts)
        if mode == SearchMode.RUNTIME_GROUNDING:
            return SearchPlan(
                targets=targets,
                search_terms=terms[:24],
                required_evidence=required,
                source_priority=["canonical", "current", "live"],
                comparison_conditions=list(contract.conditions),
                verification_conditions=["public_only", "exclude_legacy", "exclude_undecided", "same_authority_conflict_fail_closed"],
                unresolved_gaps=list(contract.missing_information),
                mode=mode,
            )
        if mode == SearchMode.KB_HARVEST:
            return SearchPlan(
                targets=targets,
                search_terms=terms[:40],
                required_evidence=required,
                source_priority=["official", "primary", "canonical_candidate", "secondary"],
                comparison_conditions=list(contract.conditions),
                verification_conditions=["preserve_provenance", "preserve_conditions", "preserve_exceptions", "collect_counter_evidence", "never_auto_canon"],
                unresolved_gaps=list(contract.missing_information),
                mode=mode,
            )
        shapes = _dedupe(task.response_shape for task in contract.need_tasks)
        intents = _dedupe(task.intent for task in contract.need_tasks)
        return SearchPlan(
            targets=[*shapes, *intents],
            search_terms=_dedupe([*shapes, *intents, *terms[:12]]),
            required_evidence=[],
            source_priority=["ACTIVE"],
            comparison_conditions=[],
            verification_conditions=["active_only", "capability_capsule_only"],
            unresolved_gaps=[],
            mode=mode,
        )
