from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from collections import defaultdict, deque
from typing import Any, Deque

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

RESPONSE_MODES = {"general", "operation", "billing", "technical", "investor", "support", "trouble", "auto"}
MODE_SOURCES = {"selected", "auto", "confirmed"}
PUBLIC_ORIGINS = [x.strip() for x in os.getenv("CUSTOMER_AI_PUBLIC_ORIGINS", "https://asterav8.jp,https://www.asterav8.jp").split(",") if x.strip()]
RATE_LIMIT = max(1, int(os.getenv("CUSTOMER_AI_PUBLIC_SESSION_REQUESTS_PER_MINUTE", "30")))
RATE_WINDOW_SECONDS = 60.0
RATE_BUCKETS: dict[str, Deque[float]] = defaultdict(deque)

app = FastAPI(title="Astera Customer AI Public Facade", version="3.0.0", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(CORSMiddleware, allow_origins=PUBLIC_ORIGINS, allow_credentials=False, allow_methods=["GET", "POST", "DELETE", "OPTIONS"], allow_headers=["content-type", "accept"], max_age=86400)


def _runtime_url() -> str:
    return os.getenv("PRIVATE_HF_RUNTIME_URL", "").strip().rstrip("/")


def _hf_token() -> str:
    return os.getenv("HF_TOKEN", "").strip()


def _hmac_secret() -> str:
    return os.getenv("CUSTOMER_AI_HMAC_SECRET", "").strip()


def _configured() -> bool:
    return bool(_runtime_url() and _hf_token() and _hmac_secret())


def _decode_secret(secret: str) -> bytes:
    if secret.startswith("base64:"):
        try:
            return base64.b64decode(secret.removeprefix("base64:"), validate=True)
        except ValueError:
            return b""
    return secret.encode("utf-8")


def _sign_standard(raw: bytes, event_id: str, timestamp: str, secret: str) -> str:
    key = _decode_secret(secret)
    if not key:
        raise RuntimeError("runtime_hmac_secret_invalid")
    body_text = raw.decode("utf-8")
    digest = hmac.new(key, f"{event_id}.{timestamp}.{body_text}".encode("utf-8"), hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode("ascii")


def _sign_process(raw: bytes, timestamp: str, secret: str) -> str:
    if not secret:
        raise RuntimeError("runtime_hmac_secret_invalid")
    digest = hmac.new(secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + raw, hashlib.sha256).hexdigest()
    return "sha256=" + digest


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _valid_id(value: Any, prefix: str) -> bool:
    text = str(value or "")
    return text.startswith(prefix + "_") and 12 <= len(text) <= 160 and all(c.isalnum() or c in "_.:-" for c in text)


def _normalize_path(value: Any) -> str:
    text = str(value or "/").strip().split("?", 1)[0].split("#", 1)[0]
    return text[:512] if text.startswith("/") and "://" not in text else "/"


def _allow_session(session_id: str) -> bool:
    now = time.monotonic()
    bucket = RATE_BUCKETS[session_id]
    while bucket and now - bucket[0] >= RATE_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT:
        return False
    bucket.append(now)
    if len(RATE_BUCKETS) > 1024:
        stale = [key for key, values in RATE_BUCKETS.items() if not values or now - values[-1] >= RATE_WINDOW_SECONDS]
        for key in stale[:256]:
            RATE_BUCKETS.pop(key, None)
    return True


def _auth_headers() -> dict[str, str]:
    return {"authorization": f"Bearer {_hf_token()}"}


async def _accept_event(event: dict[str, Any]) -> httpx.Response:
    raw = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time()))
    headers = {
        **_auth_headers(),
        "content-type": "application/cloudevents+json",
        "webhook-id": str(event["id"]),
        "webhook-timestamp": timestamp,
        "webhook-signature": _sign_standard(raw, str(event["id"]), timestamp, _hmac_secret()),
    }
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        return await client.post(_runtime_url() + "/internal/customer-ai/accept", headers=headers, content=raw)


