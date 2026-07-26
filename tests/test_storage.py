from pathlib import Path

import pytest

from runtime.schemas import MessagePayload
from runtime.storage import ConflictError, JobStore


def payload(message: str = "hello") -> MessagePayload:
    return MessagePayload(session_id="session_123", message_id="message_123", message=message, locale="en", source="astera-app")


def test_accept_is_idempotent(tmp_path: Path):
    store = JobStore(tmp_path)
    first, created = store.accept(job_id="job_12345678", event_id="event_12345678", payload=payload())
    second, created_again = store.accept(job_id="job_12345678", event_id="event_12345678", payload=payload())
    assert created is True
    assert created_again is False
    assert first.request_hash == second.request_hash


def test_job_id_reuse_with_different_payload_is_rejected(tmp_path: Path):
    store = JobStore(tmp_path)
    store.accept(job_id="job_12345678", event_id="event_12345678", payload=payload())
    with pytest.raises(ConflictError):
        store.accept(job_id="job_12345678", event_id="event_12345678", payload=payload("different"))


def test_lease_excludes_second_owner(tmp_path: Path):
    store = JobStore(tmp_path)
    lease_path = tmp_path / "lease.json"
    with store.store.lease(lease_path, "one", 30):
        with pytest.raises(ConflictError):
            with store.store.lease(lease_path, "two", 30):
                pass
