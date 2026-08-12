from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class RoleName(str, Enum):
    CONSTRUCTIVE = "constructive"
    ADVERSARIAL = "adversarial"
    EVIDENCE_BOUND = "evidence_bound"


class NeedTask(BaseModel):
    task_id: str
    text: str
    intent: str
    required_facts: list[str] = Field(default_factory=list)
    completion_condition: str
    priority: Literal["primary", "secondary"] = "primary"
    response_shape: Literal["direct", "procedure", "comparison", "troubleshooting"] = "direct"
    required_user_inputs: list[str] = Field(default_factory=list)
    actionability_required: bool = False


class GroundedFact(BaseModel):
    fact_id: str
    value: str
    source_id: str
    authority: Literal["canonical", "current", "live"]
    freshness: str | None = None
    public: bool = True
    legacy: bool = False
    undecided: bool = False


class TaskResolution(BaseModel):
    task_id: str
    public_text: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    action_steps: list[str] = Field(default_factory=list)
    unresolved_reason: str | None = None

    @property
    def resolved(self) -> bool:
        return bool(self.public_text.strip()) and self.unresolved_reason is None


class SharedRolePacket(BaseModel):
    request_id: str
    session_id: str
    turn_id: str
    user_message: str
    normalized_need: str
    audience: str
    tasks: list[NeedTask]
    user_conditions: dict[str, Any] = Field(default_factory=dict)
    language_hints: dict[str, Any] = Field(default_factory=dict)
    facts: list[GroundedFact] = Field(default_factory=list)
    unresolved_items: list[str] = Field(default_factory=list)
    repair_targets: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    legacy_exclusions: list[str] = Field(default_factory=list)
    completion_conditions: list[str] = Field(default_factory=list)


class RoleResult(BaseModel):
    role: RoleName
    claims: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    missing_needs: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    task_resolutions: list[TaskResolution] = Field(default_factory=list)
    proposed_resolution: str = Field(default="", description="Legacy migration field; never use as the sole final response source.")
    completion_state: Literal["complete", "partial", "blocked"] = "partial"


class ResolutionMode(str, Enum):
    RESOLVED = "resolved"
    NEEDS_USER_INPUT = "needs_user_input"
    SAFE_PARTIAL = "safe_partial"
    BLOCKED_CURRENT_FACT = "blocked_current_fact"
    SAFETY_BLOCKED = "safety_blocked"
    RUNTIME_FAILURE = "runtime_failure"


class FinalResponse(BaseModel):
    request_id: str
    session_id: str
    turn_id: str
    answer: str | None
    answered_task_ids: list[str]
    unresolved_task_ids: list[str]
    evidence_ids: list[str]
    resolution_score: float
    passed: bool
    resolution_mode: ResolutionMode
    clarification_questions: list[str] = Field(default_factory=list)
    failure_class: Literal["coverage_defect", "grounding_conflict", "safety_rejection", "runtime_failure"] | None = None
    violations: list[str] = Field(default_factory=list)
