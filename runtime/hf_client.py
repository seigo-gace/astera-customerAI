from __future__ import annotations

import json
from collections.abc import Sequence

import httpx

HF_MODEL_4B = "llm-jp/llm-jp-3-3.7b-instruct3"
HF_MODEL_8B = "llm-jp/llm-jp-4-8b-instruct"
HF_ALLOWED_MODELS = frozenset({HF_MODEL_4B, HF_MODEL_8B})

HF_CHAT_API_CONSTRUCTIVE = "http://127.0.0.1:8081/v1/chat/completions"
HF_CHAT_API_ADVERSARIAL = "http://127.0.0.1:8082/v1/chat/completions"
HF_CHAT_API_EVIDENCE = "http://127.0.0.1:8083/v1/chat/completions"
HF_CHAT_API = HF_CHAT_API_CONSTRUCTIVE  # compatibility alias only


def _default_local_url(model_id: str) -> str:
    if model_id == HF_MODEL_4B:
        return HF_CHAT_API_CONSTRUCTIVE
    if model_id == HF_MODEL_8B:
        return HF_CHAT_API_EVIDENCE
    raise ValueError(f"model_drift:{model_id}")


class HFChatClient:
    """Role-scoped OpenAI-compatible client backed only by local llama.cpp.

    Production rejects non-loopback endpoints so HF Inference Providers cannot
    be used accidentally and cannot consume inference credits.
    """

    def __init__(
        self,
        *,
        token: str = "",
        model_id: str,
        api_url: str | None = None,
        timeout_seconds: float = 600.0,
        client: httpx.AsyncClient | None = None,
    ):
        if model_id not in HF_ALLOWED_MODELS:
            raise ValueError(f"model_drift:{model_id}")
        resolved_url = (api_url or _default_local_url(model_id)).strip()
        if not resolved_url.startswith(("http://127.0.0.1", "http://localhost")):
            raise ValueError("remote_inference_endpoint_forbidden")
        self.model_id = model_id
        self.api_url = resolved_url
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
            "temperature": 0.1,
            "top_p": 0.9,
            "max_tokens": max_tokens,
        }
        response = await self._client.post(self.api_url, json=payload)
        response.raise_for_status()
        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            raise RuntimeError("model_empty_choices")
        content = choices[0].get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("model_empty_content")
        text = content.strip()
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
