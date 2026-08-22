from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from runtime.service import CustomerAIWork
from runtime.startup import RuntimeNotReady, create_work_from_environment

WORK: CustomerAIWork | None = None
STARTUP_BLOCKER: str | None = "not_started"


class MessageRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=160)
    message: str = Field(min_length=1, max_length=200000)


class PublicRespondRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    source: str = Field(default="astera-hp", min_length=1, max_length=64)
    locale: str = Field(default="ja-JP", min_length=2, max_length=32)
    session_id: str = Field(min_length=1, max_length=160)
    message_id: str = Field(min_length=1, max_length=160)
    response_mode: Literal[
        "general",
        "operation",
        "billing",
        "technical",
        "investor",
        "support",
        "trouble",
        "auto",
    ] = "auto"
    mode_source: Literal["selected", "auto", "confirmed"] = "auto"
    current_path: str = Field(default="/", max_length=2048)


def set_work(work: CustomerAIWork | None) -> None:
    global WORK, STARTUP_BLOCKER
    WORK = work
    STARTUP_BLOCKER = None if work is not None else "customer_ai_not_ready"


def bootstrap_from_environment() -> None:
    global WORK, STARTUP_BLOCKER
    if WORK is not None:
        STARTUP_BLOCKER = None
        return
    try:
        WORK = create_work_from_environment()
        STARTUP_BLOCKER = None
    except RuntimeNotReady as exc:
        WORK = None
        STARTUP_BLOCKER = exc.code


@asynccontextmanager
async def lifespan(_: FastAPI):
    bootstrap_from_environment()
    yield


app = FastAPI(title="Astera Customer AI", version="0.0.0", lifespan=lifespan)
_DEFAULT_ALLOWED_ORIGINS = (
    "https://asterav8.jp",
    "https://staging.asterav8.jp",
    "https://open.asterav8.jp",
    "https://localhost",
    "capacitor://localhost",
)


def _merge_allowed_origins(configured: str) -> list[str]:
    extra_origins = [
        origin.strip()
        for origin in configured.split(",")
        if origin.strip()
    ]
    return list(dict.fromkeys([*_DEFAULT_ALLOWED_ORIGINS, *extra_origins]))


_configured_origins = os.environ.get("CUSTOMER_AI_ALLOWED_ORIGINS", "").strip()
_origins = _merge_allowed_origins(_configured_origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["content-type", "accept"],
)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "zero_gpu": False}


@app.get("/ready")
async def ready() -> dict[str, Any]:
    is_ready = WORK is not None
    return {
        "status": "ready" if is_ready else "not_ready",
        "three_role_resident": is_ready,
        "zero_gpu": False,
        "blocker": None if is_ready else STARTUP_BLOCKER,
    }


@app.post("/v1/customer-ai/messages")
async def customer_ai_message(req: MessageRequest) -> dict[str, Any]:
    if WORK is None:
        raise HTTPException(status_code=503, detail=STARTUP_BLOCKER or "customer_ai_not_ready")
    return (await WORK.run(req.session_id, req.message)).model_dump(mode="json")


@app.post("/respond")
async def public_respond(req: PublicRespondRequest) -> dict[str, Any]:
    if WORK is None:
        raise HTTPException(status_code=503, detail=STARTUP_BLOCKER or "customer_ai_runtime_not_configured")
    try:
        result = await WORK.run(req.session_id, req.message)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="runtime_process_failed") from exc

    clarification = "\n".join(result.clarification_questions).strip()
    if result.passed:
        status = "completed"
    elif clarification:
        status = "awaiting_clarification"
    else:
        status = "failed"

    return {
        "status": status,
        "answer": result.answer or "",
        "clarification": clarification,
        "session_id": req.session_id,
        "message_id": req.message_id,
        "response_mode": req.response_mode,
        "mode_source": req.mode_source,
        "resolution_mode": result.resolution_mode.value,
        "resolution_score": result.resolution_score,
        "evidence_ids": result.evidence_ids,
        "error": result.failure_class if status == "failed" else None,
    }


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str) -> dict[str, Any]:
    if len(session_id) > 160:
        raise HTTPException(status_code=400, detail="invalid_session_id")
    if WORK is None:
        return {"ok": True, "deleted": False}
    return {"ok": True, "deleted": WORK.delete_session(session_id)}
