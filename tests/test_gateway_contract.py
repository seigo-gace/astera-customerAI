from __future__ import annotations

import pytest

from runtime.config import Settings
from runtime.service import InternalEventApiClient


class FakeResponse:
    def raise_for_status(self) -> None:
        return None


class FakeAsyncClient:
    captured: dict = {}

    def __init__(self, *, timeout: int):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, *, json: dict, headers: dict[str, str]):
        self.__class__.captured = {
            "url": url,
            "json": json,
            "headers": headers,
            "timeout": self.timeout,
        }
        return FakeResponse()


@pytest.mark.asyncio
async def test_result_callback_uses_generic_internal_event_api(data_root, monkeypatch):
    monkeypatch.setenv(
        "INTERNAL_EVENT_API_URL", "https://gateway.example.test/internal/events"
    )
    monkeypatch.setenv("INTERNAL_EVENT_API_TOKEN", "internal-api-token")
    monkeypatch.setenv("INTERNAL_EVENT_SOURCE_ID", "hf-private-runtime")
    monkeypatch.setenv("INTERNAL_EVENT_RESULT_DESTINATION_ID", "app-receiver")
    monkeypatch.setattr("runtime.service.httpx.AsyncClient", FakeAsyncClient)

    settings = Settings.load()
    client = InternalEventApiClient(settings)
    await client.emit(
        "customer.ai.response.completed",
        "job/job_12345678",
        {"job_id": "job_12345678", "status": "completed", "answer": "ok"},
    )

    captured = FakeAsyncClient.captured
    headers = captured["headers"]
    payload = captured["json"]
    assert captured["url"].endswith("/internal/events")
    assert headers["content-type"] == "application/json"
    assert headers["authorization"] == "Bearer internal-api-token"
    assert payload["sourceId"] == "hf-private-runtime"
    assert payload["destinationId"] == "app-receiver"
    assert payload["eventType"] == "customer.ai.response.completed"
    assert payload["subject"] == "job/job_12345678"
    assert payload["data"]["answer"] == "ok"
    assert payload["eventId"].startswith("evt_")
