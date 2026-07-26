from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .bots import BotContract, RoutineBotSupervisor
from .config import Settings
from .control import ControlledExecutionCore
from .kb import KBIndex
from .model import ControlledLanguageEngine
from .notion import NotionClient
from .schemas import CloudEvent, JobRecord, JobResult, MessagePayload
from .security import canonical_json, redact_text, sanitize_structure, sign_hmac, validate_identifier
from .skills import build_default_registry
from .storage import ConflictError, JobStore
from .v8 import V8Supervisor

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
            "source": "customer-ai://hf-runtime",
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
        self.engine = ControlledLanguageEngine(self.settings)
        self.skills = build_default_registry()
        self.control = ControlledExecutionCore(v8=self.v8, engine=self.engine, skills=self.skills)
        self.gateway = GatewayClient(self.settings)
        self.notion = NotionClient(self.settings.notion_token, self.settings.notion_data_source_id)
        self.bots = RoutineBotSupervisor()
        self._process_semaphore = asyncio.Semaphore(self.settings.process_concurrency)
        self._register_bots()

    def _register_bots(self) -> None:
        self.bots.register(
            BotContract(
                bot_id="$bot.customer-ai.recovery",
                interval_seconds=self.settings.recovery_bot_interval_seconds,
                purpose="Recover stale jobs and request deterministic requeue through the existing Gateway.",
                side_effect="network",
            ),
            self.recover_once,
        )
        self.bots.register(
            BotContract(
                bot_id="$bot.customer-ai.question-insight",
                interval_seconds=self.settings.insight_bot_interval_seconds,
                purpose="Aggregate sanitized question-insight records for KB maintenance without an AI call.",
                side_effect="write",
            ),
            self.aggregate_insights_once,
        )
        if self.settings.enable_kb_sync_bot:
            self.bots.register(
                BotContract(
                    bot_id="$bot.customer-ai.kb-sync",
                    interval_seconds=self.settings.kb_sync_bot_interval_seconds,
                    purpose="Synchronize confirmed Notion KB pages into a versioned local SQLite snapshot.",
                    side_effect="network",
                ),
                self.sync_notion_kb_auto,
            )

    async def startup(self) -> None:
        try:
            await self.v8.start()
        except Exception as exc:
            LOG.warning("V8 startup degraded: %s", exc)
        self.kb.open()
        await self.bots.start()

    async def shutdown(self) -> None:
        await self.bots.stop()
        await self.v8.stop()

    async def sync_notion_kb(self, version: str) -> dict[str, Any]:
        pages = await self.notion.fetch_pages()
        info = await asyncio.to_thread(self.kb.build_snapshot, version=version, pages=pages)
        self.kb.open()
        result = {"version": info.version, "path": str(info.path), "source_pages": len(pages)}
        await self.gateway.emit("customer.ai.kb.update.applied", f"kb/{version}", result)
        return result

    async def sync_notion_kb_auto(self) -> dict[str, Any]:
        if not self.settings.notion_token or not self.settings.notion_data_source_id:
            return {"skipped": True, "reason": "notion_not_configured"}
        version = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return await self.sync_notion_kb(version)

    async def aggregate_insights_once(self) -> dict[str, Any]:
        counts: Counter[str] = Counter()
        scanned = 0
        for insight_path in self.settings.data_root.glob("jobs/*/*/insight.json"):
            try:
                row = json.loads(insight_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            counts[str(row.get("classification") or "unknown")] += 1
            scanned += 1
        result = {
            "generated_at": datetime.now(UTC).isoformat(),
            "scanned": scanned,
            "classifications": dict(sorted(counts.items())),
        }
        self.jobs.store.put_json(self.settings.data_root / "bots" / "question-insight-summary.json", result)
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
            await self.gateway.emit("customer.ai.job.requeue.requested", f"job/{record.job_id}", {"job_id": record.job_id})
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
            with self.jobs.store.lease(self.jobs.job_dir(job_id) / "lease.json", f"processor:{job_id}", self.settings.job_lease_seconds):
                existing = self.jobs.get_result(job_id)
                if existing:
                    return existing
                request = self.jobs.get_request(job_id)
                self.jobs.update_job(job_id, status="processing", stage="controlled_execution")
                session_lease_path = self.jobs.sessions_root / request.session_id / "lease.json"
                try:
                    with self.jobs.store.lease(session_lease_path, job_id, self.settings.session_lease_seconds):
                        result = await self._run_pipeline(job_id, request)
                except ConflictError:
                    self.jobs.update_job(job_id, status="retrying", stage="session_busy")
                    return {"job_id": job_id, "status": "retrying", "retry_after": 2}
                self.jobs.save_result(job_id, result)
                self.jobs.update_job(job_id, status=result["status"], stage=result["status"])
                await self.gateway.emit("customer.ai.response.completed", f"job/{job_id}", result)
                return result

    async def _run_pipeline(self, job_id: str, request: MessagePayload) -> dict[str, Any]:
        session = self.jobs.get_session_state(request.session_id)
        outcome = await self.control.execute(job_id=job_id, request=request, session=session, search=self.kb.search)
        self.jobs.append_session_state(request.session_id, job_id, outcome.state)
        self.jobs.save_insight(job_id, outcome.insight)
        base = JobResult(
            job_id=job_id,
            session_id=request.session_id,
            status=outcome.status,
            answer=outcome.answer,
            kb_ids=outcome.kb_ids,
            ai_invoked=outcome.engine_invoked,
            clarification=outcome.clarification,
            facts=outcome.facts,
        ).model_dump(mode="json")
        return base | {"violations": outcome.violations, "insight": outcome.insight, "execution": outcome.execution}

    def readiness(self) -> dict[str, Any]:
        return {
            "data_root": self.settings.data_root.exists() and os_access(self.settings.data_root),
            "v8": bool(self.v8.process and self.v8.process.returncode is None),
            "kb": self.kb.current() is not None,
            "model_enabled": self.settings.enable_model,
            "model_revision_pinned": bool(self.settings.model_revision),
            "active_structured_skills": self.skills.active_ids(),
            "routine_bots": self.bots.status(),
            "control_core": "$controlled-execution-core-derived",
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
