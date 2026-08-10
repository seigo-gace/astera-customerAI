from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Deque

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from runtime import CustomerAIService
from runtime.schemas import MessagePayload
from runtime.security import redact_text

LOGGER = logging.getLogger(__name__)
PUBLIC_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CUSTOMER_AI_PUBLIC_ORIGINS",
        "https://asterav8.jp,https://www.asterav8.jp",
    ).split(",")
    if origin.strip()
]
PUBLIC_RATE_LIMIT = max(1, int(os.getenv("CUSTOMER_AI_PUBLIC_SESSION_REQUESTS_PER_MINUTE", "30")))
_RATE_WINDOW_SECONDS = 60.0
_RATE_BUCKETS: dict[str, Deque[float]] = defaultdict(deque)
_SESSION_LOCKS: dict[str, asyncio.Lock] = {}
service = CustomerAIService()


def _allow_request(session_id: str) -> bool:
    now = time.monotonic()
    bucket = _RATE_BUCKETS[session_id]
    while bucket and now - bucket[0] >= _RATE_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= PUBLIC_RATE_LIMIT:
        return False
    bucket.append(now)
    if len(_RATE_BUCKETS) > 1024:
        stale = [key for key, values in _RATE_BUCKETS.items() if not values or now - values[-1] >= _RATE_WINDOW_SECONDS]
        for key in stale[:256]:
            _RATE_BUCKETS.pop(key, None)
            _SESSION_LOCKS.pop(key, None)
    return True


def _session_lock(session_id: str) -> asyncio.Lock:
    lock = _SESSION_LOCKS.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _SESSION_LOCKS[session_id] = lock
    return lock


@asynccontextmanager
async def lifespan(_: FastAPI):
    await service.startup()
    if service.settings.notion_token:
        try:
            version = f"public-live-{int(time.time())}"
            synced = await service.sync_notion_kb(version)
            LOGGER.info("CUSTOMER_AI_PUBLIC_KB_SYNC_OK version=%s source_pages=%s", synced.get("version"), synced.get("source_pages"))
        except Exception:
            LOGGER.exception("CUSTOMER_AI_PUBLIC_KB_SYNC_FAILED")
    yield
    await service.shutdown()


app = FastAPI(
    title="Astera Customer AI Public API",
    version="2.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=PUBLIC_ORIGINS,
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
    ready = bool(checks.get("data_root") and checks.get("v8") and checks.get("kb"))
    if not ready:
        raise HTTPException(status_code=503, detail={"ready": False, "checks": checks})
    return {"ready": True, "checks": {"data_root": True, "v8": True, "kb": True, "model_enabled": checks.get("model_enabled", False)}}


@app.post("/public/customer-ai/respond")
async def public_respond(payload: MessagePayload) -> dict:
    if payload.source != "astera-hp":
        raise HTTPException(status_code=422, detail="unsupported_public_source")
    if not _allow_request(payload.session_id):
        raise HTTPException(status_code=429, detail="rate_limited", headers={"Retry-After": "60"})
    redacted = redact_text(payload.message)
    request = payload.model_copy(update={"message": redacted.text})
    request_id = "public_" + hashlib.sha256(f"{request.session_id}:{request.message_id}".encode()).hexdigest()[:32]
    async with _session_lock(request.session_id):
        result = await service._run_pipeline(request_id, request)
    return {
        "status": result.get("status", "failed"),
        "session_id": request.session_id,
        "answer": result.get("answer", ""),
        "clarification": result.get("clarification"),
        "context_used": bool(result.get("context_used")),
        "routing": result.get("routing", {}),
    }


@app.delete("/public/customer-ai/sessions/{session_id}")
async def delete_public_session(session_id: str) -> dict:
    from runtime.security import validate_identifier

    safe_id = validate_identifier(session_id, field="session_id")
    deleted = service.conversations.delete(safe_id)
    _RATE_BUCKETS.pop(safe_id, None)
    _SESSION_LOCKS.pop(safe_id, None)
    return {"ok": True, "session_id": safe_id, "deleted": deleted}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860, log_level="info")
