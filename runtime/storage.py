from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from .schemas import JobRecord, MessagePayload
from .security import canonical_json


class ConflictError(RuntimeError):
    pass


class NotFoundError(RuntimeError):
    pass


@dataclass(slots=True)
class Lease:
    path: Path
    owner: str
    expires_at: datetime


class AtomicStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _write_atomic(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def create_once(self, path: Path, data: bytes) -> bool:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return False
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return True

    def put_json(self, path: Path, value: Any) -> None:
        self._write_atomic(path, canonical_json(value))

    def get_json(self, path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise NotFoundError(str(path)) from exc

    @contextmanager
    def lease(self, path: Path, owner: str, ttl_seconds: int) -> Iterator[Lease]:
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=ttl_seconds)
        payload = {"owner": owner, "heartbeat_at": now.isoformat(), "expires_at": expires.isoformat()}
        created = self.create_once(path, canonical_json(payload))
        if not created:
            current = self.get_json(path)
            current_expiry = datetime.fromisoformat(current["expires_at"])
            if current_expiry > now:
                raise ConflictError(f"lease owned by {current.get('owner')}")
            stale = path.with_suffix(f".stale.{int(time.time())}.json")
            try:
                os.replace(path, stale)
            except FileNotFoundError:
                pass
            if not self.create_once(path, canonical_json(payload)):
                raise ConflictError("lease was concurrently recovered")
        lease = Lease(path=path, owner=owner, expires_at=expires)
        try:
            yield lease
        finally:
            try:
                current = self.get_json(path)
                if current.get("owner") == owner:
                    path.unlink(missing_ok=True)
            except NotFoundError:
                pass

    def heartbeat(self, lease: Lease, ttl_seconds: int) -> None:
        current = self.get_json(lease.path)
        if current.get("owner") != lease.owner:
            raise ConflictError("lease ownership changed")
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=ttl_seconds)
        self.put_json(
            lease.path,
            {"owner": lease.owner, "heartbeat_at": now.isoformat(), "expires_at": expires.isoformat()},
        )
        lease.expires_at = expires


class JobStore:
    def __init__(self, root: Path):
        self.store = AtomicStore(root)
        self.jobs_root = root / "jobs"
        self.sessions_root = root / "sessions"

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_root / job_id[:2] / job_id

    def accept(self, *, job_id: str, event_id: str, payload: MessagePayload) -> tuple[JobRecord, bool]:
        job_dir = self.job_dir(job_id)
        request_data = payload.model_dump(mode="json")
        request_hash = hashlib.sha256(canonical_json(request_data)).hexdigest()
        created = self.store.create_once(job_dir / "request.json", canonical_json(request_data))
        if not created:
            existing_hash = hashlib.sha256((job_dir / "request.json").read_bytes()).hexdigest()
            if existing_hash != request_hash:
                raise ConflictError("job id reused with different payload")
            return JobRecord.model_validate(self.store.get_json(job_dir / "status.json")), False
        record = JobRecord.new(job_id=job_id, event_id=event_id, payload=payload, request_hash=request_hash)
        self.store.put_json(job_dir / "status.json", record.model_dump(mode="json"))
        self.store.create_once(
            self.sessions_root / payload.session_id / "events" / f"{payload.message_id}.json",
            canonical_json({"type": "message", "job_id": job_id, **request_data}),
        )
        return record, True

    def get_job(self, job_id: str) -> JobRecord:
        return JobRecord.model_validate(self.store.get_json(self.job_dir(job_id) / "status.json"))

    def get_request(self, job_id: str) -> MessagePayload:
        return MessagePayload.model_validate(self.store.get_json(self.job_dir(job_id) / "request.json"))

    def update_job(self, job_id: str, **changes: Any) -> JobRecord:
        record = self.get_job(job_id)
        data = record.model_dump()
        data.update(changes)
        data["updated_at"] = datetime.now(UTC)
        updated = JobRecord.model_validate(data)
        self.store.put_json(self.job_dir(job_id) / "status.json", updated.model_dump(mode="json"))
        return updated

    def save_result(self, job_id: str, result: dict[str, Any]) -> None:
        self.store.put_json(self.job_dir(job_id) / "result.json", result)

    def get_result(self, job_id: str) -> dict[str, Any] | None:
        path = self.job_dir(job_id) / "result.json"
        return self.store.get_json(path) if path.exists() else None

    def save_insight(self, job_id: str, insight: dict[str, Any]) -> None:
        self.store.put_json(self.job_dir(job_id) / "insight.json", insight)

    def append_session_state(self, session_id: str, job_id: str, state: dict[str, Any]) -> None:
        session_dir = self.sessions_root / session_id
        self.store.create_once(
            session_dir / "events" / f"state-{job_id}.json",
            canonical_json({"type": "state", "job_id": job_id, "state": state}),
        )
        self.store.put_json(session_dir / "state.json", state)

    def get_session_state(self, session_id: str) -> dict[str, Any]:
        path = self.sessions_root / session_id / "state.json"
        if not path.exists():
            return {}
        return self.store.get_json(path)
