from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .schemas import KBHit
from .security import contains_internal_implementation, redact_text


SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_pages (
  kb_id TEXT PRIMARY KEY,
  question TEXT NOT NULL,
  short_answer TEXT NOT NULL,
  body TEXT NOT NULL,
  search_terms TEXT NOT NULL DEFAULT '',
  answer_boundary TEXT NOT NULL DEFAULT '',
  target TEXT NOT NULL DEFAULT '',
  public_status TEXT NOT NULL,
  implementation_status TEXT NOT NULL,
  needs_review INTEGER NOT NULL DEFAULT 0,
  source_url TEXT NOT NULL DEFAULT '',
  verified_at TEXT NOT NULL DEFAULT ''
);
CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(
  kb_id UNINDEXED,
  question,
  short_answer,
  body,
  search_terms,
  tokenize='unicode61'
);
"""
MIN_EVIDENCE_SCORE = 5.0
QUERY_ALIASES: dict[str, tuple[str, ...]] = {
    "credit": ("クレジット", "残高", "付与", "決済"),
    "billing": ("料金", "価格", "費用", "プラン"),
    "account": ("アカウント", "ログイン", "認証", "退会", "削除"),
    "webhook-gateway": ("Webhook Gateway", "webhook", "配送", "再送", "復旧"),
    "astera": ("Astera", "アステラ", "判断材料", "主役AI"),
    "api": ("API", "APIキー", "連携", "エンドポイント"),
    "procedure": ("方法", "手順", "確認"),
    "troubleshooting": ("不具合", "エラー", "反映されない", "確認"),
    "pricing": ("料金", "価格", "いくら", "費用"),
    "comparison": ("違い", "比較"),
}
QUERY_STOP_TERMS = {
    "すか",
    "ですか",
    "ますか",
    "現在",
    "system",
    "prompt",
    "internal",
    "admin",
    "env",
    "内容",
    "全部",
    "全部出して",
    "教えて",
    "ください",
}


@dataclass(slots=True)
class SnapshotInfo:
    version: str
    path: Path


class KBIndex:
    def __init__(self, root: Path, *, cache_ttl_seconds: int = 120, cache_max_entries: int = 256):
        self.root = root / "kb"
        self.root.mkdir(parents=True, exist_ok=True)
        self._connection: sqlite3.Connection | None = None
        self._version: str | None = None
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache_max_entries = cache_max_entries
        self._query_cache: OrderedDict[tuple[str, str, int], tuple[float, list[KBHit]]] = OrderedDict()
        self._lock = threading.RLock()

    def _manifest_path(self) -> Path:
        return self.root / "manifest.json"

    def current(self) -> SnapshotInfo | None:
        with self._lock:
            path = self._manifest_path()
            if not path.exists():
                return None
            manifest = json.loads(path.read_text(encoding="utf-8"))
            snapshot = self.root / "snapshots" / manifest["version"] / "kb.sqlite"
            return SnapshotInfo(version=manifest["version"], path=snapshot)

    def open(self) -> bool:
        with self._lock:
            info = self.current()
            if not info or not info.path.exists():
                return False
            if self._connection is not None and self._version == info.version:
                return True
            new = sqlite3.connect(f"file:{info.path}?mode=ro&immutable=1", uri=True, check_same_thread=False)
            new.row_factory = sqlite3.Row
            old = self._connection
            self._connection = new
            self._version = info.version
            self._query_cache.clear()
            if old is not None:
                old.close()
            return True

    def search(self, query: str, *, limit: int = 5) -> list[KBHit]:
        with self._lock:
            if not self.open() or self._connection is None or not self._version:
                return []
            normalized_query = " ".join(query.split())[:1000]
            expanded_terms = self._expand_terms(normalized_query)
            expanded_query = " ".join(expanded_terms)
            cache_key = (self._version, expanded_query, limit)
            now = time.monotonic()
            cached = self._query_cache.get(cache_key)
            if cached and cached[0] > now:
                self._query_cache.move_to_end(cache_key)
                return [KBHit.model_validate(item.model_dump()) for item in cached[1]]
            if cached:
                self._query_cache.pop(cache_key, None)

            terms = [part.strip().replace("%", "").replace("_", "") for part in expanded_terms if part.strip()][:20]
            if not terms:
                return []
            score_parts: list[str] = []
            where_parts: list[str] = []
            score_params: list[Any] = []
            where_params: list[Any] = []
            for term in terms:
                pattern = f"%{term}%"
                score_parts.append(
                    "(CASE WHEN question LIKE ? THEN 12 ELSE 0 END + "
                    "CASE WHEN search_terms LIKE ? THEN 10 ELSE 0 END + "
                    "CASE WHEN short_answer LIKE ? THEN 5 ELSE 0 END + "
                    "CASE WHEN body LIKE ? THEN 2 ELSE 0 END)"
                )
                score_params.extend([pattern, pattern, pattern, pattern])
                where_parts.append("(question LIKE ? OR search_terms LIKE ? OR short_answer LIKE ? OR body LIKE ?)")
                where_params.extend([pattern, pattern, pattern, pattern])
            sql = (
                "SELECT *, (" + " + ".join(score_parts) + ") AS score FROM kb_pages WHERE "
                + " OR ".join(where_parts)
                + " ORDER BY score DESC, kb_id ASC LIMIT ?"
            )
            params = [*score_params, *where_params, limit]
            rows = self._connection.execute(sql, params).fetchall()
            hits: list[KBHit] = []
            for row in rows:
                score = float(row["score"] or 0.0)
                if score < MIN_EVIDENCE_SCORE:
                    continue
                hits.append(
                    KBHit(
                        kb_id=row["kb_id"],
                        question=row["question"],
                        short_answer=row["short_answer"],
                        body=row["body"],
                        score=score,
                        answer_boundary=row["answer_boundary"],
                        target=row["target"],
                    )
                )
            self._query_cache[cache_key] = (now + self._cache_ttl_seconds, hits)
            self._query_cache.move_to_end(cache_key)
            while len(self._query_cache) > self._cache_max_entries:
                self._query_cache.popitem(last=False)
            return [KBHit.model_validate(item.model_dump()) for item in hits]

    def cache_status(self) -> dict[str, int]:
        with self._lock:
            return {"entries": len(self._query_cache), "max_entries": self._cache_max_entries}

    def build_snapshot(self, *, version: str, pages: Iterable[dict[str, Any]]) -> SnapshotInfo:
        with self._lock:
            snapshot_dir = self.root / "snapshots" / version
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(suffix=".sqlite", dir=snapshot_dir)
            os.close(fd)
            temp = Path(temp_name)
            connection = sqlite3.connect(temp)
            try:
                connection.executescript(SCHEMA)
                accepted = 0
                for page in pages:
                    normalized = normalize_page(page)
                    if normalized is None:
                        continue
                    connection.execute("INSERT INTO kb_pages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", normalized)
                    connection.execute(
                        "INSERT INTO kb_fts VALUES (?, ?, ?, ?, ?)",
                        (normalized[0], normalized[1], normalized[2], normalized[3], normalized[4]),
                    )
                    accepted += 1
                connection.commit()
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise RuntimeError(f"SQLite integrity check failed: {integrity}")
                if accepted == 0:
                    raise RuntimeError("snapshot contains no publishable KB pages")
            finally:
                connection.close()
            final = snapshot_dir / "kb.sqlite"
            os.replace(temp, final)
            manifest_tmp = self.root / ".manifest.tmp"
            manifest_tmp.write_text(json.dumps({"version": version, "page_count": accepted}), encoding="utf-8")
            os.replace(manifest_tmp, self._manifest_path())
            self._query_cache.clear()
            return SnapshotInfo(version=version, path=final)

    @staticmethod
    def _expand_terms(query: str) -> list[str]:
        raw = [part for part in query.split() if part][:12]
        expanded: list[str] = []
        for term in raw:
            normalized = term.strip()
            key = normalized.lower()
            if not normalized or key in QUERY_STOP_TERMS:
                continue
            if normalized not in expanded:
                expanded.append(normalized)
            for alias in QUERY_ALIASES.get(key, ()):
                if alias not in expanded:
                    expanded.append(alias)
        return expanded[:20]


def _lines(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_lines(item))
        return result
    if isinstance(value, dict):
        return [f"{key}: {item}" for key, item in value.items() if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _section(title: str, value: Any, *, numbered: bool = False) -> str:
    values = _lines(value)
    if not values:
        return ""
    if numbered:
        content = "\n".join(f"{index}. {item}" for index, item in enumerate(values, start=1))
    else:
        content = "\n".join(f"- {item}" for item in values)
    return f"## {title}\n\n{content}"


def compose_structured_body(page: dict[str, Any]) -> str:
    sections = [
        _section("目的・概要", page.get("目的")),
        _section("理由", page.get("理由")),
        _section("前提条件", page.get("前提条件")),
        _section("利用条件", page.get("利用条件")),
        _section("手順", page.get("手順"), numbered=True),
        _section("条件", page.get("条件")),
        _section("例外", page.get("例外")),
        _section("障害症状", page.get("障害症状")),
        _section("考えられる原因", page.get("原因")),
        _section("確認方法", page.get("確認方法"), numbered=True),
        _section("解決手順", page.get("解決手順"), numbered=True),
        _section("完了確認", page.get("完了確認")),
        _section("追加質問への対応", page.get("追加質問")),
        _section("関連KB", page.get("関連KB")),
    ]
    return "\n\n".join(section for section in sections if section).strip()


def _search_terms(page: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("製品", "システム", "カテゴリ", "質問種別", "検索語", "同義語", "追加質問"):
        values.extend(_lines(page.get(key)))
    return " ".join(dict.fromkeys(values))


def _answer_boundary(page: dict[str, Any]) -> str:
    values = [*_lines(page.get("回答境界")), *_lines(page.get("禁止回答"))]
    return "\n".join(dict.fromkeys(values))


def normalize_page(page: dict[str, Any]) -> tuple[Any, ...] | None:
    if page.get("公開状態") != "公開":
        return None
    if page.get("実装状態") not in {"実装済み", "文書確認済み"}:
        return None
    if page.get("要再確認") in (True, "__YES__", 1):
        return None
    question = redact_text(str(page.get("質問", ""))).text.strip()
    short = redact_text(str(page.get("短い回答", ""))).text.strip()
    raw_body = str(page.get("本文", "")).strip() or compose_structured_body(page)
    body = redact_text(raw_body).text.strip()
    boundary = redact_text(_answer_boundary(page)).text.strip()
    if not question or not short or not body:
        return None
    if contains_internal_implementation("\n".join((question, short, body, boundary))):
        return None
    return (
        str(page.get("id") or page.get("url") or question),
        question,
        short,
        body,
        _search_terms(page),
        boundary,
        ",".join(page.get("対象", [])) if isinstance(page.get("対象"), list) else str(page.get("対象", "")),
        str(page.get("公開状態")),
        str(page.get("実装状態")),
        0,
        str(page.get("url", "")),
        str(page.get("確認日", "")),
    )
