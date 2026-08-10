from __future__ import annotations

import asyncio
import hashlib
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Deque

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from runtime import CustomerAIService
from runtime.schemas import MessagePayload
from runtime.security import redact_text, validate_identifier


DEFAULT_ORIGINS = "https://asterav8.jp,https://www.asterav8.jp"
MAX_PUBLIC_MESSAGE_CHARS = 12_000
RATE_WINDOW_SECONDS = 60.0
RATE_LIMIT_PER_SESSION = max(
    1,
    int(os.getenv("CUSTOMER_AI_HP_REQUESTS_PER_MINUTE", "30")),
)
PUBLIC_ORIGINS = tuple(
    origin.strip()
    for origin in os.getenv("CUSTOMER_AI_PUBLIC_ORIGINS", DEFAULT_ORIGINS).split(",")
    if origin.strip()
)

service = CustomerAIService()
_rate_buckets: dict[str, Deque[float]] = defaultdict(deque)
_session_locks: dict[str, asyncio.Lock] = {}


def _allow_request(session_id: str) -> bool:
    now = time.monotonic()
    bucket = _rate_buckets[session_id]
    while bucket and now - bucket[0] >= RATE_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_PER_SESSION:
        return False
    bucket.append(now)
    if len(_rate_buckets) > 1024:
        stale = [
            key
            for key, values in _rate_buckets.items()
            if not values or now - values[-1] >= RATE_WINDOW_SECONDS
        ]
        for key in stale[:256]:
            _rate_buckets.pop(key, None)
            _session_locks.pop(key, None)
    return True


def _session_lock(session_id: str) -> asyncio.Lock:
    lock = _session_locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _session_locks[session_id] = lock
    return lock


def _origin_allowed(request: Request) -> bool:
    origin = request.headers.get("origin", "").strip()
    return not origin or origin in PUBLIC_ORIGINS


@asynccontextmanager
async def lifespan(_: FastAPI):
    await service.startup()
    yield
    await service.shutdown()


app = FastAPI(
    title="Astera Customer AI HP Runtime",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(PUBLIC_ORIGINS),
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["content-type", "accept"],
    max_age=86400,
)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict:
    checks = service.readiness()
    ready = bool(
        checks.get("data_root")
        and checks.get("v8")
        and checks.get("kb")
        and (
            not checks.get("model_enabled")
            or service.model.available()
        )
    )
    if not ready:
        raise HTTPException(
            status_code=503,
            detail={"ready": False, "checks": checks},
        )
    return {
        "ready": True,
        "checks": {
            "data_root": True,
            "v8": True,
            "kb": True,
            "model_enabled": bool(checks.get("model_enabled")),
            "hf_api_available": bool(service.model.available()),
        },
    }


@app.post("/respond")
async def respond(payload: MessagePayload, request: Request) -> dict:
    if not _origin_allowed(request):
        raise HTTPException(status_code=403, detail="origin_not_allowed")
    if payload.source != "astera-hp":
        raise HTTPException(status_code=422, detail="unsupported_public_source")
    if len(payload.message) > MAX_PUBLIC_MESSAGE_CHARS:
        raise HTTPException(status_code=413, detail="message_too_large")
    if not _allow_request(payload.session_id):
        raise HTTPException(
            status_code=429,
            detail="rate_limited",
            headers={"Retry-After": "60"},
        )

    redacted = redact_text(payload.message)
    safe_payload = payload.model_copy(update={"message": redacted.text})
    request_id = "hp_" + hashlib.sha256(
        f"{safe_payload.session_id}:{safe_payload.message_id}".encode()
    ).hexdigest()[:32]

    async with _session_lock(safe_payload.session_id):
        result = await service._run_pipeline(request_id, safe_payload)

    return {
        "status": result.get("status", "failed"),
        "session_id": safe_payload.session_id,
        "message_id": safe_payload.message_id,
        "answer": result.get("answer", ""),
        "clarification": result.get("clarification"),
        "context_used": bool(result.get("context_used")),
        "routing": result.get("routing", {}),
    }


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request) -> dict:
    if not _origin_allowed(request):
        raise HTTPException(status_code=403, detail="origin_not_allowed")
    safe_id = validate_identifier(session_id, field="session_id")
    deleted = service.conversations.delete(safe_id)
    _rate_buckets.pop(safe_id, None)
    _session_locks.pop(safe_id, None)
    return {
        "ok": True,
        "session_id": safe_id,
        "status": "deleted",
        "deleted": deleted,
    }


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.getenv("CUSTOMER_AI_HTTP_HOST", "127.0.0.1"),
        port=int(os.getenv("CUSTOMER_AI_HTTP_PORT", "7860")),
        log_level="info",
    )
