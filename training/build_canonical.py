from __future__ import annotations

from collections import defaultdict

from .schemas import CanonicalFact, RawFact


class CanonicalConflictError(ValueError):
    pass


def _scope_key(item: RawFact) -> tuple[str, str, str, str, str]:
    return (
        item.conflict_group or item.canonical_key or item.fact_id,
        item.condition_signature,
        item.valid_from or "",
        item.valid_to or "",
        item.authority,
    )


def build_canonical(raw_facts: list[RawFact]) -> list[CanonicalFact]:
    approved = [item for item in raw_facts if item.status == "approved" and item.public and item.lifecycle_status == "active"]
    grouped: dict[tuple[str, str, str, str, str], list[RawFact]] = defaultdict(list)
    for item in approved:
        grouped[_scope_key(item)].append(item)
    output: list[CanonicalFact] = []
    for scope, items in grouped.items():
        statements = {item.statement.strip() for item in items}
        if len(statements) > 1:
            raise CanonicalConflictError(f"conflicting approved facts for scope {scope[0]}: {scope[1]}")
        chosen = items[-1]
        output.append(
            CanonicalFact(
                fact_id=chosen.fact_id,
                topic=chosen.topic,
                statement=chosen.statement,
                source_id=chosen.source_id,
                source_ids=list(dict.fromkeys(item.source_id for item in items)),
                public=chosen.public,
                volatile=chosen.volatile,
                canonical_key=chosen.canonical_key,
                condition_signature=chosen.condition_signature,
                conditions=list(chosen.conditions),
                exceptions=list(chosen.exceptions),
                relations=list(chosen.relations),
                authority=chosen.authority,
                valid_from=chosen.valid_from,
                valid_to=chosen.valid_to,
                conflict_group=chosen.conflict_group,
                lifecycle_status=chosen.lifecycle_status,
                generation_id=chosen.generation_id,
            )
        )
    return sorted(
        output,
        key=lambda item: (
            item.fact_id,
            item.condition_signature,
            item.valid_from or "",
            item.valid_to or "",
        ),
    )
