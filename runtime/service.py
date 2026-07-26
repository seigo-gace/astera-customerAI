from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from .config import Settings
from .kb import KBIndex
from .model import DialogueModel
from .notion import NotionClient
from .schemas import CloudEvent, JobRecord, JobResult, MessagePayload
from .security import (
    canonical_json,
    contains_internal_implementation,
    redact_text,
    safe_candidate_phrase,
    sanitize_structure,
    sign_hmac,
    validate_identifier,
)
from .storage import ConflictError, JobStore
from .v8 import V8Supervisor, V8Unavailable

LOG = logging.getLogger("customer-ai")


class GatewayClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def emit(self, event_type: str, subject: str, data: dict[str, Any]) -> None:
        if not self.settings.gateway_callback_url or not self.settings.gateway_callback_secret:
            return
        event = {
            "specversion": "1.0",
            "id": "evt_" + hashlib.sha256(f"{event_type}:{subject}:{canonical_json(data).hex()}".encode()).hexdigest()[:32],
            "source": "astera://customer-ai/hf",
            "type": event_type,
            "subject": subject,
            "time": datetime.now(UTC).isoformat(),
            "datacontenttype": "application/json",
            "data": sanitize_structure(data),
        }
        body = canonical_json(event)
        timestamp = str(int(time.time()))
        headers = {
            "content-type": "application/cloudevents+json",
            "x-webhook-timestamp": timestamp,
            "x-webhook-signature": sign_hmac(body, timestamp, self.settings.gateway_callback_secret),
        }
        async with httpx.AsyncClient(timeout=self.settings.gateway_timeout_seconds) as client:
            response = await client.post(self.settings.gateway_callback_url, content=body, headers=headers)
            response.raise_for_status()


