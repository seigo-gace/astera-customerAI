from __future__ import annotations

import os
from typing import Any

import httpx

from .notion import (
    ACTIVE_INDEX_STATUS,
    CATEGORY_ROOTS,
    NotionClient,
    NotionSyncError,
    TECHNICAL_MARKERS,
    _property_value,
    _question_types,
    _sha256,
    _slug,
)

V3_DATA_SOURCE_ID = "e8f1bcaa-8e1f-482f-97db-f90542699e4a"
V3_PUBLIC_STATES = ("公開", "検証公開")
V3_PUBLIC_PERMISSIONS = {"公開可", "条件付き"}
V3_VALIDATED_RESULTS = {"合格"}
V3_PROHIBITED_ROLES = {"prohibited"}
V3_FIELDS = (
    "KB ID",
    "質問",
    "完全一致質問",
    "短い回答",
    "直接回答",
    "本文",
    "検索語",
    "言い換え",
    "参照表現",
    "質問タスク",
    "回答境界",
    "誤前提",
    "訂正文",
    "禁止断定",
    "適用条件",
    "非適用条件",
    "競合排除キー",
    "一貫性キー",
    "矛盾禁止キー",
    "会話継承キー",
    "継承条件",
    "話題切替条件",
    "ドメイン",
    "対象",
    "対象物",
    "対象者",
    "操作",
    "状態",
    "処理段階",
    "Evidence Role",
    "Runtime採用",
    "公開可否",
    "公開状態",
    "実装状態",
    "最終検証結果",
    "要再確認",
    "優先度",
    "回答スロット",
    "回答順",
    "単独回答可",
    "統合必須",
)

_ORIGINAL_FETCH_PAGES = NotionClient.fetch_pages
_INSTALLED = False


def _text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _join_fields(properties: dict[str, Any], names: tuple[str, ...]) -> str:
    parts: list[str] = []
    for name in names:
        value = _text(properties.get(name))
        if value and value not in parts:
            parts.append(value)
    return "\n".join(parts)


def _checkbox_true(value: Any) -> bool:
    return value in (True, 1, "__YES__", "true", "True")


def _root_domain(category: str) -> str:
    if category in CATEGORY_ROOTS:
        return CATEGORY_ROOTS[category]
    return {
        "product": "product",
        "customer-ai": "customer-ai",
        "account": "account",
        "authentication": "account",
        "billing": "billing",
        "credit": "billing",
        "subscription": "billing",
        "private-mode": "security",
        "api": "api",
        "webhook": "integration",
        "storage": "storage",
        "support": "support",
        "campfire": "campfire",
        "sponsor": "support",
        "investor": "investment",
        "corporate": "enterprise",
        "security": "security",
        "incident": "troubleshooting",
        "operations": "operations",
        "terms": "terms",
    }.get(category, category or "general")


