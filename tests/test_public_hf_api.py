from __future__ import annotations

import os

os.environ.setdefault("CUSTOMER_AI_DATA_ROOT", "/tmp/astera-customer-ai-public-test")

import pytest
from fastapi import HTTPException

import public_app
from runtime.schemas import MessagePayload


def payload(**changes) -> MessagePayload:
    values = {
        "session_id": "session_public_123456",
        "message_id": "message_public_123456",
        "message": "Asteraについて教えて",
        "locale": "ja-JP",
        "source": "astera-hp",
        "response_mode": "technical",
        "mode_source": "selected",
        "current_path": "/ja/developer/",
    }
    values.update(changes)
    return MessagePayload(**values)


@pytest.mark.asyncio
async def test_public_response_is_synchronous_and_returns_only_public_contract(monkeypatch):
    async def fake_run_pipeline(job_id, request):
        assert job_id.startswith("public_")
        assert request.response_mode == "technical"
        assert request.current_path == "/ja/developer/"
        return {
            "status": "completed",
            "answer": "公開回答",
            "clarification": None,
            "context_used": True,
            "routing": {"active_topic": "technical"},
            "analysis": {"private": "must-not-leak"},
            "blueprint": {"private": "must-not-leak"},
        }

    monkeypatch.setattr(public_app.service, "_run_pipeline", fake_run_pipeline)
    public_app._RATE_BUCKETS.clear()
    result = await public_app.public_respond(payload())
    assert result == {
        "status": "completed",
        "session_id": "session_public_123456",
        "answer": "公開回答",
        "clarification": None,
        "context_used": True,
        "routing": {"active_topic": "technical"},
    }
    assert "analysis" not in result
    assert "blueprint" not in result


@pytest.mark.asyncio
async def test_public_response_rejects_non_hp_source():
    with pytest.raises(HTTPException) as error:
        await public_app.public_respond(payload(source="astera-api"))
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_public_session_delete_clears_runtime_context(monkeypatch):
    deleted = []
    monkeypatch.setattr(public_app.service.conversations, "delete", lambda session_id: deleted.append(session_id) or True)
    result = await public_app.delete_public_session("session_public_123456")
    assert result["ok"] is True
    assert result["deleted"] is True
    assert deleted == ["session_public_123456"]
