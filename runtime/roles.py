from __future__ import annotations

from .schemas import RoleName

ROLE_RULES: dict[RoleName, tuple[str, ...]] = {
    RoleName.CONSTRUCTIVE: (
        "Resolve every major user need.",
        "Build the answer, conditions, exceptions, procedure and completion conditions.",
        "Do not invent Astera-specific facts.",
    ),
    RoleName.ADVERSARIAL: (
        "Independently inspect missing needs, contradictions and failure conditions.",
        "Detect false premises, legacy mixing, unsupported claims and hidden conditions.",
        "Do not expand the task outside the user need.",
    ),
    RoleName.EVIDENCE_BOUND: (
        "Bind claims to supplied canonical/current/live evidence.",
        "Check source authority, freshness, conflicts and public boundary.",
        "Do not approve claims without supported evidence when grounding is required.",
    ),
}


def role_rules(role: RoleName) -> tuple[str, ...]:
    return ROLE_RULES[role]
