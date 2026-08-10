from __future__ import annotations

from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.model import ConversationLanguageEngine, _generate_remote, _routed_model


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    monkeypatch.setenv("CUSTOMER_AI_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("CUSTOMER_AI_ENABLE_MODEL", "1")
    monkeypatch.setenv("CUSTOMER_AI_MODEL_ID", "Qwen/Qwen3-0.6B")
    monkeypatch.setenv("HF_TOKEN", "hf_test_secret_value")
    return Settings.load()


def test_engine_requires_private_hf_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("CUSTOMER_AI_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("CUSTOMER_AI_ENABLE_MODEL", "1")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    engine = ConversationLanguageEngine(Settings.load())
    assert engine.available() is False


def test_default_provider_is_explicit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CUSTOMER_AI_HF_PROVIDER", raising=False)
    assert _routed_model("Qwen/Qwen3-0.6B") == "Qwen/Qwen3-0.6B:featherless-ai"
    assert _routed_model("Qwen/Qwen3-0.6B:featherless-ai") == "Qwen/Qwen3-0.6B:featherless-ai"


def test_hf_api_request_stays_server_side(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HF_TOKEN", "hf_test_secret_value")
    monkeypatch.setenv("CUSTOMER_AI_HF_PROVIDER", "featherless-ai")
    captured: dict = {}

    def fake_post(url, *, headers, json, timeout, follow_redirects):
        captured.update(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
                "follow_redirects": follow_redirects,
            }
        )
        return FakeResponse(
            200,
            {"choices": [{"message": {"content": "テスト回答"}}]},
        )

    monkeypatch.setattr("runtime.model.httpx.post", fake_post)
    answer = _generate_remote("Qwen/Qwen3-0.6B", '{"deterministic_answer":"テスト"}', 32)

    assert answer == "テスト回答"
    assert captured["url"] == "https://router.huggingface.co/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer hf_test_secret_value"
    assert captured["json"]["model"] == "Qwen/Qwen3-0.6B:featherless-ai"
    assert captured["json"]["max_tokens"] == 32
    assert "temperature" not in captured["json"]
    assert captured["follow_redirects"] is True
    assert "hf_test_secret_value" not in str(captured["json"])


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (401, "hf_api_unauthorized"),
        (402, "hf_api_payment_required"),
        (429, "hf_api_rate_limited"),
        (503, "hf_api_unavailable:503"),
    ],
)
def test_hf_api_errors_are_classified(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    message: str,
):
    monkeypatch.setenv("HF_TOKEN", "hf_test_secret_value")
    monkeypatch.setattr(
        "runtime.model.httpx.post",
        lambda *args, **kwargs: FakeResponse(status, {"error": "provider error"}),
    )
    with pytest.raises(RuntimeError, match=message):
        _generate_remote("Qwen/Qwen3-0.6B", "{}", 8)


def test_engine_executes_hf_api_without_local_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    settings = _settings(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "runtime.model._generate_remote",
        lambda model_id, packet, max_new_tokens: "決済状態を確認してください。",
    )
    engine = ConversationLanguageEngine(settings)
    result = engine.execute(
        {
            "message": "どう確認する？",
            "response_rules": {},
            "support_packet": {
                "question_tasks": [
                    {
                        "task_id": "q1",
                        "text": "どう確認する？",
                        "answer_shape": "ordered_steps",
                    }
                ],
                "evidence": [
                    {
                        "evidence_id": "kb:1",
                        "question": "確認方法",
                        "target": "operation",
                        "short_answer": "決済状態を確認してください。",
                        "answer_boundary": "登録済み情報のみ",
                    }
                ],
                "blueprint": {
                    "sections": [{"task_id": "q1", "resolved": True}],
                    "unresolved_task_ids": [],
                    "deterministic_answer": "決済状態を確認してください。",
                },
            },
        }
    )
    assert result["answer"] == "決済状態を確認してください。"
    assert result["answered_task_ids"] == ["q1"]
    assert result["used_evidence_ids"] == ["kb:1"]
