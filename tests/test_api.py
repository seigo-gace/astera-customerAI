from __future__ import annotations

import importlib
import time
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from runtime.security import canonical_json, sign_hmac


def test_accept_rejects_bad_signature(data_root, monkeypatch):
    import app as app_module

    importlib.reload(app_module)
    with TestClient(app_module.app) as client:
        response = client.post("/internal/customer-ai/accept", content=b"{}", headers={"content-type": "application/json"})
        assert response.status_code == 401


def test_accepts_valid_event(data_root, monkeypatch):
    import app as app_module

    importlib.reload(app_module)
    event = {
        "specversion": "1.0",
        "id": "event_12345678",
        "source": "astera://cloudflare/customer-ai",
        "type": "customer.ai.message.requested",
        "subject": "job/job_12345678",
        "time": datetime.now(UTC).isoformat(),
        "datacontenttype": "application/json",
        "data": {
            "job_id": "job_12345678",
            "message": {
                "session_id": "session_12345678",
                "message_id": "message_12345678",
                "message": "hello",
                "locale": "en",
                "source": "astera-app",
            },
        },
    }
    body = canonical_json(event)
    timestamp = str(int(time.time()))
    signature = sign_hmac(body, timestamp, "test-secret")
    with TestClient(app_module.app) as client:
        response = client.post(
            "/internal/customer-ai/accept",
            content=body,
            headers={"content-type": "application/cloudevents+json", "x-webhook-timestamp": timestamp, "x-webhook-signature": signature},
        )
        assert response.status_code == 202
        assert response.json()["accepted"] is True
