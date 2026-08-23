from fastapi.testclient import TestClient

import app as app_module
from runtime.schemas import FinalResponse, ResolutionMode


class FakeWork:
    def __init__(self):
        self.deleted = []

    async def run(self, session_id: str, message: str) -> FinalResponse:
        return FinalResponse(
            request_id="req_test",
            session_id=session_id,
            turn_id="turn_test",
            answer=f"answer:{message}",
            answered_task_ids=["t1"],
            unresolved_task_ids=[],
            evidence_ids=["f1"],
            resolution_score=1.0,
            passed=True,
            resolution_mode=ResolutionMode.RESOLVED,
        )

    def delete_session(self, session_id: str) -> bool:
        self.deleted.append(session_id)
        return True


def test_health_and_not_ready_contract():
    app_module.set_work(None)
    with TestClient(app_module.app) as client:
        assert client.get("/health").status_code == 200
        ready = client.get("/ready").json()
        assert ready["status"] == "not_ready"
        assert ready["blocker"] in {
            "kb_snapshot_missing",
            "kb_build_id_missing",
            "hf_token_missing",
            "customer_ai_not_ready",
        }
        assert client.post(
            "/v1/customer-ai/messages",
            json={"session_id": "s", "message": "q"},
        ).status_code == 503


def test_public_respond_and_session_delete_contract():
    work = FakeWork()
    with TestClient(app_module.app) as client:
        app_module.set_work(work)
        response = client.post(
            "/respond",
            json={
                "message": "Asteraとは？",
                "source": "astera-hp",
                "locale": "ja-JP",
                "session_id": "session_test",
                "message_id": "message_test",
                "response_mode": "auto",
                "mode_source": "auto",
                "current_path": "/",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert body["answer"] == "answer:Asteraとは？"
        assert body["session_id"] == "session_test"
        assert body["message_id"] == "message_test"
        assert body["evidence_ids"] == ["f1"]

        deleted = client.delete("/sessions/session_test")
        assert deleted.status_code == 200
        assert deleted.json() == {"ok": True, "deleted": True}
        assert work.deleted == ["session_test"]
    app_module.set_work(None)


def test_public_message_limit_is_12000_chars():
    work = FakeWork()
    with TestClient(app_module.app) as client:
        app_module.set_work(work)
        response = client.post(
            "/respond",
            json={
                "message": "x" * 12001,
                "session_id": "s",
                "message_id": "m",
            },
        )
        assert response.status_code == 422
    app_module.set_work(None)


def test_default_cors_allows_hp_web_app_and_native_origins():
    with TestClient(app_module.app) as client:
        for origin in (
            "https://asterav8.jp",
            "https://staging.asterav8.jp",
            "https://open.asterav8.jp",
            "https://localhost",
            "capacitor://localhost",
        ):
            response = client.options(
                "/respond",
                headers={
                    "origin": origin,
                    "access-control-request-method": "POST",
                    "access-control-request-headers": "content-type",
                },
            )
            assert response.status_code == 200
            assert response.headers["access-control-allow-origin"] == origin


def test_configured_origins_extend_defaults_instead_of_replacing_staging():
    origins = app_module._merge_allowed_origins(
        "https://asterav8.jp,https://customer.example"
    )
    assert "https://asterav8.jp" in origins
    assert "https://staging.asterav8.jp" in origins
    assert "https://open.asterav8.jp" in origins
    assert "https://localhost" in origins
    assert "capacitor://localhost" in origins
    assert "https://customer.example" in origins
    assert len(origins) == len(set(origins))
