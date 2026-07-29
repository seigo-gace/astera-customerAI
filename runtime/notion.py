from __future__ import annotations

import asyncio
from typing import Any

import httpx


PUBLIC_STATUS = "公開"
V2_FIELDS = (
    "Title",
    "Category",
    "Target_Intents",
    "Definitive_Answer",
    "Exceptions_and_Limits",
    "Status",
)


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
    if kind in {"select", "status"}:
        return (value or {}).get("name", "")
    return value


class NotionClient:
    """Reads only the strict CustomerAI_Master_v2 property contract."""

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
        pages: list[dict[str, Any]] = []
        for row in rows:
            source = row.get("properties", {})
            properties = {
                field: _property_value(source.get(field, {}))
                for field in V2_FIELDS
                if isinstance(source.get(field), dict)
            }
            properties.update({"id": row.get("id", ""), "url": row.get("url", "")})
            pages.append(properties)
        return pages

    async def _query_all(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            payload: dict[str, Any] = {
                "page_size": 100,
                "filter": {
                    "property": "Status",
                    "select": {"equals": PUBLIC_STATUS},
                },
            }
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
