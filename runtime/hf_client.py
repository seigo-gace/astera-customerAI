from __future__ import annotations

import json
from collections.abc import Sequence

import httpx

HF_MODEL_4B = "Qwen/Qwen3-4B"
HF_MODEL_8B = "Qwen/Qwen3-8B"
HF_ALLOWED_MODELS = frozenset({HF_MODEL_4B, HF_MODEL_8B})
HF_CHAT_API = "https://router.huggingface.co/v1/chat/completions"


class HFChatClient:
    """Role-scoped Hugging Face chat client. Multiple role clients may share one HTTP pool."""

    def __init__(
        self,
        *,
        token: str,
        model_id: str,
        api_url: str = HF_CHAT_API,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ):
        if model_id not in HF_ALLOWED_MODELS:
            raise ValueError(f"model_drift:{model_id}")
        if not token.strip() and client is None:
            raise ValueError("hf_token_required")
        self.model_id = model_id
        self.api_url = api_url
        self._token = token.strip()
        self._owned = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def chat_json(
        self,
        messages: Sequence[dict[str, str]],
        *,
        max_tokens: int = 1800,
    ) -> dict[str, object]:
        payload = {
            "model": self.model_id,
            "messages": list(messages),
            "response_format": {"type": "json_object"},
            "stream": False,
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        headers = {"authorization": f"Bearer {self._token}"} if self._token else {}
        response = await self._client.post(self.api_url, headers=headers, json=payload)
        response.raise_for_status()
        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            raise RuntimeError("model_empty_choices")
        content = choices[0].get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("model_empty_content")
        decoded = json.loads(content)
        if not isinstance(decoded, dict):
            raise RuntimeError("model_non_object_json")
        return decoded

    async def aclose(self) -> None:
        if self._owned:
            await self._client.aclose()
