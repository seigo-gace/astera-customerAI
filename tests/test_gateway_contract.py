from __future__ import annotations

import json

import pytest

from runtime.config import Settings
from runtime.security import verify_standard_webhook
from runtime.service import GatewayClient


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

    async def post(self, url: str, *, content: bytes, headers: dict[str, str]):
        self.__class__.captured = {
            "url": url,
            "content": content,
            "headers": headers,
            "timeout": self.timeout,
        }
        return FakeResponse()


@pytest.mark.asyncio
async def test_gateway_callback_uses_standard_webhooks(data_root, monkeypatch):
    monkeypatch.setenv("GATEWAY_CALLBACK_URL", "https://gateway.example.test/ingress/customer-ai-result")
    monkeypatch.setenv("GATEWAY_CALLBACK_SECRET", "base64:Y3VzdG9tZXItYWktcmVzdWx0LXNlY3JldA==")
    monkeypatch.setattr("runtime.service.httpx.AsyncClient", FakeAsyncClient)

    settings = Settings.load()
    client = GatewayClient(settings)
    await client.emit(
        "customer.ai.response.completed",
        "job/job_12345678",
        {"job_id": "job_12345678", "status": "completed", "answer": "ok"},
    )

    captured = FakeAsyncClient.captured
    headers = captured["headers"]
    assert captured["url"].endswith("/ingress/customer-ai-result")
    assert headers["content-type"] == "application/cloudevents+json"
    assert headers["webhook-event"] == "customer.ai.response.completed"
    assert verify_standard_webhook(
        captured["content"],
        headers["webhook-id"],
        headers["webhook-timestamp"],
        headers["webhook-signature"],
        settings.gateway_callback_secret,
    )
    event = json.loads(captured["content"])
    assert event["source"] == "customer-ai://hf-runtime"
    assert event["type"] == "customer.ai.response.completed"
    assert event["subject"] == "job/job_12345678"