def map_v3_record(
    properties: dict[str, Any],
    row: dict[str, Any],
    *,
    allow_validated_candidates: bool = False,
) -> dict[str, Any] | None:
    """Map one canonical KB v3 row into the stable runtime search contract."""
    if _text(properties.get("公開状態")) not in V3_PUBLIC_STATES:
        return None
    if _text(properties.get("公開可否")) not in V3_PUBLIC_PERMISSIONS:
        return None
    if _text(properties.get("Evidence Role")) in V3_PROHIBITED_ROLES:
        return None
    if _checkbox_true(properties.get("要再確認")):
        return None

    runtime_adopted = _checkbox_true(properties.get("Runtime採用"))
    validated = _text(properties.get("最終検証結果")) in V3_VALIDATED_RESULTS
    if not runtime_adopted and not (allow_validated_candidates and validated):
        return None

    title = _text(properties.get("質問")) or _text(properties.get("完全一致質問"))
    if not title:
        return None
    category = _text(properties.get("ドメイン")) or "product"
    target_intents = _join_fields(
        properties,
        (
            "完全一致質問",
            "検索語",
            "言い換え",
            "参照表現",
            "質問タスク",
            "会話継承キー",
            "継承条件",
            "話題切替条件",
            "適用条件",
            "対象",
            "対象物",
            "対象者",
            "操作",
            "状態",
            "処理段階",
        ),
    ) or title
    direct = _join_fields(properties, ("直接回答", "短い回答"))
    body = _text(properties.get("本文"))
    definitive_answer = "\n\n".join(part for part in (direct, body) if part).strip()
    if not definitive_answer:
        return None
    exceptions = _join_fields(
        properties,
        (
            "回答境界",
            "誤前提",
            "訂正文",
            "禁止断定",
            "非適用条件",
            "競合排除キー",
            "一貫性キー",
            "矛盾禁止キー",
        ),
    ) or "正本に記載された条件・境界を超えて断定しない。"

    kb_id = _text(properties.get("KB ID")) or str(row.get("id", "")) or _sha256(title)[:24]
    combined = "\n".join((title, target_intents, definitive_answer, exceptions))
    digest = _sha256(combined)
    source_identity = "|".join(
        (
            kb_id,
            str(row.get("id", "")),
            str(row.get("url", "")),
            str(row.get("last_edited_time", "")),
        )
    )
    root = _root_domain(category)
    audience = properties.get("対象者") if isinstance(properties.get("対象者"), list) else []
    audience = [str(item) for item in audience if str(item).strip()] or ["general"]
    technical = any(marker in combined.lower() for marker in TECHNICAL_MARKERS)
    answer_level = ["simple", "detailed"] + (["technical_public"] if technical else [])
    disclosure = "public_technical" if technical else "public"
    aliases = _join_fields(properties, ("言い換え", "参照表現", "会話継承キー"))
    keywords = _join_fields(properties, ("検索語", "質問タスク", "適用条件", "対象物", "操作"))
    priority = _text(properties.get("優先度")) or "default"

    return {
        "id": kb_id,
        "url": str(row.get("url", "")),
        "Title": title,
        "Category": category,
        "Target_Intents": target_intents,
        "Definitive_Answer": definitive_answer,
        "Exceptions_and_Limits": exceptions,
        "Status": "公開",
        "_index": {
            "Index_Title": kb_id,
            "Master_Title": title,
            "Canonical_ID": kb_id,
            "Parent_ID": f"v3.{root}",
            "Root_Domain": root,
            "Topic": _slug(category),
            "Subtopic": _slug(_text(properties.get("質問タスク")) or title),
            "Audience": audience,
            "Answer_Level": answer_level,
            "Question_Types": _question_types(title),
            "Aliases": aliases,
            "Keywords": keywords,
            "Related_IDs": "",
            "Source_Page_ID": str(row.get("id", "")),
            "Source_Section": "Astera Customer AI KB v3",
            "Source_Priority": f"v3_priority_{priority}",
            "Source_Last_Edited": str(row.get("last_edited_time", "")),
            "Content_Hash": digest,
            "Source_Hash": _sha256(source_identity),
            "Version": str(row.get("last_edited_time", "")) or digest[:12],
            "Implementation_Status": _text(properties.get("実装状態")) or "unknown",
            "Disclosure_Level": disclosure,
            "Index_Status": ACTIVE_INDEX_STATUS,
            "Effective_From": str(row.get("last_edited_time", ""))[:10],
        },
    }


def _is_v3_client(client: NotionClient) -> bool:
    schema = os.getenv("CUSTOMER_AI_KB_SCHEMA", "auto").strip().lower() or "auto"
    normalized = client.data_source_id.replace("-", "").lower()
    v3_id = V3_DATA_SOURCE_ID.replace("-", "")
    return schema == "v3" or (schema == "auto" and normalized == v3_id)


async def _fetch_pages_with_v3(self: NotionClient) -> list[dict[str, Any]]:
    if not _is_v3_client(self):
        return await _ORIGINAL_FETCH_PAGES(self)
    if not self.token or not self.data_source_id:
        raise NotionSyncError("notion_not_configured")

    allow_candidates = os.getenv("CUSTOMER_AI_KB_ALLOW_VALIDATED_CANDIDATES", "0") == "1"
    rows_by_id: dict[str, dict[str, Any]] = {}
    async with httpx.AsyncClient(timeout=self.timeout_seconds, headers=self.headers) as client:
        for state in V3_PUBLIC_STATES:
            rows = await self._query_all(
                client,
                filter_property="公開状態",
                filter_value=state,
            )
            for row in rows:
                key = str(row.get("id", "")) or str(row.get("url", ""))
                rows_by_id[key] = row

    pages: list[dict[str, Any]] = []
    for row in rows_by_id.values():
        source = row.get("properties", {})
        properties = {
            field: _property_value(source.get(field, {}))
            for field in V3_FIELDS
            if isinstance(source.get(field), dict)
        }
        mapped = map_v3_record(
            properties,
            row,
            allow_validated_candidates=allow_candidates,
        )
        if mapped is not None:
            pages.append(mapped)
    return pages


def install_v3_notion_adapter() -> bool:
    global _INSTALLED
    if _INSTALLED:
        return False
    NotionClient.fetch_pages = _fetch_pages_with_v3  # type: ignore[method-assign]
    _INSTALLED = True
    return True
