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
    "awaiting_confirmation",
    "awaiting_action",
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
    status: Literal["completed", "awaiting_clarification", "awaiting_confirmation", "failed"]
    answer: str
    kb_ids: list[str] = []
    ai_invoked: bool = False
    action: dict[str, Any] | None = None
    clarification: str | None = None
    facts: list[str] = []
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
    result: dict[str, Any] = {}
    error_code: str | None = None
    duration_ms: int = 0
