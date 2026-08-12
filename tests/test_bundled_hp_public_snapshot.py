from __future__ import annotations

import json
import re
from pathlib import Path

from runtime.bundled_snapshot import (
    EXPECTED_SOURCE_HASH,
    load_bundled_pages,
)
from runtime.kb import KBIndex


def test_bundled_hp_public_snapshot_is_exact_and_public() -> None:
    pages, metadata = load_bundled_pages()
    assert metadata["source_sha256"] == EXPECTED_SOURCE_HASH
    assert metadata["schema_version"] == "customerai_master_v2_hp_public_bundle_v2"
    assert metadata["page_count"] == 24
    assert len(pages) == 24
    assert len({page["Title"] for page in pages}) == 24
    assert all(page["Status"] == "公開" for page in pages)
    assert all(
        page[field]
        for page in pages
        for field in (
            "Title",
            "Category",
            "Target_Intents",
            "Definitive_Answer",
            "Exceptions_and_Limits",
            "Status",
        )
    )


def test_bundled_hp_public_snapshot_contains_latest_hp_contract() -> None:
    pages, _ = load_bundled_pages()
    serialized = json.dumps(pages, ensure_ascii=False)
    for required in (
        "外付けAI強化外装",
        "Google V8",
        "多重並列思考",
        "本当の目的",
        "前提不足",
        "事実確認",
        "危機察知",
        "反対視点",
        "比較案",
        "推奨判断",
        "主役AIへの再指示",
        "Copy",
        "Web Form",
        "製品API",
        "Webhook",
        "Customer AI専用には改造していません",
    ):
        assert required in serialized
    for forbidden in ("月額2,000円", "Stripe", "旧KAGURA"):
        assert forbidden not in serialized
    assert re.search(r"(?<![\d,])9,800円", serialized) is None


def test_bundled_hp_public_snapshot_builds_and_retrieves(tmp_path: Path) -> None:
    pages, metadata = load_bundled_pages()
    index = KBIndex(tmp_path)
    info = index.build_snapshot(
        version="hp-public-test",
        pages=pages,
    )
    assert info.path.exists()
    manifest = json.loads((tmp_path / "kb" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["page_count"] == metadata["page_count"]
    assert manifest["index_count"] == 0

    queries = {
        "Astera": ("外付けAI強化外装", "主役AI"),
        "判断素材 8項目": ("本当の目的", "主役AIへの再指示"),
        "Google V8 生成AI": ("V8", "生成AI Model"),
        "多重 並列 依存": ("並列", "依存"),
        "判断材料 Copy Webhook": ("Copy", "Webhook"),
        "Browser Private HF Cloudflare": ("接続しません", "Cloudflare"),
        "Gateway Customer AI 専用 汎用": ("汎用", "専用"),
        "KB Model 補いません": ("補いません",),
    }
    for query, terms in queries.items():
        hits = index.search(query, limit=5)
        assert hits, query
        text = "\n".join(f"{hit.short_answer}\n{hit.answer_boundary}" for hit in hits)
        for term in terms:
            assert term in text, (query, term, text)
