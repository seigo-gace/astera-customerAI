from __future__ import annotations

import json
from collections.abc import Sequence

import httpx

HF_MODEL_4B = "Qwen/Qwen3-4B"  # legacy constant; production role backend must not use it.
HF_MODEL_8B = "Qwen/Qwen3-8B"
HF_ALLOWED_MODELS = frozenset({HF_MODEL_8B})
# Production default is deliberately localhost. This prevents accidental use of
# Hugging Face Inference Providers and therefore prevents inference-credit burn.
HF_CHAT_API = "http://127.0.0.1:8081/v1/chat/completions"


class HFChatClient:
    """OpenAI-compatible chat client used by the three role runtimes.

    In production the endpoint is the single local llama.cpp Qwen3-8B process.
    The class name is retained for compatibility with existing tests/imports.
    """

    def __init__(
        self,
        *,
        token: str,
        model_id: str,
        api_url: str = HF_CHAT_API,
        timeout_seconds: float = 300.0,
        client: httpx.AsyncClient | None = None,
    ):
        if model_id not in HF_ALLOWED_MODELS:
            raise ValueError(f"model_drift:{model_id}")
        self.model_id = model_id
        self.api_url = api_url
        self._token = token.strip()
        self._owned = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def chat_json(
        self,
        messages: Sequence[dict[str, str]],
        *,
        max_tokens: int = 900,
    ) -> dict[str, object]:
        payload = {
            "model": self.model_id,
            "messages": list(messages),
            "response_format": {"type": "json_object"},
            "stream": False,
            "temperature": 0.2,
            "top_p": 0.8,
            "max_tokens": max_tokens,
        }
        # Local llama.cpp does not require auth. Keep the header only for an
        # explicitly configured non-local compatibility endpoint.
        headers = {}
        if self._token and not self.api_url.startswith(("http://127.0.0.1", "http://localhost")):
            headers["authorization"] = f"Bearer {self._token}"
        response = await self._client.post(self.api_url, headers=headers, json=payload)
        response.raise_for_status()
        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            raise RuntimeError("model_empty_choices")
        content = choices[0].get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("model_empty_content")

        text = content.strip()
        # Qwen3 may emit an empty/short thinking wrapper even when /no_think is
        # requested through a soft switch. Never expose it; decode only the JSON.
        if "</think>" in text:
            text = text.split("</think>", 1)[1].strip()
        first = text.find("{")
        last = text.rfind("}")
        if first >= 0 and last >= first:
            text = text[first : last + 1]
        decoded = json.loads(text)
        if not isinstance(decoded, dict):
            raise RuntimeError("model_non_object_json")
        return decoded

    async def aclose(self) -> None:
        if self._owned:
            await self._client.aclose()
