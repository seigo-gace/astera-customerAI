from __future__ import annotations

import hashlib
import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .schemas import KBHit, SessionContext
from .security import canonical_json, redact_text, safe_candidate_phrase
from .storage import AtomicStore


@dataclass(slots=True)
class EvidenceItem:
    evidence_id: str
    kind: str
    title: str
    summary: str
    body: str
    boundary: str
    verified: bool
    metadata: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind,
            "title": self.title,
            "summary": self.summary,
            "body": self.body,
            "boundary": self.boundary,
            "verified": self.verified,
            "metadata": self.metadata,
        }


class ResponseCache:
    """Caches only a fully verified answer for the same compact context and evidence set."""

    def __init__(self, *, ttl_seconds: int, max_entries: int):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._entries: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()

    def key(self, *, message: str, locale: str, context: dict[str, Any], evidence_ids: list[str]) -> str:
        payload = {
            "message": " ".join(message.split())[:4000],
            "locale": locale,
            "goal": context.get("user_goal", ""),
            "topic": context.get("active_topic", ""),
            "details": context.get("confirmed_details", {}),
            "unresolved": context.get("unresolved_questions", []),
            "last_summary": context.get("last_answer_summary", ""),
            "evidence_ids": sorted(evidence_ids),
        }
        return hashlib.sha256(canonical_json(payload)).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        now = time.monotonic()
        cached = self._entries.get(key)
        if not cached:
            return None
        if cached[0] <= now:
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return json.loads(json.dumps(cached[1], ensure_ascii=False))

    def put(self, key: str, value: dict[str, Any]) -> None:
        self._entries[key] = (time.monotonic() + self.ttl_seconds, json.loads(json.dumps(value, ensure_ascii=False)))
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def prune_expired(self) -> int:
        now = time.monotonic()
        expired = [key for key, (expires_at, _) in self._entries.items() if expires_at <= now]
        for key in expired:
            self._entries.pop(key, None)
        return len(expired)

    def status(self) -> dict[str, int]:
        return {"entries": len(self._entries), "max_entries": self.max_entries}


class GapStore:
    """Persists sanitized unanswered patterns so KB work can be prioritized without storing raw PII."""

    def __init__(self, root: Path):
        self.root = root / "gaps"
        self.store = AtomicStore(root)

    def record(self, *, topic: str, missing_questions: list[str], missing_information: list[str]) -> dict[str, Any] | None:
        clean_questions = []
        for question in missing_questions:
            clean = redact_text(str(question)).text.strip()[:500]
            if clean and safe_candidate_phrase(clean):
                clean_questions.append(clean)
        clean_information = [redact_text(str(item)).text.strip()[:200] for item in missing_information if str(item).strip()]
        if not clean_questions:
            return None
        signature = hashlib.sha256(canonical_json({"topic": topic, "questions": clean_questions})).hexdigest()
        path = self.root / f"{signature}.json"
        previous = self.store.get_json(path) if path.exists() else {}
        row = {
            "signature": signature,
            "topic": topic[:160],
            "questions": clean_questions[:8],
            "missing_information": clean_information[:8],
            "count": int(previous.get("count", 0)) + 1,
            "first_seen": previous.get("first_seen") or datetime.now(UTC).isoformat(),
            "last_seen": datetime.now(UTC).isoformat(),
        }
        self.store.put_json(path, row)
        return row

    def summary(self, *, limit: int = 20) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for path in self.root.glob("*.json"):
            try:
                rows.append(self.store.get_json(path))
            except Exception:
                continue
        rows.sort(key=lambda row: (-int(row.get("count", 0)), str(row.get("last_seen", ""))))
        return {"count": len(rows), "top": rows[:limit]}


