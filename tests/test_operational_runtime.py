from __future__ import annotations

import importlib
import json
import time
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from runtime.security import canonical_json, sign_hmac, sign_standard_webhook
from tests.test_story_runtime import story_pages


PIPELINE_NAME = "astera-customerai-master-v2-kb-only"


def internal_signed_headers(
    body: bytes,
    secret: str = "test-secret",
    *,
    content_type: str = "application/json",
) -> dict[str, str]:
    timestamp = str(int(time.time()))
    return {
        "content-type": content_type,
        "x-webhook-timestamp": timestamp,
        "x-webhook-signature": sign_hmac(body, timestamp, secret),
    }


def gateway_signed_headers(
    body: bytes,
    secret: str = "test-secret",
    *,
    webhook_id: str = "wh_customer_ai_delivery_0001",
    event_type: str = "customer.ai.message.requested",
) -> dict[str, str]:
    timestamp = str(int(time.time()))
    return {
        "content-type": "application/json",
        "webhook-id": webhook_id,
        "webhook-timestamp": timestamp,
        "webhook-signature": sign_standard_webhook(
            body,
            webhook_id,
            timestamp,
            secret,
        ),
        "webhook-event": event_type,
        "x-gace-destination": "customer-ai-hf",
    }


def event(*, index: int, session_id: str, message: str) -> dict:
    job_id = f"job_operational_{index:08d}"
    return {
        "specversion": "1.0",
        "id": f"event_operational_{index:08d}",
        "source": "astera://webhook-gateway/customer-ai",
        "type": "customer.ai.message.requested",
        "subject": f"job/{job_id}",
        "time": datetime.now(UTC).isoformat(),
        "datacontenttype": "application/json",
        "data": {
            "job_id": job_id,
            "message": {
                "session_id": session_id,
                "message_id": f"message_operational_{index:08d}",
                "message": message,
                "locale": "ja-JP",
                "source": "astera-app",
            },
        },
    }


def test_operational_signed_ingress_processing_persistence_and_follow_up(
    data_root,
    monkeypatch,
):
    del monkeypatch
    import app as app_module

    app_module = importlib.reload(app_module)
    session_id = "session_operational_restart"
    first_event = event(
        index=1,
        session_id=session_id,
        message="購入したクレジットが反映されません",
    )
    first_body = canonical_json(first_event)
    sync_body = canonical_json(
        {"version": "operational-v2", "pages": story_pages()}
    )

    with TestClient(app_module.app) as client:
        health = client.get("/healthz")
        ready_before_sync = client.get("/readyz")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}
        assert ready_before_sync.status_code == 503
        assert ready_before_sync.json()["checks"]["v8"] is True
        assert ready_before_sync.json()["checks"]["kb"] is False
        assert (
            ready_before_sync.json()["checks"]["support_pipeline"]
            == PIPELINE_NAME
        )

        bad_sync = client.post(
            "/internal/kb/sync",
            content=sync_body,
            headers={"content-type": "application/json"},
        )
        assert bad_sync.status_code == 401

        synced = client.post(
            "/internal/kb/sync",
            content=sync_body,
            headers=internal_signed_headers(sync_body),
        )
        assert synced.status_code == 202
        assert synced.json()["source_pages"] == len(story_pages())

        ready_after_sync = client.get("/readyz")
        assert ready_after_sync.status_code == 200
        assert ready_after_sync.json()["ready"] is True
        assert ready_after_sync.json()["checks"]["kb"] is True

        bad_accept = client.post(
            "/internal/customer-ai/accept",
            content=first_body,
            headers={"content-type": "application/json"},
        )
        assert bad_accept.status_code == 401

        accepted = client.post(
            "/internal/customer-ai/accept",
            content=first_body,
            headers=gateway_signed_headers(first_body),
        )
        assert accepted.status_code == 202
        assert accepted.json()["created"] is True
        job_id = first_event["data"]["job_id"]

        duplicate = client.post(
            "/internal/customer-ai/accept",
            content=first_body,
            headers=gateway_signed_headers(
                first_body,
                webhook_id="wh_customer_ai_delivery_0002",
            ),
        )
        assert duplicate.status_code == 202
        assert duplicate.json()["created"] is False

        unsigned_process = client.post(
            f"/internal/customer-ai/jobs/{job_id}/process",
            content=b"{}",
            headers={"content-type": "application/json"},
        )
        assert unsigned_process.status_code == 401

        process_body = b"{}"
        processed = client.post(
            f"/internal/customer-ai/jobs/{job_id}/process",
            content=process_body,
            headers=internal_signed_headers(process_body),
        )
        assert processed.status_code == 200
        processing_result = processed.json()
        assert processing_result["status"] == "completed"
        assert "決済状態" in processing_result["answer"]
        assert processing_result["execution"]["pipeline"] == PIPELINE_NAME

        stored = client.get(f"/internal/customer-ai/jobs/{job_id}")
        assert stored.status_code == 200
        assert stored.json()["job"]["status"] == "completed"
        assert stored.json()["result"]["answer"] == processing_result["answer"]

        repeated_process = client.post(
            f"/internal/customer-ai/jobs/{job_id}/process",
            content=process_body,
            headers=internal_signed_headers(process_body),
        )
        assert repeated_process.status_code == 200
        assert repeated_process.json() == stored.json()["result"]

        unknown = client.get(
            "/internal/customer-ai/jobs/job_operational_missing"
        )
        assert unknown.status_code == 404

    app_module = importlib.reload(app_module)
    follow_up_event = event(
        index=2,
        session_id=session_id,
        message="昨日の夜です。どこを確認すればいい？",
    )
    follow_up_body = canonical_json(follow_up_event)

    with TestClient(app_module.app) as restarted_client:
        restarted_ready = restarted_client.get("/readyz")
        assert restarted_ready.status_code == 200
        assert restarted_ready.json()["checks"]["kb"] is True

        restored = restarted_client.get(
            f"/internal/customer-ai/jobs/{first_event['data']['job_id']}"
        )
        assert restored.status_code == 200
        assert restored.json()["result"]["status"] == "completed"

        accepted_follow_up = restarted_client.post(
            "/internal/customer-ai/accept",
            content=follow_up_body,
            headers=gateway_signed_headers(
                follow_up_body,
                webhook_id="wh_customer_ai_delivery_0003",
            ),
        )
        assert accepted_follow_up.status_code == 202

        follow_up_process_body = b"{}"
        follow_up_processed = restarted_client.post(
            (
                "/internal/customer-ai/jobs/"
                f"{follow_up_event['data']['job_id']}/process"
            ),
            content=follow_up_process_body,
            headers=internal_signed_headers(follow_up_process_body),
        )
        assert follow_up_processed.status_code == 200
        follow_up_result = follow_up_processed.json()
        assert follow_up_result["status"] == "completed"
        assert follow_up_result["context_used"] is True
        assert "購入時刻" in follow_up_result["answer"]

        context_path = data_root / "sessions" / session_id / "context.json"
        context = json.loads(context_path.read_text(encoding="utf-8"))
        assert context["user_goal"] == "購入したクレジットが反映されません"
        assert context["active_topic"] == "credit"
        assert len(context["turns"]) == 4
        assert context["question_ledger"]
        assert context["last_blueprint"]["sections"]