class CustomerAIService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.load()
        self.settings.ensure_directories()
        self.jobs = JobStore(self.settings.data_root)
        self.kb = KBIndex(self.settings.data_root)
        self.v8 = V8Supervisor(self.settings)
        self.model = DialogueModel(self.settings)
        self.gateway = GatewayClient(self.settings)
        self.notion = NotionClient(self.settings.notion_token, self.settings.notion_data_source_id)
        self._process_semaphore = asyncio.Semaphore(self.settings.process_concurrency)
        self._recovery_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def startup(self) -> None:
        try:
            await self.v8.start()
        except Exception as exc:
            LOG.warning("V8 startup degraded: %s", exc)
        self.kb.open()
        self._stopping.clear()
        self._recovery_task = asyncio.create_task(self._recovery_loop(), name="customer-ai-recovery")

    async def shutdown(self) -> None:
        self._stopping.set()
        if self._recovery_task:
            self._recovery_task.cancel()
            await asyncio.gather(self._recovery_task, return_exceptions=True)
            self._recovery_task = None
        await self.v8.stop()


    async def sync_notion_kb(self, version: str) -> dict[str, Any]:
        pages = await self.notion.fetch_pages()
        info = await asyncio.to_thread(self.kb.build_snapshot, version=version, pages=pages)
        self.kb.open()
        result = {"version": info.version, "path": str(info.path), "source_pages": len(pages)}
        await self.gateway.emit("customer.ai.kb.update.applied", f"kb/{version}", result)
        return result

    async def recover_once(self) -> dict[str, Any]:
        recovered: list[str] = []
        now = datetime.now(UTC)
        for status_path in self.settings.data_root.glob("jobs/*/*/status.json"):
            try:
                record = JobRecord.model_validate_json(status_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            age = (now - record.updated_at).total_seconds()
            should_requeue = record.status in {"accepted", "queued_processing", "retrying"} and age > 60
            if record.status == "processing":
                lease_path = status_path.parent / "lease.json"
                if not lease_path.exists():
                    should_requeue = age > self.settings.job_lease_seconds
                else:
                    try:
                        lease = self.jobs.store.get_json(lease_path)
                        should_requeue = datetime.fromisoformat(lease["expires_at"]) <= now
                    except Exception:
                        should_requeue = True
            if not should_requeue:
                continue
            self.jobs.update_job(record.job_id, status="retrying", stage="recovery_requeue")
            await self.gateway.emit(
                "customer.ai.job.requeue.requested", f"job/{record.job_id}", {"job_id": record.job_id}
            )
            recovered.append(record.job_id)
        return {"recovered": recovered, "count": len(recovered)}

    async def _recovery_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.recover_once()
            except Exception as exc:
                LOG.warning("recovery scan failed: %s", exc)
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=60)
            except TimeoutError:
                continue

    async def accept(self, event: CloudEvent) -> tuple[dict[str, Any], bool]:
        if event.type != "customer.ai.message.requested":
            raise ValueError("unsupported_event_type")
        payload = MessagePayload.model_validate(event.data["message"])
        if len(payload.message) > self.settings.max_input_chars:
            raise ValueError("message_too_large")
        redacted = redact_text(payload.message)
        payload = payload.model_copy(update={"message": redacted.text})
        validate_identifier(event.id, field="event_id")
        job_id = str(event.data.get("job_id") or "job_" + hashlib.sha256(event.id.encode()).hexdigest()[:32])
        validate_identifier(job_id, field="job_id")
        record, created = self.jobs.accept(job_id=job_id, event_id=event.id, payload=payload)
        await self.gateway.emit(
            "customer.ai.job.accepted",
            f"job/{job_id}",
            {"job_id": job_id, "created": created, "redaction_kinds": redacted.kinds},
        )
        return record.model_dump(mode="json"), created

    async def process_job(self, job_id: str) -> dict[str, Any]:
        existing = self.jobs.get_result(job_id)
        if existing:
            return existing
        async with self._process_semaphore:
            with self.jobs.store.lease(
                self.jobs.job_dir(job_id) / "lease.json", f"processor:{job_id}", self.settings.job_lease_seconds
            ):
                existing = self.jobs.get_result(job_id)
                if existing:
                    return existing
                request = self.jobs.get_request(job_id)
                self.jobs.update_job(job_id, status="processing", stage="preprocess")
                session_lease_path = self.jobs.sessions_root / request.session_id / "lease.json"
                try:
                    with self.jobs.store.lease(
                        session_lease_path, job_id, self.settings.session_lease_seconds
                    ):
                        result = await self._run_pipeline(job_id, request)
                except ConflictError:
                    self.jobs.update_job(job_id, status="retrying", stage="session_busy")
                    return {"job_id": job_id, "status": "retrying", "retry_after": 2}
                self.jobs.save_result(job_id, result)
                self.jobs.update_job(job_id, status=result["status"], stage="completed")
                await self.gateway.emit("customer.ai.response.completed", f"job/{job_id}", result)
                return result

    async def _run_pipeline(self, job_id: str, request: MessagePayload) -> dict[str, Any]:
        session = self.jobs.get_session_state(request.session_id)
        try:
            phase_a = await self.v8.request(
                "preprocess", {"message": request.message, "locale": request.locale, "session": session}
            )
        except V8Unavailable:
            phase_a = fallback_preprocess(request.message, request.locale)
        query = str(phase_a.get("search_query") or request.message)
        hits = self.kb.search(query, limit=5)
        materials = [hit.model_dump() for hit in hits]
        try:
            phase_b = await self.v8.request(
                "plan",
                {
                    "message": request.message,
                    "locale": request.locale,
                    "session": session,
                    "preprocess": phase_a,
                    "kb": materials,
                },
            )
        except V8Unavailable:
            phase_b = fallback_plan(request.message, materials)

        answer = render_script_answer(request.locale, materials, phase_b)
        ai_invoked = False
        if phase_b.get("ai_required") and self.model.available():
            try:
                generated = await asyncio.to_thread(
                    self.model.generate,
                    {
                        "locale": request.locale,
                        "message": request.message,
                        "confirmed_kb": materials,
                        "plan": phase_b,
                        "prohibited_claims": [
                            "unverified payment or credit status",
                            "unverified action completion",
                            "internal implementation details",
                        ],
                    },
                )
                answer = generated["answer"]
                ai_invoked = True
            except Exception as exc:
                LOG.warning("model fallback for %s: %s", job_id, exc)

        try:
            phase_c = await self.v8.request(
                "verify",
                {
                    "answer": answer,
                    "message": request.message,
                    "locale": request.locale,
                    "kb": materials,
                    "plan": phase_b,
                },
            )
            answer = str(phase_c.get("answer") or answer)
            violations = phase_c.get("violations", [])
        except V8Unavailable:
            violations = []

        safe = redact_text(answer).text
        if contains_internal_implementation(safe):
            safe = "内部構成の詳細は公開していません。利用方法と問題解決に必要な範囲で案内します。"
        if not materials and not phase_b.get("clarification"):
            phase_b["clarification"] = (
                "確認できる情報が不足しています。どの画面・操作・エラーで起きたかを教えてください。"
                if request.locale == "ja-JP"
                else "I need a little more confirmed context. Which screen, operation, or error is involved?"
            )
        status = "awaiting_clarification" if phase_b.get("clarification") and not materials else "completed"
        if status == "awaiting_clarification":
            safe = str(phase_b["clarification"])

        state = {
            "topic": phase_a.get("intent", "unknown"),
            "intent": phase_a.get("intent", "unknown"),
            "confirmed_values": phase_a.get("entities", {}),
            "missing_values": phase_b.get("missing_values", []),
            "pending_action": phase_b.get("action"),
            "last_kb_ids": [hit.kb_id for hit in hits],
            "emotion": phase_a.get("emotion", "neutral"),
            "resolution": "pending_feedback" if status == "completed" else "unresolved",
        }
        self.jobs.append_session_state(request.session_id, job_id, state)
        insight = build_insight(request, hits, status, phase_a, phase_b)
        self.jobs.save_insight(job_id, insight)
        return JobResult(
            job_id=job_id,
            session_id=request.session_id,
            status=status,
            answer=safe,
            kb_ids=[hit.kb_id for hit in hits],
            ai_invoked=ai_invoked,
            clarification=phase_b.get("clarification"),
            facts=[hit.short_answer for hit in hits],
        ).model_dump(mode="json") | {"violations": violations, "insight": insight}

    def readiness(self) -> dict[str, Any]:
        return {
            "data_root": self.settings.data_root.exists() and os_access(self.settings.data_root),
            "v8": bool(self.v8.process and self.v8.process.returncode is None),
            "kb": self.kb.current() is not None,
            "model_enabled": self.settings.enable_model,
            "model_revision_pinned": bool(self.settings.model_revision),
        }


