from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .config import Settings
from .control import ConversationCore
from .conversation import ConversationCache
from .kb import KBIndex
from .model import ConversationLanguageEngine
from .notion import NotionClient
from .schemas import CloudEvent, JobRecord, JobResult, MessagePayload
from .security import canonical_json, redact_text, sanitize_structure, validate_identifier
from .storage import ConflictError, JobStore
from .support import FeedbackStore
from .v8 import V8Supervisor


LOGGER = logging.getLogger(__name__)
SUPPORT_PIPELINE = "astera-customerai-master-v2-kb-only"


class InternalEventApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def emit(self, event_type: str, subject: str, data: dict[str, Any]) -> None:
        if not (
            self.settings.internal_event_api_url
            and self.settings.internal_event_api_token
            and self.settings.internal_event_result_destination_id
        ):
            return
        sanitized = sanitize_structure(data)
        event_id = "evt_" + hashlib.sha256(
            f"{event_type}:{subject}:{canonical_json(sanitized).hex()}".encode()
        ).hexdigest()[:32]
        payload = {
            "eventId": event_id,
            "eventType": event_type,
            "sourceId": self.settings.internal_event_source_id,
            "destinationId": self.settings.internal_event_result_destination_id,
            "subject": subject,
            "time": datetime.now(UTC).isoformat(),
            "data": sanitized,
        }
        headers = {
            "authorization": f"Bearer {self.settings.internal_event_api_token}",
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(
            timeout=self.settings.internal_event_api_timeout_seconds
        ) as client:
            response = await client.post(
                self.settings.internal_event_api_url,
                json=payload,
                headers=headers,
            )
            response.raise_for_status()


class CustomerAIService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.load()
        self.settings.ensure_directories()
        self.jobs = JobStore(self.settings.data_root)
        self.kb = KBIndex(
            self.settings.data_root,
            cache_ttl_seconds=self.settings.kb_cache_ttl_seconds,
            cache_max_entries=self.settings.kb_cache_max_entries,
        )
        self.v8 = V8Supervisor(self.settings)
        self.engine = ConversationLanguageEngine(self.settings)
        self.conversations = ConversationCache(
            self.settings.data_root,
            ttl_seconds=self.settings.session_cache_ttl_seconds,
            max_sessions=self.settings.session_cache_max_sessions,
            max_turns=self.settings.session_cache_max_turns,
        )
        self.feedback = FeedbackStore(self.settings.data_root)
        self.core = ConversationCore(
            v8=self.v8,
            engine=self.engine,
            cache=self.conversations,
            search=self.kb.search,
            feedback_store=self.feedback,
        )
        self.events = InternalEventApiClient(self.settings)
        self.notion = NotionClient(
            self.settings.notion_token, self.settings.notion_data_source_id
        )
        self._process_semaphore = asyncio.Semaphore(self.settings.process_concurrency)
        self._v8_startup_error = ""

    async def startup(self) -> None:
        try:
            await self.v8.start()
            self._v8_startup_error = ""
            LOGGER.info(
                "CUSTOMER_AI_V8_READY node=%s socket=%s",
                self.v8.node_binary,
                self.v8.socket_path,
            )
        except Exception as error:
            self._v8_startup_error = f"{type(error).__name__}:{error}"
            LOGGER.exception("CUSTOMER_AI_V8_STARTUP_FAILED")
        self.kb.open()

    async def shutdown(self) -> None:
        await self.v8.stop()

    async def sync_notion_kb(self, version: str) -> dict[str, Any]:
        pages = await self.notion.fetch_pages()
        info = await asyncio.to_thread(
            self.kb.build_snapshot, version=version, pages=pages
        )
        self.kb.open()
        result = {
            "version": info.version,
            "path": str(info.path),
            "source_pages": len(pages),
        }
        await self.events.emit(
            "customer.ai.kb.update.applied", f"kb/{version}", result
        )
        return result

    async def recover_once(self) -> dict[str, Any]:
        recovered: list[str] = []
        now = datetime.now(UTC)
        for status_path in self.settings.data_root.glob("jobs/*/*/status.json"):
            try:
                record = JobRecord.model_validate_json(
                    status_path.read_text(encoding="utf-8")
                )
            except Exception:
                continue
            age = (now - record.updated_at).total_seconds()
            should_requeue = (
                record.status in {"accepted", "queued_processing", "retrying"}
                and age > 60
            )
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
            self.jobs.update_job(
                record.job_id, status="retrying", stage="recovery_requeue"
            )
            await self.events.emit(
                "customer.ai.job.requeue.requested",
                f"job/{record.job_id}",
                {"job_id": record.job_id},
            )
            recovered.append(record.job_id)
        return {"recovered": recovered, "count": len(recovered)}

    async def accept(self, event: CloudEvent) -> tuple[dict[str, Any], bool]:
        if event.type != "customer.ai.message.requested":
            raise ValueError("unsupported_event_type")
        payload = MessagePayload.model_validate(event.data["message"])
        if len(payload.message) > self.settings.max_input_chars:
            raise ValueError("message_too_large")
        redacted = redact_text(payload.message)
        payload = payload.model_copy(update={"message": redacted.text})
        validate_identifier(event.id, field="event_id")
        job_id = str(
            event.data.get("job_id")
            or "job_" + hashlib.sha256(event.id.encode()).hexdigest()[:32]
        )
        validate_identifier(job_id, field="job_id")
        record, created = self.jobs.accept(
            job_id=job_id, event_id=event.id, payload=payload
        )
        await self.events.emit(
            "customer.ai.job.accepted",
            f"job/{job_id}",
            {
                "job_id": job_id,
                "created": created,
                "redaction_kinds": redacted.kinds,
            },
        )
        return record.model_dump(mode="json"), created

    async def process_job(self, job_id: str) -> dict[str, Any]:
        existing = self.jobs.get_result(job_id)
        if existing:
            return existing
        async with self._process_semaphore:
            with self.jobs.store.lease(
                self.jobs.job_dir(job_id) / "lease.json",
                f"processor:{job_id}",
                self.settings.job_lease_seconds,
            ):
                existing = self.jobs.get_result(job_id)
                if existing:
                    return existing
                request = self.jobs.get_request(job_id)
                self.jobs.update_job(
                    job_id, status="processing", stage="support_preparation"
                )
                session_lease_path = (
                    self.jobs.sessions_root / request.session_id / "lease.json"
                )
                try:
                    with self.jobs.store.lease(
                        session_lease_path,
                        job_id,
                        self.settings.session_lease_seconds,
                    ):
                        result = await self._run_pipeline(job_id, request)
                except ConflictError:
                    self.jobs.update_job(
                        job_id, status="retrying", stage="session_busy"
                    )
                    return {"job_id": job_id, "status": "retrying", "retry_after": 2}
                self.jobs.save_result(job_id, result)
                self.jobs.update_job(
                    job_id, status=result["status"], stage=result["status"]
                )
                await self.events.emit(
                    "customer.ai.response.completed", f"job/{job_id}", result
                )
                return result

    async def _run_pipeline(
        self, job_id: str, request: MessagePayload
    ) -> dict[str, Any]:
        outcome = await self.core.execute(request=request)
        return JobResult(
            job_id=job_id,
            session_id=request.session_id,
            status=outcome.status,
            answer=outcome.answer,
            kb_ids=outcome.kb_ids,
            ai_invoked=outcome.engine_invoked,
            clarification=outcome.clarification,
            facts=outcome.facts,
            context_used=outcome.context_used,
        ).model_dump(mode="json") | {
            "analysis": outcome.analysis,
            "violations": outcome.violations,
            "processing_grade": outcome.processing_grade,
            "question_tasks": outcome.question_tasks,
            "blueprint": outcome.blueprint,
            "repair_attempted": outcome.repair_attempted,
            "feedback_candidate_id": outcome.feedback_candidate_id,
            "execution": outcome.execution,
        }

    def readiness(self) -> dict[str, Any]:
        return {
            "data_root": self.settings.data_root.exists()
            and os_access(self.settings.data_root),
            "v8": bool(self.v8.process and self.v8.process.returncode is None),
            "v8_startup_error": self._v8_startup_error,
            "kb": self.kb.current() is not None,
            "model_enabled": self.settings.enable_model,
            "model_revision_pinned": bool(self.settings.model_revision),
            "conversation_cache": self.conversations.status(),
            "kb_cache": self.kb.cache_status(),
            "feedback_store": self.feedback.root.exists(),
            "internal_event_api": {
                "configured": bool(
                    self.settings.internal_event_api_url
                    and self.settings.internal_event_api_token
                    and self.settings.internal_event_result_destination_id
                ),
                "source_id": self.settings.internal_event_source_id,
                "result_destination_id": self.settings.internal_event_result_destination_id,
            },
            "support_pipeline": SUPPORT_PIPELINE,
        }


def os_access(path: Path) -> bool:
    try:
        probe = path / "temporary" / ".ready-probe"
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False
