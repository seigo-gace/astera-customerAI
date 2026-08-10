from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


VALID_PAYLOAD = {
    "session_id": "session_hp_12345678",
    "message_id": "message_hp_12345678",
    "message": "Asteraについて教えて",
    "locale": "ja-JP",
    "source": "astera-hp",
    "response_mode": "general",
    "mode_source": "selected",
    "current_path": "/ja/",
}
ORIGIN = "https://asterav8.jp"


def load_runtime(data_root):
    import hp_runtime

    return importlib.reload(hp_runtime)


def install_pipeline(monkeypatch, module, answer="Asteraの案内です。"):
    captured = {}

    async def fake_pipeline(request_id, payload):
        captured["request_id"] = request_id
        captured["payload"] = payload
        return {
            "status": "completed",
            "answer": answer,
            "clarification": None,
            "context_used": True,
            "routing": {
                "response_mode": payload.response_mode,
                "mode_source": payload.mode_source,
                "topic": "astera",
            },
        }

    monkeypatch.setattr(module.service, "_run_pipeline", fake_pipeline)
    return captured


def test_health_and_docs_are_minimal(data_root):
    module = load_runtime(data_root)
    with TestClient(module.app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_respond_runs_existing_pipeline_synchronously(data_root, monkeypatch):
    module = load_runtime(data_root)
    captured = install_pipeline(monkeypatch, module)
    with TestClient(module.app) as client:
        response = client.post("/respond", json=VALID_PAYLOAD, headers={"origin": ORIGIN})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["answer"] == "Asteraの案内です。"
    assert body["session_id"] == VALID_PAYLOAD["session_id"]
    assert body["message_id"] == VALID_PAYLOAD["message_id"]
    assert body["context_used"] is True
    assert body["routing"]["response_mode"] == "general"
    assert captured["request_id"].startswith("hp_")
    assert captured["payload"].source == "astera-hp"
    assert response.headers.get("access-control-allow-origin") == ORIGIN


def test_respond_rejects_unapproved_origin(data_root, monkeypatch):
    module = load_runtime(data_root)
    install_pipeline(monkeypatch, module)
    with TestClient(module.app) as client:
        response = client.post(
            "/respond",
            json=VALID_PAYLOAD,
            headers={"origin": "https://example.invalid"},
        )
    assert response.status_code == 403
    assert response.json()["detail"] == "origin_not_allowed"


def test_respond_rejects_non_hp_source(data_root, monkeypatch):
    module = load_runtime(data_root)
    install_pipeline(monkeypatch, module)
    payload = {**VALID_PAYLOAD, "source": "astera-app"}
    with TestClient(module.app) as client:
        response = client.post("/respond", json=payload, headers={"origin": ORIGIN})
    assert response.status_code == 422
    assert response.json()["detail"] == "unsupported_public_source"


def test_respond_enforces_12000_character_boundary(data_root, monkeypatch):
    module = load_runtime(data_root)
    install_pipeline(monkeypatch, module)
    payload = {**VALID_PAYLOAD, "message": "あ" * 12001}
    with TestClient(module.app) as client:
        response = client.post("/respond", json=payload, headers={"origin": ORIGIN})
    assert response.status_code == 413
    assert response.json()["detail"] == "message_too_large"


def test_session_rate_limit_is_enforced(data_root, monkeypatch):
    module = load_runtime(data_root)
    install_pipeline(monkeypatch, module)
    module._rate_buckets.clear()
    monkeypatch.setattr(module, "RATE_LIMIT_PER_SESSION", 1)
    with TestClient(module.app) as client:
        first = client.post("/respond", json=VALID_PAYLOAD, headers={"origin": ORIGIN})
        second = client.post(
            "/respond",
            json={**VALID_PAYLOAD, "message_id": "message_hp_87654321"},
            headers={"origin": ORIGIN},
        )
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["detail"] == "rate_limited"
    assert second.headers.get("retry-after") == "60"


def test_delete_session_is_local_and_origin_limited(data_root, monkeypatch):
    module = load_runtime(data_root)
    deleted_ids = []
    monkeypatch.setattr(
        module.service.conversations,
        "delete",
        lambda session_id: deleted_ids.append(session_id) or True,
    )
    module._rate_buckets[VALID_PAYLOAD["session_id"]].append(1.0)
    with TestClient(module.app) as client:
        response = client.delete(
            f"/sessions/{VALID_PAYLOAD['session_id']}",
            headers={"origin": ORIGIN},
        )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert deleted_ids == [VALID_PAYLOAD["session_id"]]
    assert VALID_PAYLOAD["session_id"] not in module._rate_buckets


def test_response_never_exposes_server_side_hf_token(data_root, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_server_secret_should_never_leak")
    module = load_runtime(data_root)
    install_pipeline(monkeypatch, module, answer="公開可能な回答だけです。")
    with TestClient(module.app) as client:
        response = client.post("/respond", json=VALID_PAYLOAD, headers={"origin": ORIGIN})
    assert response.status_code == 200
    assert "hf_server_secret_should_never_leak" not in response.text
    assert "HF_TOKEN" not in response.text
