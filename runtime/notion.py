from __future__ import annotations

import asyncio
from typing import Any

import httpx


class NotionSyncError(RuntimeError):
    pass


def _rich_text(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    return "".join(str(item.get("plain_text", "")) for item in value if isinstance(item, dict))


def _property_value(prop: dict[str, Any]) -> Any:
    kind = prop.get("type")
    value = prop.get(kind, {}) if kind else None
    if kind in {"title", "rich_text"}:
        return _rich_text(value)
    if kind == "select":
        return (value or {}).get("name", "")
    if kind == "multi_select":
        return [item.get("name", "") for item in (value or [])]
    if kind == "checkbox":
        return bool(value)
    if kind == "date":
        return (value or {}).get("start", "")
    if kind == "url":
        return value or ""
    if kind == "status":
        return (value or {}).get("name", "")
    if kind == "number":
        return value
    if kind == "formula":
        formula_type = (value or {}).get("type")
        return (value or {}).get(formula_type) if formula_type else None
    return value


def _block_text(block: dict[str, Any]) -> str:
    kind = block.get("type")
    payload = block.get(kind, {}) if kind else {}
    text = _rich_text(payload.get("rich_text", []))
    if kind in {"bulleted_list_item", "numbered_list_item", "to_do"} and text:
        return f"- {text}"
    if kind and kind.startswith("heading_") and text:
        return f"## {text}"
    if kind == "code" and text:
        language = payload.get("language", "text")
        return f"```{language}\n{text}\n```"
    return text


class NotionClient:
    def __init__(self, token: str, data_source_id: str, *, timeout_seconds: int = 20):
        self.token = token
        self.data_source_id = data_source_id
        self.timeout_seconds = timeout_seconds
        self.headers = {
            "authorization": f"Bearer {token}",
            "notion-version": "2025-09-03",
            "content-type": "application/json",
        }

    async def fetch_pages(self) -> list[dict[str, Any]]:
        if not self.token or not self.data_source_id:
            raise NotionSyncError("notion_not_configured")
        async with httpx.AsyncClient(timeout=self.timeout_seconds, headers=self.headers) as client:
            rows = await self._query_all(client)
            semaphore = asyncio.Semaphore(3)

            async def hydrate(row: dict[str, Any]) -> dict[str, Any]:
                async with semaphore:
                    content = await self._fetch_blocks(client, row["id"])
                    properties = {
                        name: _property_value(value)
                        for name, value in row.get("properties", {}).items()
                        if isinstance(value, dict)
                    }
                    properties.update(
                        {
                            "id": row["id"],
                            "url": row.get("url", ""),
                            "本文": content,
                        }
                    )
                    return properties

            return await asyncio.gather(*(hydrate(row) for row in rows))

    async def _query_all(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            payload: dict[str, Any] = {"page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            response = await self._request_with_retry(
                client,
                "POST",
                f"https://api.notion.com/v1/data_sources/{self.data_source_id}/query",
                json=payload,
            )
            body = response.json()
            results.extend(body.get("results", []))
            if not body.get("has_more"):
                return results
            cursor = body.get("next_cursor")
            if not cursor:
                raise NotionSyncError("notion_missing_cursor")

    async def _fetch_blocks(self, client: httpx.AsyncClient, block_id: str) -> str:
        lines: list[str] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            response = await self._request_with_retry(
                client,
                "GET",
                f"https://api.notion.com/v1/blocks/{block_id}/children",
                params=params,
            )
            body = response.json()
            for block in body.get("results", []):
                text = _block_text(block)
                if text:
                    lines.append(text)
                if block.get("has_children"):
                    child = await self._fetch_blocks(client, block["id"])
                    if child:
                        lines.append(child)
            if not body.get("has_more"):
                break
            cursor = body.get("next_cursor")
            if not cursor:
                break
        return "\n".join(lines).strip()

    async def _request_with_retry(self, client: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> httpx.Response:
        delay = 1.0
        last_error: Exception | None = None
        for _ in range(5):
            try:
                response = await client.request(method, url, **kwargs)
                if response.status_code == 429:
                    await asyncio.sleep(float(response.headers.get("retry-after", delay)))
                    delay = min(delay * 2, 16)
                    continue
                if response.status_code >= 500:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 16)
                    continue
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_error = exc
                await asyncio.sleep(delay)
                delay = min(delay * 2, 16)
        raise NotionSyncError(f"notion_request_failed: {last_error}")
