from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .notion import NotionClient

BUNDLED_NOTION_TOKEN = "bundled:hp-public-v2"
EXPECTED_SCHEMA = "customerai_master_v2_hp_public_bundle_v2"
EXPECTED_SOURCE_HASH = "8c2de4259b00a4c64dc175bb76ed7187387db1c127e2f3de66fc21278490d8f5"
REQUIRED_FIELDS = (
    "Title",
    "Category",
    "Target_Intents",
    "Definitive_Answer",
    "Exceptions_and_Limits",
    "Status",
)
DEFAULT_BUNDLE_NAMES = (
    "bundled-hp-public-v2.json",
    "bundled-hp-public-boundary-v2.json",
)


def _bundle_paths() -> list[Path]:
    configured = os.getenv("CUSTOMER_AI_BUNDLED_KB_PATH", "").strip()
    if configured:
        candidate = Path(configured)
        if candidate.exists():
            return [candidate]
    root = Path(__file__).resolve().parents[1] / "kb"
    return [root / name for name in DEFAULT_BUNDLE_NAMES]


def _load_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != EXPECTED_SCHEMA:
        raise RuntimeError("bundled_kb_schema_invalid")
    if payload.get("source_sha256") != EXPECTED_SOURCE_HASH:
        raise RuntimeError("bundled_kb_source_hash_invalid")
    raw_pages = payload.get("pages")
    if not isinstance(raw_pages, list) or not raw_pages:
        raise RuntimeError("bundled_kb_pages_missing")
    return payload


def load_bundled_pages() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    paths = _bundle_paths()
    pages: list[dict[str, Any]] = []
    titles: set[str] = set()
    effective_dates: list[str] = []

    for path in paths:
        payload = _load_payload(path)
        effective_dates.append(str(payload.get("effective_date", "")))
        for raw in payload["pages"]:
            if not isinstance(raw, dict):
                raise RuntimeError("bundled_kb_page_invalid")
            missing = [field for field in REQUIRED_FIELDS if not str(raw.get(field, "")).strip()]
            if missing:
                raise RuntimeError("bundled_kb_required_field_missing:" + ",".join(missing))
            if raw.get("Status") != "公開":
                raise RuntimeError("bundled_kb_non_public_page")
            title = str(raw["Title"]).strip()
            if title in titles:
                raise RuntimeError("bundled_kb_duplicate_title:" + title)
            titles.add(title)
            pages.append(dict(raw))

    metadata = {
        "mode": "bundled_hp_public",
        "paths": [str(path) for path in paths],
        "schema_version": EXPECTED_SCHEMA,
        "source_sha256": EXPECTED_SOURCE_HASH,
        "effective_date": max(effective_dates),
        "page_count": len(pages),
    }
    return pages, metadata


async def _fetch_bundled_pages(self: NotionClient) -> list[dict[str, Any]]:
    pages, _ = load_bundled_pages()
    return pages


def install_bundled_notion_fallback() -> bool:
    if os.getenv("NOTION_TOKEN", "").strip() != BUNDLED_NOTION_TOKEN:
        return False
    NotionClient.fetch_pages = _fetch_bundled_pages  # type: ignore[method-assign]
    return True
