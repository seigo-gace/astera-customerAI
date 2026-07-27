from __future__ import annotations

import json
import os
import sqlite3
import tempfile
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

    def _manifest_path(self) -> Path:
        return self.root / "manifest.json"

    def current(self) -> SnapshotInfo | None:
        path = self._manifest_path()
        if not path.exists():
            return None
        manifest = json.loads(path.read_text(encoding="utf-8"))
        snapshot = self.root / "snapshots" / manifest["version"] / "kb.sqlite"
        return SnapshotInfo(version=manifest["version"], path=snapshot)

    def open(self) -> bool:
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
        if not self.open() or self._connection is None or not self._version:
            return []
        normalized_query = " ".join(query.split())[:1000]
        cache_key = (self._version, normalized_query, limit)
        now = time.monotonic()
        cached = self._query_cache.get(cache_key)
        if cached and cached[0] > now:
            self._query_cache.move_to_end(cache_key)
            return [KBHit.model_validate(item.model_dump()) for item in cached[1]]
        if cached:
            self._query_cache.pop(cache_key, None)

        terms = [part.strip().replace("%", "").replace("_", "") for part in normalized_query.split() if part.strip()][:12]
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
        hits = [
            KBHit(
                kb_id=row["kb_id"],
                question=row["question"],
                short_answer=row["short_answer"],
                body=row["body"],
                score=float(row["score"]),
                answer_boundary=row["answer_boundary"],
                target=row["target"],
            )
            for row in rows
        ]
        self._query_cache[cache_key] = (now + self._cache_ttl_seconds, hits)
        self._query_cache.move_to_end(cache_key)
        while len(self._query_cache) > self._cache_max_entries:
            self._query_cache.popitem(last=False)
        return [KBHit.model_validate(item.model_dump()) for item in hits]

    def cache_status(self) -> dict[str, int]:
        return {"entries": len(self._query_cache), "max_entries": self._cache_max_entries}

    def build_snapshot(self, *, version: str, pages: Iterable[dict[str, Any]]) -> SnapshotInfo:
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
                connection.execute("INSERT INTO kb_fts VALUES (?, ?, ?, ?, ?)", (normalized[0], normalized[1], normalized[2], normalized[3], normalized[4]))
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


def normalize_page(page: dict[str, Any]) -> tuple[Any, ...] | None:
    if page.get("公開状態") != "公開":
        return None
    if page.get("実装状態") not in {"実装済み", "文書確認済み"}:
        return None
    if page.get("要再確認") in (True, "__YES__", 1):
        return None
    question = redact_text(str(page.get("質問", ""))).text.strip()
    short = redact_text(str(page.get("短い回答", ""))).text.strip()
    body = redact_text(str(page.get("本文", page.get("content", "")))).text.strip()
    boundary = redact_text(str(page.get("回答境界", ""))).text.strip()
    if not question or not short or not body:
        return None
    if contains_internal_implementation("\n".join((question, short, body, boundary))):
        return None
    return (
        str(page.get("id") or page.get("url") or question),
        question,
        short,
        body,
        str(page.get("検索語", "")),
        boundary,
        ",".join(page.get("対象", [])) if isinstance(page.get("対象"), list) else str(page.get("対象", "")),
        str(page.get("公開状態")),
        str(page.get("実装状態")),
        0,
        str(page.get("url", "")),
        str(page.get("確認日", "")),
    )
