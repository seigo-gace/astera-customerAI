from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .security import validate_identifier


JobStatus = Literal[
    "received",
    "accepted",
    "queued_processing",
    "processing",
    "awaiting_clarification",
    "retrying",
    "degraded",
    "completed",
    "failed",
]
ResponseMode = Literal[
    "general",
    "operation",
    "billing",
    "technical",
    "investor",
    "support",
    "trouble",
    "auto",
]
ModeSource = Literal["selected", "auto", "confirmed"]


class MessagePayload(BaseModel):
    session_id: str = Field(min_length=8, max_length=160)
    message_id: str = Field(min_length=8, max_length=160)
    message: str = Field(min_length=1, max_length=20000)
    locale: Literal["ja-JP", "en"] = "ja-JP"
    source: Literal["astera-hp", "astera-app", "astera-api"]
    response_mode: ResponseMode = "auto"
    mode_source: ModeSource = "auto"
    current_path: str = Field(default="/", max_length=512)

    @field_validator("session_id", "message_id")
    @classmethod
    def safe_identifier(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("current_path")
    @classmethod
    def safe_current_path(cls, value: str) -> str:
        path = str(value or "/").strip()
        if not path.startswith("/") or "://" in path:
            return "/"
        return path.split("?", 1)[0].split("#", 1)[0][:512] or "/"


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=8000)
    message_id: str = ""
    kb_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SessionContext(BaseModel):
    session_id: str
    user_goal: str = ""
    active_topic: str = ""
    confirmed_details: dict[str, Any] = Field(default_factory=dict)
    unresolved_questions: list[str] = Field(default_factory=list)
    last_kb_ids: list[str] = Field(default_factory=list)
    turns: list[ConversationTurn] = Field(default_factory=list)
    answered_question_ids: list[str] = Field(default_factory=list)
    question_ledger: list[dict[str, Any]] = Field(default_factory=list)
    evidence_cache: dict[str, dict[str, Any]] = Field(default_factory=dict)
    last_blueprint: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CloudEvent(BaseModel):
    specversion: Literal["1.0"] = "1.0"
    id: str = Field(min_length=8, max_length=160)
    source: str = Field(min_length=3, max_length=300)
    type: str = Field(min_length=3, max_length=160)
    subject: str = Field(min_length=3, max_length=300)
    time: datetime
    datacontenttype: Literal["application/json"] = "application/json"
    data: dict[str, Any]


class JobRecord(BaseModel):
    job_id: str
    event_id: str
    session_id: str
    message_id: str
    status: JobStatus
    stage: str
    request_hash: str
    created_at: datetime
    updated_at: datetime
    attempts: int = 0
    error_code: str | None = None
    queue_event_id: str | None = None

    @classmethod
    def new(cls, *, job_id: str, event_id: str, payload: MessagePayload, request_hash: str) -> "JobRecord":
        now = datetime.now(UTC)
        return cls(
            job_id=job_id,
            event_id=event_id,
            session_id=payload.session_id,
            message_id=payload.message_id,
            status="accepted",
            stage="durable_accept",
            request_hash=request_hash,
            created_at=now,
            updated_at=now,
        )


class JobResult(BaseModel):
    job_id: str
    session_id: str
    status: Literal["completed", "awaiting_clarification", "failed"]
    answer: str
    kb_ids: list[str] = Field(default_factory=list)
    ai_invoked: bool = False
    clarification: str | None = None
    facts: list[str] = Field(default_factory=list)
    context_used: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class KBHit(BaseModel):
    kb_id: str
    question: str
    short_answer: str
    body: str
    score: float
    answer_boundary: str = ""
    target: str = ""


class NodeResponse(BaseModel):
    request_id: str
    ok: bool
    result: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    duration_ms: int = 0


# --- Current Customer AI work contract (additive migration) ---

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
    proposed_resolution: str = Field(
        default="",
        description="Legacy migration field; do not use as sole Final Response source",
    )
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
    failure_class: Literal[
        "coverage_defect",
        "grounding_conflict",
        "safety_rejection",
        "runtime_failure",
    ] | None = None
    violations: list[str] = Field(default_factory=list)
