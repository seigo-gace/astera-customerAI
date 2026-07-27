from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .security import validate_identifier


JobStatus = Literal[
    "received",
    "accepted",
    "queued_processing",
    "processing",
    "awaiting_clarification",
    "awaiting_resolution",
    "retrying",
    "degraded",
    "completed",
    "failed",
]


class MessagePayload(BaseModel):
    session_id: str = Field(min_length=8, max_length=160)
    message_id: str = Field(min_length=8, max_length=160)
    message: str = Field(min_length=1, max_length=20000)
    locale: Literal["ja-JP", "en"] = "ja-JP"
    source: Literal["astera-hp", "astera-app"]

    @field_validator("session_id", "message_id")
    @classmethod
    def safe_identifier(cls, value: str) -> str:
        return validate_identifier(value)


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=8000)
    message_id: str = ""
    kb_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    question_ids: list[str] = Field(default_factory=list)
    answer_summary: str = Field(default="", max_length=1200)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SessionContext(BaseModel):
    session_id: str
    user_goal: str = ""
    active_topic: str = ""
    response_mode: str = "direct"
    confirmed_details: dict[str, Any] = Field(default_factory=dict)
    user_state: dict[str, Any] = Field(default_factory=dict)
    unresolved_questions: list[str] = Field(default_factory=list)
    answered_questions: list[str] = Field(default_factory=list)
    last_kb_ids: list[str] = Field(default_factory=list)
    last_evidence_ids: list[str] = Field(default_factory=list)
    topic_evidence: dict[str, list[str]] = Field(default_factory=dict)
    last_answer_summary: str = ""
    turns: list[ConversationTurn] = Field(default_factory=list)
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
    status: Literal["completed", "awaiting_clarification", "awaiting_resolution", "failed"]
    answer: str
    kb_ids: list[str] = Field(default_factory=list)
    ai_invoked: bool = False
    clarification: str | None = None
    facts: list[str] = Field(default_factory=list)
    context_used: bool = False
    cache_hit: bool = False
    repair_attempted: bool = False
    resolver_used: bool = False
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
