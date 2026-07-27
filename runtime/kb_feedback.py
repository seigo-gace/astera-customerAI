from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .security import canonical_json, redact_text, safe_candidate_phrase
from .storage import AtomicStore, NotFoundError


class KBFeedbackStore:
    """Turns real support questions into deduplicated KB improvement candidates."""

    def __init__(self, root: Path):
        self.root = root / "gaps" / "questions"
        self.review_root = root / "gaps" / "review"
        self.store = AtomicStore(root)

    def record_turn(
        self,
        *,
        job_id: str,
        session_id: str,
        topic: str,
        questions: list[dict[str, Any]],
        answered_question_ids: list[str],
        used_evidence_ids: list[str],
        unresolved_questions: list[str],
        requested_information: list[str],
    ) -> list[dict[str, Any]]:
        answered = set(answered_question_ids)
        unresolved_normalized = {" ".join(str(item).split()) for item in unresolved_questions}
        records: list[dict[str, Any]] = []
        for question in questions[:8]:
            question_id = str(question.get("id") or "")[:40]
            raw_text = str(question.get("text") or "")
            text = " ".join(redact_text(raw_text).text.split())[:500]
            if not safe_candidate_phrase(text):
                continue
            classification = self._classify(
                answered=question_id in answered,
                unresolved=text in unresolved_normalized or question_id not in answered,
                evidence_ids=used_evidence_ids,
                requested_information=requested_information,
            )
            signature = hashlib.sha256(canonical_json({"topic": topic, "question": text, "classification": classification})).hexdigest()
            path = self.root / f"{signature}.json"
            previous = self.store.get_json(path) if path.exists() else {}
            source_sessions = list(dict.fromkeys([*(previous.get("source_session_hashes") or []), hashlib.sha256(session_id.encode()).hexdigest()]))[-20:]
            source_jobs = list(dict.fromkeys([*(previous.get("source_job_ids") or []), job_id]))[-20:]
            evidence_ids = list(dict.fromkeys([*(previous.get("evidence_ids") or []), *used_evidence_ids]))[-20:]
            row = {
                "candidate_id": signature,
                "topic": topic[:160],
                "question": text,
                "classification": classification,
                "count": int(previous.get("count", 0)) + 1,
                "evidence_ids": evidence_ids,
                "requested_information": list(dict.fromkeys([*(previous.get("requested_information") or []), *requested_information]))[-12:],
                "source_session_hashes": source_sessions,
                "source_job_ids": source_jobs,
                "resolved_count": int(previous.get("resolved_count", 0)),
                "unresolved_count": int(previous.get("unresolved_count", 0)) + (1 if classification != "known_answered" else 0),
                "first_seen": previous.get("first_seen") or datetime.now(UTC).isoformat(),
                "last_seen": datetime.now(UTC).isoformat(),
                "review_status": previous.get("review_status") or "pending",
                "safe_alias_eligible": bool(classification == "known_answered" and len([item for item in evidence_ids if item.startswith("kb:")]) == 1),
            }
            self.store.put_json(path, row)
            records.append(row)
        return records

    def add_feedback(self, *, job_id: str, resolved: bool, reason: str = "") -> list[dict[str, Any]]:
        updated: list[dict[str, Any]] = []
        clean_reason = " ".join(redact_text(reason).text.split())[:500]
        for path in self.root.glob("*.json"):
            try:
                row = self.store.get_json(path)
            except Exception:
                continue
            if job_id not in row.get("source_job_ids", []):
                continue
            key = "resolved_count" if resolved else "unresolved_count"
            row[key] = int(row.get(key, 0)) + 1
            row["last_feedback_at"] = datetime.now(UTC).isoformat()
            if clean_reason and safe_candidate_phrase(clean_reason):
                reasons = list(dict.fromkeys([*(row.get("feedback_reasons") or []), clean_reason]))[-10:]
                row["feedback_reasons"] = reasons
            if resolved and row.get("safe_alias_eligible"):
                row["review_status"] = "safe_alias_ready"
            elif not resolved:
                row["review_status"] = "content_review_required"
            self.store.put_json(path, row)
            updated.append(row)
        return updated

    def build_review_batch(self, *, limit: int = 50) -> dict[str, Any]:
        rows = self.list_candidates(statuses={"pending", "safe_alias_ready", "content_review_required"}, limit=limit)
        safe_aliases: list[dict[str, Any]] = []
        factual_gaps: list[dict[str, Any]] = []
        for row in rows:
            kb_ids = [item.removeprefix("kb:") for item in row.get("evidence_ids", []) if item.startswith("kb:")]
            if row.get("review_status") == "safe_alias_ready" and len(set(kb_ids)) == 1:
                safe_aliases.append(
                    {
                        "candidate_id": row["candidate_id"],
                        "kb_id": kb_ids[0],
                        "search_term": row["question"],
                        "count": row["count"],
                        "resolved_count": row.get("resolved_count", 0),
                    }
                )
            elif row.get("classification") != "known_answered" or row.get("unresolved_count", 0) > 0:
                factual_gaps.append(row)
        batch = {
            "generated_at": datetime.now(UTC).isoformat(),
            "safe_aliases": safe_aliases,
            "factual_gaps": factual_gaps,
            "candidate_count": len(rows),
        }
        self.store.put_json(self.review_root / "latest.json", batch)
        return batch

    def list_candidates(self, *, statuses: set[str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in self.root.glob("*.json"):
            try:
                row = self.store.get_json(path)
            except Exception:
                continue
            if statuses and row.get("review_status") not in statuses:
                continue
            rows.append(row)
        rows.sort(key=lambda row: (-int(row.get("count", 0)), -int(row.get("unresolved_count", 0)), str(row.get("last_seen", ""))))
        return rows[:limit]

    def get(self, candidate_id: str) -> dict[str, Any]:
        path = self.root / f"{candidate_id}.json"
        if not path.exists():
            raise NotFoundError(candidate_id)
        return self.store.get_json(path)

    def mark(self, candidate_id: str, *, status: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        row = self.get(candidate_id)
        row["review_status"] = status
        row["reviewed_at"] = datetime.now(UTC).isoformat()
        if metadata:
            row["review_metadata"] = metadata
        self.store.put_json(self.root / f"{candidate_id}.json", row)
        return row

    @staticmethod
    def _classify(*, answered: bool, unresolved: bool, evidence_ids: list[str], requested_information: list[str]) -> str:
        if answered and evidence_ids:
            return "known_answered"
        if requested_information:
            return "missing_follow_up_or_condition"
        if unresolved and evidence_ids:
            return "kb_content_or_relation_gap"
        if unresolved:
            return "missing_kb_page"
        return "question_variant"
