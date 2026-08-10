from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import public_app
from runtime.schemas import MessagePayload


def payload(**changes) -> MessagePayload:
    values = {
        "session_id": "session_public_123456",
        "message_id": "message_public_123456",
        "message": "料金を教えて",
        "locale": "ja-JP",
        "source": "astera-hp",
        "response_mode": "billing",
        "mode_source": "selected",
        "current_path": "/ja/",
    }
    values.update(changes)
    return MessagePayload(**values)


def request(origin: str = "https://asterav8.jp") -> Request:
    headers = [(b"origin", origin.encode())] if origin else []
    return Request({"type": "http", "method": "POST", "path": "/respond", "headers": headers})


@pytest.mark.asyncio
async def test_response_is_synchronous_and_returns_public_contract(monkeypatch):
    async def fake_run_pipeline(job_id, message):
        assert job_id.startswith("hp_")
        assert message.response_mode == "billing"
        return {
            "status": "completed",
            "answer": "公開回答",
            "clarification": None,
            "context_used": True,
            "routing": {"active_topic": "billing"},
            "analysis": {"private": "must-not-leak"},
        }

    monkeypatch.setattr(public_app.service, "_run_pipeline", fake_run_pipeline)
    public_app._rate_buckets.clear()
    result = await public_app.respond(payload(), request())
    assert result == {
        "status": "completed",
        "session_id": "session_public_123456",
        "message_id": "message_public_123456",
        "answer": "公開回答",
        "clarification": None,
        "context_used": True,
        "routing": {"active_topic": "billing"},
    }
    assert "analysis" not in result


@pytest.mark.asyncio
async def test_response_rejects_non_hp_source():
    with pytest.raises(HTTPException) as error:
        await public_app.respond(payload(source="astera-api"), request())
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_response_rejects_unknown_origin():
    with pytest.raises(HTTPException) as error:
        await public_app.respond(payload(), request("https://example.invalid"))
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_session_delete_clears_runtime_context(monkeypatch):
    deleted = []
    monkeypatch.setattr(
        public_app.service.conversations,
        "delete",
        lambda session_id: deleted.append(session_id) or True,
    )
    result = await public_app.delete_session("session_public_123456", request())
    assert result["ok"] is True
    assert result["status"] == "deleted"
    assert result["deleted"] is True
    assert deleted == ["session_public_123456"]