async def _process_job(job_id: str) -> dict[str, Any]:
    raw = b"{}"
    timestamp = str(int(time.time()))
    headers = {
        **_auth_headers(),
        "content-type": "application/json",
        "x-webhook-timestamp": timestamp,
        "x-webhook-signature": _sign_process(raw, timestamp, _hmac_secret()),
    }
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.post(_runtime_url() + f"/internal/customer-ai/jobs/{job_id}/process", headers=headers, content=raw)
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="runtime_process_failed")
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="runtime_process_invalid") from exc


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"status": "ok", "service": "customer-ai-github-facade", "runtime_configured": _configured()}


@app.get("/public/customer-ai/config")
async def public_config() -> dict[str, str]:
    return {"turnstile_site_key": ""}


@app.post("/public/customer-ai/respond")
async def public_respond(request: Request) -> dict[str, Any]:
    if not _configured():
        raise HTTPException(status_code=503, detail="customer_ai_runtime_not_configured")
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid_json") from exc
    message = str(payload.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="message_required")
    if len(message) > 12000:
        raise HTTPException(status_code=413, detail="message_too_large")
    response_mode = str(payload.get("response_mode") or "auto")
    mode_source = str(payload.get("mode_source") or ("auto" if response_mode == "auto" else "selected"))
    if response_mode not in RESPONSE_MODES:
        raise HTTPException(status_code=422, detail="response_mode_invalid")
    if mode_source not in MODE_SOURCES:
        raise HTTPException(status_code=422, detail="mode_source_invalid")
    session_id = str(payload.get("session_id") or "")
    if not _valid_id(session_id, "session"):
        session_id = _new_id("session")
    if not _allow_session(session_id):
        raise HTTPException(status_code=429, detail="rate_limited", headers={"Retry-After": "60"})
    message_id = str(payload.get("message_id") or "")
    if not _valid_id(message_id, "message"):
        message_id = _new_id("message")
    job_id = _new_id("job")
    event_id = f"event_{message_id}"
    event = {
        "specversion": "1.0",
        "id": event_id,
        "source": "astera://github-private/customer-ai-facade",
        "type": "customer.ai.message.requested",
        "subject": f"job/{job_id}",
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "datacontenttype": "application/json",
        "data": {
            "job_id": job_id,
            "message": {
                "session_id": session_id,
                "message_id": message_id,
                "message": message,
                "locale": str(payload.get("locale") or "ja-JP"),
                "source": "astera-hp",
                "response_mode": response_mode,
                "mode_source": mode_source,
                "current_path": _normalize_path(payload.get("current_path")),
            },
        },
    }
    accepted = await _accept_event(event)
    if accepted.status_code >= 400:
        raise HTTPException(status_code=502, detail="runtime_accept_failed")
    result = await _process_job(job_id)
    return {
        "ok": True,
        "job_id": job_id,
        "session_id": session_id,
        "message_id": message_id,
        "status": str(result.get("status") or "completed"),
        "answer": str(result.get("answer") or ""),
        "clarification": str(result.get("clarification") or ""),
        "public_source": result.get("public_source") or result.get("public_sources"),
        "routing": result.get("routing") or {"response_mode": response_mode, "mode_source": mode_source, "current_path": _normalize_path(payload.get("current_path"))},
    }


@app.delete("/public/customer-ai/sessions/{session_id}")
async def delete_public_session(session_id: str) -> dict[str, Any]:
    if not _valid_id(session_id, "session"):
        raise HTTPException(status_code=422, detail="session_id_invalid")
    if not _configured():
        raise HTTPException(status_code=503, detail="customer_ai_runtime_not_configured")
    event = {
        "specversion": "1.0",
        "id": _new_id("event"),
        "source": "astera://github-private/customer-ai-facade",
        "type": "customer.ai.session.delete.requested",
        "subject": f"session/{session_id}",
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "datacontenttype": "application/json",
        "data": {"session_id": session_id, "source": "astera-hp"},
    }
    response = await _accept_event(event)
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="runtime_session_delete_failed")
    RATE_BUCKETS.pop(session_id, None)
    return {"ok": True, "session_id": session_id, "status": "deleted"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860, log_level="info")
