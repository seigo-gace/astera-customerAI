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


SCHEMA_VERSION = "customerai_master_v2"
PUBLIC_STATUS = "公開"
REQUIRED_V2_FIELDS = (
    "Title",
    "Category",
    "Target_Intents",
    "Definitive_Answer",
    "Exceptions_and_Limits",
    "Status",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_pages (
  kb_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  target_intents TEXT NOT NULL,
  definitive_answer TEXT NOT NULL,
  exceptions_and_limits TEXT NOT NULL,
  category TEXT NOT NULL,
  source_url TEXT NOT NULL DEFAULT ''
);
CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(
  kb_id UNINDEXED,
  title,
  target_intents,
  definitive_answer,
  exceptions_and_limits,
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
            if manifest.get("schema_version") != SCHEMA_VERSION:
                return None
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
            columns = {row[1] for row in new.execute("PRAGMA table_info(kb_pages)").fetchall()}
            if not {"title", "target_intents", "definitive_answer", "exceptions_and_limits"}.issubset(columns):
                new.close()
                return False
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
                    "(CASE WHEN title LIKE ? THEN 12 ELSE 0 END + "
                    "CASE WHEN target_intents LIKE ? THEN 10 ELSE 0 END + "
                    "CASE WHEN definitive_answer LIKE ? THEN 5 ELSE 0 END + "
                    "CASE WHEN exceptions_and_limits LIKE ? THEN 2 ELSE 0 END)"
                )
                score_params.extend([pattern, pattern, pattern, pattern])
                where_parts.append(
                    "(title LIKE ? OR target_intents LIKE ? OR definitive_answer LIKE ? OR exceptions_and_limits LIKE ?)"
                )
                where_params.extend([pattern, pattern, pattern, pattern])
            sql = (
                "SELECT *, (" + " + ".join(score_parts) + ") AS score FROM kb_pages WHERE "
                + " OR ".join(where_parts)
                + " ORDER BY score DESC, kb_id ASC LIMIT ?"
            )
            rows = self._connection.execute(sql, [*score_params, *where_params, limit]).fetchall()
            hits: list[KBHit] = []
            for row in rows:
                score = float(row["score"] or 0.0)
                if score < MIN_EVIDENCE_SCORE:
                    continue
                answer = row["definitive_answer"]
                hits.append(
                    KBHit(
                        kb_id=row["kb_id"],
                        question=row["title"],
                        short_answer=answer,
                        body=answer,
                        score=score,
                        answer_boundary=row["exceptions_and_limits"],
                        target=row["target_intents"],
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
                    connection.execute("INSERT INTO kb_pages VALUES (?, ?, ?, ?, ?, ?, ?)", normalized)
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
                    raise RuntimeError("snapshot contains no publishable CustomerAI_Master_v2 pages")
            finally:
                connection.close()
            final = snapshot_dir / "kb.sqlite"
            os.replace(temp, final)
            manifest_tmp = self.root / ".manifest.tmp"
            manifest_tmp.write_text(
                json.dumps(
                    {"version": version, "page_count": accepted, "schema_version": SCHEMA_VERSION},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
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


def normalize_page(page: dict[str, Any]) -> tuple[Any, ...] | None:
    if page.get("Status") != PUBLIC_STATUS:
        return None
    if any(field not in page for field in REQUIRED_V2_FIELDS):
        return None

    title = redact_text(str(page.get("Title", ""))).text.strip()
    category = redact_text(str(page.get("Category", ""))).text.strip()
    target_intents = redact_text(str(page.get("Target_Intents", ""))).text.strip()
    definitive_answer = redact_text(str(page.get("Definitive_Answer", ""))).text.strip()
    exceptions = redact_text(str(page.get("Exceptions_and_Limits", ""))).text.strip()
    if not all((title, category, target_intents, definitive_answer, exceptions)):
        return None
    if contains_internal_implementation("\n".join((title, target_intents, definitive_answer, exceptions))):
        return None

    kb_id = str(page.get("id") or page.get("url") or title)
    return (
        kb_id,
        title,
        target_intents,
        definitive_answer,
        exceptions,
        category,
        str(page.get("url", "")),
    )