def os_access(path: Any) -> bool:
    try:
        probe = path / "temporary" / ".ready-probe"
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def fallback_preprocess(message: str, locale: str) -> dict[str, Any]:
    lowered = message.lower()
    intent = "credit" if any(word in lowered for word in ("credit", "クレジット", "残高")) else "general"
    emotion = "frustrated" if any(word in lowered for word in ("困", "怒", "反映され", "doesn't", "not working")) else "neutral"
    return {"intent": intent, "emotion": emotion, "entities": {}, "search_query": message}


def fallback_plan(message: str, materials: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ai_required": len(materials) > 1 or len(message) > 220,
        "clarification": None,
        "missing_values": [],
        "action": None,
    }


def render_script_answer(locale: str, materials: list[dict[str, Any]], plan: dict[str, Any]) -> str:
    if materials:
        primary = materials[0]
        detail = primary.get("body", "").strip()
        return primary.get("short_answer", "") + (f"\n\n{detail}" if detail else "")
    return (
        "確認できるKBが見つかりませんでした。状況を特定するため、対象の機能と現在表示されている内容を教えてください。"
        if locale == "ja-JP"
        else "I could not find a confirmed KB entry. Please tell me the feature and what is currently shown."
    )


def build_insight(request: MessagePayload, hits: list[Any], status: str, phase_a: dict[str, Any], phase_b: dict[str, Any]) -> dict[str, Any]:
    if not hits:
        classification = "missing_page"
    elif status == "awaiting_clarification":
        classification = "missing_follow_up"
    elif len(hits) > 1:
        classification = "known_composite"
    else:
        classification = "known_exact"
    normalized = " ".join(request.message.split())[:500]
    candidate_safe = safe_candidate_phrase(normalized)
    return {
        "classification": classification,
        "normalized_question": normalized,
        "intent": phase_a.get("intent"),
        "matched_kb_ids": [hit.kb_id for hit in hits],
        # Question-derived phrases are candidates only. They become Level A after resolved feedback
        # and a second deterministic validation pass; facts always require confirmed sources.
        "safe_auto_update_level": "candidate_A" if classification == "known_exact" and candidate_safe else "C",
        "suggested_search_term": normalized if classification == "known_exact" and candidate_safe else None,
        "requires_resolved_feedback": True,
        "requires_confirmed_source": classification not in {"known_exact", "known_composite"},
        "action": phase_b.get("action"),
    }
