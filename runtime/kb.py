from __future__ import annotations

import json
import os
import sqlite3
import string
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .schemas import KBHit
from .security import contains_internal_implementation, redact_text


SCHEMA_VERSION = "customerai_master_v2_index_v2"
PUBLIC_STATUS = "公開"
ACTIVE_INDEX_STATUS = "active"
PUBLIC_DISCLOSURE_LEVELS = {"public", "public_technical"}
REQUIRED_V2_FIELDS = (
    "Title",
    "Category",
    "Target_Intents",
    "Definitive_Answer",
    "Exceptions_and_Limits",
    "Status",
)
REQUIRED_INDEX_FIELDS = (
    "Master_Title",
    "Canonical_ID",
    "Disclosure_Level",
    "Index_Status",
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
CREATE TABLE IF NOT EXISTS kb_metadata (
  master_title TEXT PRIMARY KEY,
  canonical_id TEXT UNIQUE NOT NULL,
  parent_id TEXT NOT NULL DEFAULT '',
  root_domain TEXT NOT NULL DEFAULT '',
  topic TEXT NOT NULL DEFAULT '',
  subtopic TEXT NOT NULL DEFAULT '',
  audience TEXT NOT NULL DEFAULT '',
  answer_level TEXT NOT NULL DEFAULT '',
  question_types TEXT NOT NULL DEFAULT '',
  aliases TEXT NOT NULL DEFAULT '',
  keywords TEXT NOT NULL DEFAULT '',
  related_ids TEXT NOT NULL DEFAULT '',
  content_hash TEXT NOT NULL DEFAULT '',
  source_hash TEXT NOT NULL DEFAULT '',
  version TEXT NOT NULL DEFAULT '',
  implementation_status TEXT NOT NULL DEFAULT '',
  disclosure_level TEXT NOT NULL DEFAULT '',
  index_status TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_kb_metadata_parent ON kb_metadata(parent_id);
CREATE INDEX IF NOT EXISTS idx_kb_metadata_domain ON kb_metadata(root_domain, topic, subtopic);
CREATE INDEX IF NOT EXISTS idx_kb_metadata_status ON kb_metadata(index_status, disclosure_level);
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
    "architecture": ("構造", "アーキテクチャ", "責務", "データフロー"),
    "module": ("モジュール", "Module", "責務", "境界"),
    "private": ("Private Mode", "非保存", "一時データ", "Cleanup"),
    "square": ("Square", "決済", "Webhook", "Subscription"),
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
TECHNICAL_TERMS = {
    "architecture",
    "module",
    "runtime",
    "worker",
    "queue",
    "webhook",
    "endpoint",
    "request",
    "response",
    "dataflow",
    "データフロー",
    "構造",
    "設計",
    "実装",
    "責務",
    "境界",
    "並列",
    "冪等",
    "idempotency",
    "hash",
    "session",
    "cookie",
    "csrf",
    "retry",
    "timeout",
    "cleanup",
    "crash",
    "api",
    "署名",
    "暗号化",
    "認証",
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
            page_columns = {row[1] for row in new.execute("PRAGMA table_info(kb_pages)").fetchall()}
            metadata_columns = {row[1] for row in new.execute("PRAGMA table_info(kb_metadata)").fetchall()}
            if not {"title", "target_intents", "definitive_answer", "exceptions_and_limits"}.issubset(page_columns):
                new.close()
                return False
            if not {"master_title", "canonical_id", "audience", "answer_level", "aliases", "keywords"}.issubset(metadata_columns):
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
            technical_query = self._is_technical_query(normalized_query, terms)
            score_parts: list[str] = []
            where_parts: list[str] = []
            score_params: list[Any] = []
            where_params: list[Any] = []
            for term in terms:
                pattern = f"%{term}%"
                score_parts.append(
                    "(CASE WHEN p.title LIKE ? THEN 12 ELSE 0 END + "
                    "CASE WHEN p.target_intents LIKE ? THEN 10 ELSE 0 END + "
                    "CASE WHEN p.definitive_answer LIKE ? THEN 5 ELSE 0 END + "
                    "CASE WHEN p.exceptions_and_limits LIKE ? THEN 2 ELSE 0 END + "
                    "CASE WHEN m.aliases LIKE ? THEN 12 ELSE 0 END + "
                    "CASE WHEN m.keywords LIKE ? THEN 8 ELSE 0 END + "
                    "CASE WHEN m.canonical_id LIKE ? THEN 8 ELSE 0 END + "
                    "CASE WHEN m.topic LIKE ? THEN 6 ELSE 0 END + "
                    "CASE WHEN m.subtopic LIKE ? THEN 6 ELSE 0 END + "
                    "CASE WHEN m.parent_id LIKE ? THEN 3 ELSE 0 END)"
                )
                score_params.extend([pattern] * 10)
                where_parts.append(
                    "(p.title LIKE ? OR p.target_intents LIKE ? OR p.definitive_answer LIKE ? OR "
                    "p.exceptions_and_limits LIKE ? OR m.aliases LIKE ? OR m.keywords LIKE ? OR "
                    "m.canonical_id LIKE ? OR m.topic LIKE ? OR m.subtopic LIKE ? OR m.parent_id LIKE ?)"
                )
                where_params.extend([pattern] * 10)
            technical_bonus = (
                " + CASE WHEN ? = 1 AND (m.audience LIKE '%developer%' OR "
                "m.answer_level LIKE '%technical_public%') THEN 8 ELSE 0 END"
            )
            sql = (
                "SELECT p.*, m.canonical_id, m.parent_id, m.related_ids, m.audience, "
                "m.answer_level, m.implementation_status, ("
                + " + ".join(score_parts)
                + technical_bonus
                + ") AS score FROM kb_pages p "
                "LEFT JOIN kb_metadata m ON m.master_title = p.title WHERE "
                + " OR ".join(where_parts)
                + " ORDER BY score DESC, p.kb_id ASC LIMIT ?"
            )
            row_limit = max(limit * 4, limit)
            rows = self._connection.execute(
                sql,
                [*score_params, 1 if technical_query else 0, *where_params, row_limit],
            ).fetchall()
            hits: list[KBHit] = []
            seen: set[str] = set()
            relation_ids: list[str] = []
            for row in rows:
                score = float(row["score"] or 0.0)
                if score < MIN_EVIDENCE_SCORE:
                    continue
                hit = self._hit_from_row(row, score)
                if hit.kb_id in seen:
                    continue
                hits.append(hit)
                seen.add(hit.kb_id)
                if len(hits) <= 2:
                    relation_ids.extend(self._relation_ids(row))
                if len(hits) >= limit:
                    break

            if len(hits) < limit and relation_ids:
                for row in self._relation_rows(relation_ids, limit=limit - len(hits)):
                    if row["kb_id"] in seen:
                        continue
                    hits.append(self._hit_from_row(row, MIN_EVIDENCE_SCORE))
                    seen.add(row["kb_id"])
                    if len(hits) >= limit:
                        break

            self._query_cache[cache_key] = (now + self._cache_ttl_seconds, hits)
            self._query_cache.move_to_end(cache_key)
            while len(self._query_cache) > self._cache_max_entries:
                self._query_cache.popitem(last=False)
            return [KBHit.model_validate(item.model_dump()) for item in hits]

    @staticmethod
    def _hit_from_row(row: sqlite3.Row, score: float) -> KBHit:
        answer = row["definitive_answer"]
        return KBHit(
            kb_id=row["kb_id"],
            question=row["title"],
            short_answer=answer,
            body=answer,
            score=score,
            answer_boundary=row["exceptions_and_limits"],
            target=row["target_intents"],
        )

    @staticmethod
    def _relation_ids(row: sqlite3.Row) -> list[str]:
        values: list[str] = []
        parent = str(row["parent_id"] or "").strip()
        if parent:
            values.append(parent)
        for item in str(row["related_ids"] or "").replace(",", "\n").splitlines():
            clean = item.strip()
            if clean and clean not in values:
                values.append(clean)
        return values[:8]

    def _relation_rows(self, canonical_ids: list[str], *, limit: int) -> list[sqlite3.Row]:
        if not canonical_ids or self._connection is None or limit <= 0:
            return []
        unique = list(dict.fromkeys(canonical_ids))[:8]
        placeholders = ",".join("?" for _ in unique)
        sql = (
            "SELECT p.*, m.canonical_id, m.parent_id, m.related_ids, m.audience, "
            "m.answer_level, m.implementation_status FROM kb_metadata m "
            "JOIN kb_pages p ON p.title = m.master_title "
            f"WHERE m.canonical_id IN ({placeholders}) LIMIT ?"
        )
        return self._connection.execute(sql, [*unique, limit]).fetchall()

    def cache_status(self) -> dict[str, int]:
        with self._lock:
            return {"entries": len(self._query_cache), "max_entries": self._cache_max_entries}

    def build_snapshot(
        self,
        *,
        version: str,
        pages: Iterable[dict[str, Any]],
        index_records: Iterable[dict[str, Any]] = (),
    ) -> SnapshotInfo:
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
                indexed = 0
                embedded_index_records: list[dict[str, Any]] = []
                for page in pages:
                    normalized = normalize_page(page)
                    if normalized is None:
                        continue
                    connection.execute("INSERT INTO kb_pages VALUES (?, ?, ?, ?, ?, ?, ?)", normalized)
                    connection.execute(
                        "INSERT INTO kb_fts VALUES (?, ?, ?, ?, ?)",
                        (normalized[0], normalized[1], normalized[2], normalized[3], normalized[4]),
                    )
                    embedded = page.get("_index")
                    if isinstance(embedded, dict):
                        embedded_index_records.append(embedded)
                    accepted += 1
                for record in [*embedded_index_records, *list(index_records)]:
                    normalized_index = normalize_index_record(record)
                    if normalized_index is None:
                        continue
                    master_exists = connection.execute(
                        "SELECT 1 FROM kb_pages WHERE title = ?",
                        (normalized_index[0],),
                    ).fetchone()
                    if not master_exists:
                        continue
                    connection.execute(
                        "INSERT OR REPLACE INTO kb_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        normalized_index,
                    )
                    indexed += 1
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
                    {
                        "version": version,
                        "page_count": accepted,
                        "index_count": indexed,
                        "schema_version": SCHEMA_VERSION,
                    },
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

    @staticmethod
    def _is_technical_query(query: str, terms: list[str]) -> bool:
        lowered = query.lower()
        return any(term.lower() in lowered for term in TECHNICAL_TERMS) or any(
            term.lower() in TECHNICAL_TERMS for term in terms
        )


def _text_list(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


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


def normalize_index_record(record: dict[str, Any]) -> tuple[Any, ...] | None:
    if any(field not in record for field in REQUIRED_INDEX_FIELDS):
        return None
    if str(record.get("Index_Status", "")) != ACTIVE_INDEX_STATUS:
        return None
    disclosure = str(record.get("Disclosure_Level", ""))
    if disclosure not in PUBLIC_DISCLOSURE_LEVELS:
        return None

    master_title = redact_text(str(record.get("Master_Title", ""))).text.strip()
    canonical_id = redact_text(str(record.get("Canonical_ID", ""))).text.strip()
    if not master_title or not canonical_id:
        return None
    text_values = (
        master_title,
        canonical_id,
        _text_list(record.get("Parent_ID")),
        _text_list(record.get("Root_Domain")),
        _text_list(record.get("Topic")),
        _text_list(record.get("Subtopic")),
        _text_list(record.get("Audience")),
        _text_list(record.get("Answer_Level")),
        _text_list(record.get("Question_Types")),
        _text_list(record.get("Aliases")),
        _text_list(record.get("Keywords")),
        _text_list(record.get("Related_IDs")),
    )
    return (
        *(redact_text(str(value)).text for value in text_values),
        _hash_value(record.get("Content_Hash")),
        _hash_value(record.get("Source_Hash")),
        redact_text(_text_list(record.get("Version"))).text,
        redact_text(_text_list(record.get("Implementation_Status"))).text,
        disclosure,
        ACTIVE_INDEX_STATUS,
    )


def _hash_value(value: Any) -> str:
    clean = str(value or "").strip().lower()
    if len(clean) == 64 and all(character in string.hexdigits for character in clean):
        return clean
    return ""
