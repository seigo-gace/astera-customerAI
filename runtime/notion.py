from __future__ import annotations

import asyncio
from datetime import UTC, datetime
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


def _rich_text_payload(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": {"content": text[:2000]}}] if text else []


def _paragraph_blocks(text: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for paragraph in [item.strip() for item in text.split("\n\n") if item.strip()]:
        for start in range(0, len(paragraph), 1900):
            chunk = paragraph[start : start + 1900]
            blocks.append(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": _rich_text_payload(chunk)},
                }
            )
    return blocks


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
        self._schema_cache: dict[str, Any] | None = None

    async def fetch_pages(self) -> list[dict[str, Any]]:
        self._ensure_configured()
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
                    properties.update({"id": row["id"], "url": row.get("url", ""), "本文": content})
                    return properties

            return await asyncio.gather(*(hydrate(row) for row in rows))

    async def add_search_term(self, page_id: str, term: str) -> dict[str, Any]:
        self._ensure_configured()
        async with httpx.AsyncClient(timeout=self.timeout_seconds, headers=self.headers) as client:
            schema = await self._schema(client)
            search_name = self._find_property(schema, preferred="検索語", allowed={"rich_text", "multi_select"})
            if not search_name:
                raise NotionSyncError("notion_search_terms_property_missing")
            page = await self._request_with_retry(client, "GET", f"https://api.notion.com/v1/pages/{page_id}")
            current = _property_value(page.json().get("properties", {}).get(search_name, {}))
            if isinstance(current, list):
                terms = list(dict.fromkeys([*current, term]))
            else:
                split = [item.strip() for item in str(current or "").replace("、", ",").split(",") if item.strip()]
                terms = list(dict.fromkeys([*split, term]))
            encoded = self._encode_property(schema[search_name], terms if schema[search_name]["type"] == "multi_select" else "、".join(terms))
            await self._request_with_retry(
                client,
                "PATCH",
                f"https://api.notion.com/v1/pages/{page_id}",
                json={"properties": {search_name: encoded}},
            )
            return {"page_id": page_id, "property": search_name, "terms": terms}

    async def publish_approved_candidate(self, candidate: dict[str, Any], approval: dict[str, Any]) -> dict[str, Any]:
        self._ensure_configured()
        source_refs = [str(item).strip() for item in approval.get("source_refs", []) if str(item).strip()]
        if not source_refs:
            raise NotionSyncError("source_refs_required")
        action = str(approval.get("action") or "")
        if action == "add_search_term":
            page_id = str(approval.get("page_id") or "")
            if not page_id:
                raise NotionSyncError("page_id_required")
            return {"action": action, **await self.add_search_term(page_id, candidate["question"])}

        question = str(approval.get("question") or candidate.get("question") or "").strip()
        short_answer = str(approval.get("short_answer") or "").strip()
        body = str(approval.get("body") or "").strip()
        if not question or not short_answer or not body:
            raise NotionSyncError("question_short_answer_body_required")
        implementation_status = str(approval.get("implementation_status") or "文書確認済み")
        if implementation_status not in {"実装済み", "文書確認済み"}:
            raise NotionSyncError("invalid_implementation_status")

        async with httpx.AsyncClient(timeout=self.timeout_seconds, headers=self.headers) as client:
            schema = await self._schema(client)
            if action == "update_existing":
                page_id = str(approval.get("page_id") or "")
                if not page_id:
                    raise NotionSyncError("page_id_required")
                properties = self._candidate_properties(
                    schema,
                    question=question,
                    short_answer=short_answer,
                    search_terms=approval.get("search_terms") or [candidate.get("question", "")],
                    implementation_status=implementation_status,
                )
                await self._request_with_retry(
                    client,
                    "PATCH",
                    f"https://api.notion.com/v1/pages/{page_id}",
                    json={"properties": properties},
                )
                append_text = f"追加確認済み情報\n{body}\n\n根拠: " + " / ".join(source_refs)
                await self._request_with_retry(
                    client,
                    "PATCH",
                    f"https://api.notion.com/v1/blocks/{page_id}/children",
                    json={"children": _paragraph_blocks(append_text)},
                )
                return {"action": action, "page_id": page_id, "source_refs": source_refs}

            if action != "create_page":
                raise NotionSyncError("unsupported_candidate_action")
            properties = self._candidate_properties(
                schema,
                question=question,
                short_answer=short_answer,
                search_terms=approval.get("search_terms") or [candidate.get("question", "")],
                implementation_status=implementation_status,
            )
            page = await self._request_with_retry(
                client,
                "POST",
                "https://api.notion.com/v1/pages",
                json={
                    "parent": {"type": "data_source_id", "data_source_id": self.data_source_id},
                    "properties": properties,
                    "children": _paragraph_blocks(body + "\n\n根拠: " + " / ".join(source_refs)),
                },
            )
            payload = page.json()
            return {"action": action, "page_id": payload.get("id"), "url": payload.get("url"), "source_refs": source_refs}

    async def _schema(self, client: httpx.AsyncClient) -> dict[str, Any]:
        if self._schema_cache is not None:
            return self._schema_cache
        response = await self._request_with_retry(client, "GET", f"https://api.notion.com/v1/data_sources/{self.data_source_id}")
        properties = response.json().get("properties", {})
        if not isinstance(properties, dict) or not properties:
            raise NotionSyncError("notion_schema_missing")
        self._schema_cache = properties
        return properties

    def _candidate_properties(
        self,
        schema: dict[str, Any],
        *,
        question: str,
        short_answer: str,
        search_terms: Any,
        implementation_status: str,
    ) -> dict[str, Any]:
        title_name = self._find_property(schema, preferred="質問", allowed={"title"})
        if not title_name:
            raise NotionSyncError("notion_title_property_missing")
        values: dict[str, Any] = {
            title_name: question,
            "短い回答": short_answer,
            "検索語": search_terms if isinstance(search_terms, list) else [search_terms],
            "公開状態": "公開",
            "実装状態": implementation_status,
            "要再確認": False,
            "確認日": datetime.now(UTC).date().isoformat(),
        }
        properties: dict[str, Any] = {}
        for name, value in values.items():
            if name in schema:
                properties[name] = self._encode_property(schema[name], value)
        return properties

    @staticmethod
    def _find_property(schema: dict[str, Any], *, preferred: str, allowed: set[str]) -> str | None:
        if preferred in schema and schema[preferred].get("type") in allowed:
            return preferred
        for name, definition in schema.items():
            if definition.get("type") in allowed:
                return name
        return None

    @staticmethod
    def _encode_property(definition: dict[str, Any], value: Any) -> dict[str, Any]:
        kind = definition.get("type")
        if kind == "title":
            return {"title": _rich_text_payload(str(value))}
        if kind == "rich_text":
            text = "、".join(str(item) for item in value if str(item).strip()) if isinstance(value, list) else str(value)
            return {"rich_text": _rich_text_payload(text)}
        if kind == "select":
            return {"select": {"name": str(value)}}
        if kind == "status":
            return {"status": {"name": str(value)}}
        if kind == "multi_select":
            items = value if isinstance(value, list) else [value]
            return {"multi_select": [{"name": str(item)[:100]} for item in items if str(item).strip()]}
        if kind == "checkbox":
            return {"checkbox": bool(value)}
        if kind == "date":
            return {"date": {"start": str(value)}}
        if kind == "url":
            return {"url": str(value)}
        raise NotionSyncError(f"unsupported_notion_property_type:{kind}")

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

    def _ensure_configured(self) -> None:
        if not self.token or not self.data_source_id:
            raise NotionSyncError("notion_not_configured")
