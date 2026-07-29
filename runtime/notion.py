from __future__ import annotations

import asyncio
import hashlib
import os
import re
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
TECHNICAL_MARKERS = (
    "api",
    "architecture",
    "module",
    "runtime",
    "worker",
    "queue",
    "webhook",
    "endpoint",
    "request",
    "response",
    "hash",
    "session",
    "cookie",
    "csrf",
    "retry",
    "timeout",
    "cleanup",
    "idempotency",
    "アーキテクチャ",
    "モジュール",
    "責務",
    "境界",
    "データフロー",
    "並列",
    "冪等",
    "署名",
    "暗号化",
    "認証",
    "実装",
)
CATEGORY_ROOTS = {
    "製品・概要": "product",
    "Astera本体・技術": "core",
    "Astera App": "app",
    "仕様・機能": "feature",
    "利用方法": "usage",
    "アカウント・認証": "account",
    "契約・料金": "billing",
    "決済・クレジット": "billing",
    "セキュリティ・プライバシー": "security",
    "法人・API": "api",
    "外部連携・Webhook": "integration",
    "トラブルシューティング": "troubleshooting",
    "Customer AI": "customer-ai",
    "CAMPFIRE": "campfire",
    "開発支援・スポンサー": "support",
    "投資・事業提携": "investment",
    "市場・競合": "market",
    "費用対効果": "roi",
}


class NotionSyncError(RuntimeError):
    pass


def _rich_text(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    return "".join(
        str(item.get("plain_text", "")) for item in value if isinstance(item, dict)
    )


def _property_value(prop: dict[str, Any]) -> Any:
    kind = prop.get("type")
    value = prop.get(kind, {}) if kind else None
    if kind in {"title", "rich_text"}:
        return _rich_text(value)
    if kind in {"select", "status"}:
        return (value or {}).get("name", "")
    if kind == "multi_select":
        return [
            str(item.get("name", ""))
            for item in (value or [])
            if isinstance(item, dict) and item.get("name")
        ]
    if kind == "relation":
        return [
            str(item.get("id", ""))
            for item in (value or [])
            if isinstance(item, dict) and item.get("id")
        ]
    if kind == "date":
        return (value or {}).get("start", "")
    return value


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized[:48] or "record"


def _question_types(title: str) -> list[str]:
    types = ["what"]
    if any(marker in title for marker in ("なぜ", "理由")):
        types.append("why")
    if any(marker in title for marker in ("どう", "方法", "手順", "できますか")):
        types.append("how")
    if any(marker in title for marker in ("違い", "同じ", "比較", "競合")):
        types.append("comparison")
    if any(marker in title for marker in ("失敗", "止ま", "反映され", "エラー")):
        types.extend(["failure", "recovery"])
    if any(marker in title for marker in ("上限", "制限", "保証", "完全")):
        types.append("limit")
    if any(marker in title.lower() for marker in ("security", "secret", "token", "認証", "署名", "暗号")):
        types.append("security")
    return list(dict.fromkeys(types))


def _implementation_status(exceptions: str) -> str:
    if any(marker in exceptions for marker in ("未実装", "設計段階", "未完成", "未検証")):
        return "planned"
    if any(marker in exceptions for marker in ("一部実装", "部分実装")):
        return "partially_implemented"
    return "implemented"


def _fallback_index(properties: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    title = str(properties.get("Title", "")).strip()
    category = str(properties.get("Category", "")).strip()
    intents = str(properties.get("Target_Intents", "")).strip()
    answer = str(properties.get("Definitive_Answer", "")).strip()
    exceptions = str(properties.get("Exceptions_and_Limits", "")).strip()
    root = CATEGORY_ROOTS.get(category, "general")
    combined = "\n".join((title, intents, answer, exceptions))
    lowered = combined.lower()
    technical = any(marker in lowered for marker in TECHNICAL_MARKERS)
    digest = _sha256(combined)
    canonical_id = f"auto.{root}.{digest[:20]}"
    source_identity = "|".join(
        (
            str(row.get("id", "")),
            str(row.get("url", "")),
            str(row.get("last_edited_time", "")),
        )
    )
    keywords = " ".join(
        dict.fromkeys(
            part
            for part in re.split(r"[\s、。・/|]+", f"{title} {intents}")
            if len(part) >= 2
        )
    )[:3000]
    audience = ["general"]
    answer_level = ["simple", "detailed"]
    disclosure = "public"
    if technical:
        audience.extend(["developer", "enterprise"])
        answer_level.append("technical_public")
        disclosure = "public_technical"
    if category in {"投資・事業提携", "市場・競合", "費用対効果"}:
        audience.append("investor")
        answer_level.append("business")
    if category == "開発支援・スポンサー":
        audience.extend(["sponsor", "supporter"])
        answer_level.append("business")
    return {
        "Index_Title": canonical_id,
        "Master_Title": title,
        "Canonical_ID": canonical_id,
        "Parent_ID": f"auto.{root}",
        "Root_Domain": root,
        "Topic": _slug(category),
        "Subtopic": digest[:12],
        "Audience": list(dict.fromkeys(audience)),
        "Answer_Level": list(dict.fromkeys(answer_level)),
        "Question_Types": _question_types(title),
        "Aliases": intents,
        "Keywords": keywords,
        "Related_IDs": "",
        "Source_Page_ID": str(row.get("id", "")),
        "Source_Section": "CustomerAI_Master_v2",
        "Source_Priority": "notion_master_fallback_index",
        "Source_Last_Edited": str(row.get("last_edited_time", "")),
        "Content_Hash": digest,
        "Source_Hash": _sha256(source_identity),
        "Version": str(row.get("last_edited_time", "")) or digest[:12],
        "Implementation_Status": _implementation_status(exceptions),
        "Disclosure_Level": disclosure,
        "Index_Status": ACTIVE_INDEX_STATUS,
        "Effective_From": str(row.get("last_edited_time", ""))[:10],
    }


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
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds, headers=self.headers
        ) as client:
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
            properties["_index"] = index_record or _fallback_index(properties, row)
            pages.append(properties)
        return pages

    async def fetch_index_records(self) -> list[dict[str, Any]]:
        if not self.index_data_source_id:
            return []
        if not self.token:
            raise NotionSyncError("notion_not_configured")
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds, headers=self.headers
        ) as client:
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

    async def _request_with_retry(
        self, client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        delay = 1.0
        last_error: Exception | None = None
        for _ in range(5):
            try:
                response = await client.request(method, url, **kwargs)
                if response.status_code == 429:
                    await asyncio.sleep(
                        float(response.headers.get("retry-after", delay))
                    )
                    delay = min(delay * 2, 16)
                    continue
                if response.status_code >= 500:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 16)
                    continue
                response.raise_for_status()
                return response
            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.HTTPStatusError,
            ) as exc:
                last_error = exc
                await asyncio.sleep(delay)
                delay = min(delay * 2, 16)
        raise NotionSyncError(f"notion_request_failed: {last_error}")