class SupportPlanner:
    """Fixed support stages: question coverage, retrieval, evidence mapping, and grounded fallback."""

    def __init__(self, kb: Any):
        self.kb = kb

    def normalize_analysis(self, analysis: dict[str, Any], message: str, context: SessionContext) -> dict[str, Any]:
        sub_questions = analysis.get("sub_questions") or []
        normalized_questions: list[dict[str, str]] = []
        for index, item in enumerate(sub_questions[:8]):
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip()
                question_id = str(item.get("id") or f"q{index + 1}")
                kind = str(item.get("kind") or "question")
            else:
                text = str(item).strip()
                question_id = f"q{index + 1}"
                kind = "question"
            if text:
                normalized_questions.append({"id": question_id[:40], "text": text[:1000], "kind": kind[:80]})
        if not normalized_questions:
            normalized_questions = [{"id": "q1", "text": message[:1000], "kind": "question"}]

        retrieval_queries = [str(item).strip()[:1200] for item in analysis.get("retrieval_queries", []) if str(item).strip()]
        if not retrieval_queries:
            retrieval_queries = [str(analysis.get("retrieval_query") or message).strip()[:1200]]
        goal = str(analysis.get("user_goal") or context.user_goal or message).strip()
        topic = str(analysis.get("active_topic") or context.active_topic or "general").strip()
        if goal and goal not in retrieval_queries:
            retrieval_queries.append(f"{goal} {topic}"[:1200])
        for unresolved in context.unresolved_questions[-3:]:
            retrieval_queries.append(f"{unresolved} {topic}"[:1200])
        analysis = dict(analysis)
        analysis.update(
            {
                "sub_questions": normalized_questions,
                "retrieval_queries": list(dict.fromkeys(retrieval_queries))[:6],
                "dynamic_requirements": [str(item)[:80] for item in analysis.get("dynamic_requirements", [])][:6],
                "required_information": [str(item)[:160] for item in analysis.get("required_information", [])][:8],
                "user_goal": goal,
                "active_topic": topic,
                "response_mode": str(analysis.get("response_mode") or context.response_mode or "direct")[:80],
            }
        )
        return analysis

    def retrieve_kb(self, *, analysis: dict[str, Any], context: SessionContext, limit: int = 8) -> list[EvidenceItem]:
        hits: list[KBHit] = []
        previous_ids = [item.removeprefix("kb:") for item in context.last_evidence_ids if str(item).startswith("kb:")]
        previous_ids.extend(context.last_kb_ids)
        if previous_ids:
            hits.extend(self.kb.get_by_ids(list(dict.fromkeys(previous_ids))[:8]))
        for query in analysis.get("retrieval_queries", [])[:6]:
            hits.extend(self.kb.search(str(query), limit=4))
        deduped: dict[str, KBHit] = {}
        for hit in hits:
            current = deduped.get(hit.kb_id)
            if current is None or hit.score > current.score:
                deduped[hit.kb_id] = hit
        ranked = sorted(deduped.values(), key=lambda hit: (-hit.score, hit.kb_id))[:limit]
        return [
            EvidenceItem(
                evidence_id=f"kb:{hit.kb_id}",
                kind="kb",
                title=hit.question[:600],
                summary=hit.short_answer[:1200],
                body=hit.body[:2400],
                boundary=hit.answer_boundary[:800],
                verified=True,
                metadata={"kb_id": hit.kb_id, "target": hit.target[:160], "score": hit.score},
            )
            for hit in ranked
        ]

    def build_blueprint(
        self,
        *,
        analysis: dict[str, Any],
        context: SessionContext,
        evidence: list[EvidenceItem],
        resolver_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        all_evidence = [item.as_dict() for item in evidence]
        all_evidence.extend(result for result in resolver_results if result.get("status") == "resolved" and result.get("verified"))
        available_ids = [str(item.get("evidence_id")) for item in all_evidence if item.get("evidence_id")]
        coverage: list[dict[str, Any]] = []
        missing_questions: list[str] = []
        for question in analysis["sub_questions"]:
            scored = []
            for item in all_evidence:
                score = _text_similarity(question["text"], " ".join((str(item.get("title", "")), str(item.get("summary", "")), str(item.get("body", "")))))
                if score > 0:
                    scored.append((score, str(item.get("evidence_id"))))
            scored.sort(key=lambda pair: (-pair[0], pair[1]))
            evidence_ids = [evidence_id for _, evidence_id in scored[:3]]
            if not evidence_ids and available_ids and analysis.get("active_topic") != "general":
                evidence_ids = available_ids[:2]
            status = "grounded" if evidence_ids else "needs_information"
            if status != "grounded":
                missing_questions.append(question["text"])
            coverage.append({**question, "status": status, "evidence_ids": evidence_ids})

        missing_information = [item for item in analysis.get("required_information", []) if item not in context.confirmed_details]
        return {
            "user_goal": analysis["user_goal"],
            "active_topic": analysis["active_topic"],
            "response_mode": analysis["response_mode"],
            "human_context": analysis.get("human_context", {}),
            "questions": coverage,
            "available_evidence_ids": available_ids,
            "required_points": list(dict.fromkeys(item.summary for item in evidence if item.summary))[:8],
            "already_answered": context.answered_questions[-12:],
            "missing_questions": missing_questions,
            "missing_information": missing_information[:8],
            "answer_order": analysis.get("answer_order") or ["direct_answer", "reason", "steps", "exceptions", "next_action"],
            "rules": {
                "answer_every_question_or_name_exact_missing_information": True,
                "do_not_repeat_answered_questions": True,
                "do_not_invent_facts": True,
                "do_not_claim_unexecuted_actions": True,
                "use_only_available_evidence_ids": True,
                "keep_original_user_goal": True,
            },
        }

    def render_grounded_fallback(
        self,
        *,
        locale: str,
        blueprint: dict[str, Any],
        evidence: list[EvidenceItem],
        resolver_results: list[dict[str, Any]],
        context: SessionContext,
    ) -> dict[str, Any]:
        evidence_map = {item.evidence_id: item for item in evidence}
        resolver_map = {str(item.get("evidence_id")): item for item in resolver_results if item.get("status") == "resolved"}
        sections: list[str] = []
        answered_ids: list[str] = []
        used_ids: list[str] = []
        unresolved: list[str] = []

        for question in blueprint["questions"]:
            pieces: list[str] = []
            for evidence_id in question["evidence_ids"]:
                if evidence_id in evidence_map:
                    item = evidence_map[evidence_id]
                    text = item.summary.strip()
                    if item.body.strip():
                        text += "\n" + item.body.strip()
                    if text and text not in pieces:
                        pieces.append(text)
                    used_ids.append(evidence_id)
                elif evidence_id in resolver_map:
                    result = resolver_map[evidence_id]
                    display = str(result.get("display_text") or result.get("summary") or "").strip()
                    if display:
                        pieces.append(display)
                    used_ids.append(evidence_id)
            if pieces:
                if len(blueprint["questions"]) > 1:
                    sections.append(f"{question['text']}\n" + "\n\n".join(pieces[:2]))
                else:
                    sections.extend(pieces[:2])
                answered_ids.append(question["id"])
            else:
                unresolved.append(question["text"])

        missing_information = blueprint.get("missing_information", [])
        if unresolved:
            if locale == "ja-JP":
                known = "、".join(f"{key}={value}" for key, value in list(context.confirmed_details.items())[-4:])
                detail = f" 現在確認できている情報は「{known}」です。" if known else ""
                need = "、".join(missing_information) if missing_information else "対象画面、表示内容、直前に行った操作"
                sections.append(f"残っている確認事項は「{' / '.join(unresolved)}」です。{detail}確定に必要なのは、{need}です。")
            else:
                need = ", ".join(missing_information) if missing_information else "the current screen, displayed result, and last action"
                sections.append(f"The remaining point is: {' / '.join(unresolved)}. To resolve it, provide {need}.")

        answer = "\n\n".join(item for item in sections if item).strip()
        if not answer:
            answer = (
                "質問を解決するために必要な確認項目を特定できませんでした。対象機能名と、期待した結果・実際の結果を教えてください。"
                if locale == "ja-JP"
                else "I could not identify the required support facts. Provide the feature name, expected result, and actual result."
            )
        return {
            "answer": answer,
            "answered_question_ids": answered_ids,
            "used_evidence_ids": list(dict.fromkeys(used_ids)),
            "unresolved_questions": unresolved,
            "requested_information": missing_information,
            "needs_clarification": bool(unresolved),
            "answer_summary": answer[:800],
        }


def _text_similarity(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    return intersection / max(1, len(left_tokens))


def _tokens(text: str) -> set[str]:
    normalized = re.sub(r"\s+", "", str(text).lower())
    words = set(re.findall(r"[a-z0-9_]{2,}|[一-龠ぁ-んァ-ヶー]{2,}", normalized))
    chars = {normalized[index : index + 2] for index in range(max(0, len(normalized) - 1)) if normalized[index : index + 2].strip()}
    return words | chars
