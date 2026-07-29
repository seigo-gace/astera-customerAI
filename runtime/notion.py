from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx


PUBLIC_STATUS = "公開"
ACTIVE_INDEX_STATUS = "active"
PUBLIC_DISCLOSURE_LEVELS = {"public", "public_technical"}
V2_FIELDS = (
    "Title",
    "Category",
    "Target_Intents",
    "Definitive_Answer",
    "Exceptions_and_Limits",
    "Status",
)
INDEX_FIELDS = (
    "Index_Title",
    "Master_Record",
    "Master_Title",
    "Canonical_ID",
    "Parent_ID",
    "Root_Domain",
    "Topic",
    "Subtopic",
    "Audience",
    "Answer_Level",
    "Question_Types",
    "Aliases",
    "Keywords",
    "Related_IDs",
    "Source_Page_ID",
    "Source_Section",
    "Source_Priority",
    "Source_Last_Edited",
    "Content_Hash",
    "Source_Hash",
    "Version",
    "Implementation_Status",
    "Disclosure_Level",
    "Index_Status",
    "Effective_From",
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
    if kind == "multi_select":
        return [str(item.get("name", "")) for item in (value or []) if isinstance(item, dict) and item.get("name")]
    if kind == "relation":
        return [str(item.get("id", "")) for item in (value or []) if isinstance(item, dict) and item.get("id")]
    if kind == "date":
        return (value or {}).get("start", "")
    return value


class NotionClient:
    """Reads the strict Master body and the public-only retrieval index."""

    def __init__(
        self,
        token: str,
        data_source_id: str,
        *,
        index_data_source_id: str | None = None,
        timeout_seconds: int = 20,
    ):
        self.token = token
        self.data_source_id = data_source_id
        self.index_data_source_id = (
            os.getenv("NOTION_INDEX_DATA_SOURCE_ID", "")
            if index_data_source_id is None
            else index_data_source_id
        )
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
            index_records = await self._fetch_index_records_with_client(client)
        index_by_title = {
            str(record.get("Master_Title", "")): record
            for record in index_records
            if str(record.get("Master_Title", ""))
        }
        pages: list[dict[str, Any]] = []
        for row in rows:
            source = row.get("properties", {})
            properties = {
                field: _property_value(source.get(field, {}))
                for field in V2_FIELDS
                if isinstance(source.get(field), dict)
            }
            properties.update({"id": row.get("id", ""), "url": row.get("url", "")})
            index_record = index_by_title.get(str(properties.get("Title", "")))
            if index_record is not None:
                properties["_index"] = index_record
            pages.append(properties)
        return pages

    async def fetch_index_records(self) -> list[dict[str, Any]]:
        if not self.index_data_source_id:
            return []
        if not self.token:
            raise NotionSyncError("notion_not_configured")
        async with httpx.AsyncClient(timeout=self.timeout_seconds, headers=self.headers) as client:
            return await self._fetch_index_records_with_client(client)

    async def _fetch_index_records_with_client(
        self, client: httpx.AsyncClient
    ) -> list[dict[str, Any]]:
        if not self.index_data_source_id:
            return []
        rows = await self._query_all(
            client,
            data_source_id=self.index_data_source_id,
            filter_property="Index_Status",
            filter_value=ACTIVE_INDEX_STATUS,
        )
        records: list[dict[str, Any]] = []
        for row in rows:
            source = row.get("properties", {})
            properties = {
                field: _property_value(source.get(field, {}))
                for field in INDEX_FIELDS
                if isinstance(source.get(field), dict)
            }
            if properties.get("Disclosure_Level") not in PUBLIC_DISCLOSURE_LEVELS:
                continue
            properties.update({"id": row.get("id", ""), "url": row.get("url", "")})
            records.append(properties)
        return records

    async def _query_all(
        self,
        client: httpx.AsyncClient,
        *,
        data_source_id: str | None = None,
        filter_property: str = "Status",
        filter_value: str = PUBLIC_STATUS,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        target_data_source_id = data_source_id or self.data_source_id
        while True:
            payload: dict[str, Any] = {
                "page_size": 100,
                "filter": {
                    "property": filter_property,
                    "select": {"equals": filter_value},
                },
            }
            if cursor:
                payload["start_cursor"] = cursor
            response = await self._request_with_retry(
                client,
                "POST",
                f"https://api.notion.com/v1/data_sources/{target_data_source_id}/query",
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
