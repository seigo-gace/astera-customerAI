from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

from .conversation import ConversationCache
from .schemas import SessionContext
from .security import contains_internal_implementation, redact_text
from .v8 import V8Unavailable


@dataclass(slots=True)
class ConversationOutcome:
    status: str
    answer: str
    kb_ids: list[str]
    facts: list[str]
    engine_invoked: bool
    clarification: str | None
    context_used: bool
    analysis: dict[str, Any]
    violations: list[str]


class ConversationCore:
    """Keeps one user's goal and conversation coherent across follow-up turns."""

    def __init__(
        self,
        *,
        v8: Any,
        engine: Any,
        cache: ConversationCache,
        search: Callable[..., list[Any]],
    ) -> None:
        self.v8 = v8
        self.engine = engine
        self.cache = cache
        self.search = search

    async def execute(self, *, request: Any) -> ConversationOutcome:
        context = self.cache.get(request.session_id)
        compact_context = self.cache.compact(context)
        analysis = await self._analyze(request.message, compact_context)
        hits = self.search(str(analysis.get("retrieval_query") or request.message), limit=5)
        kb_evidence = [
            {
                "kb_id": hit.kb_id,
                "question": hit.question,
                "short_answer": hit.short_answer,
                "body": hit.body[:3000],
                "answer_boundary": hit.answer_boundary,
                "target": hit.target,
            }
            for hit in hits[:5]
        ]
        fallback = render_fallback(request.locale, hits, analysis)
        answer = fallback["answer"]
        clarification = fallback["clarification"]
        unresolved = list(context.unresolved_questions)
        used_kb_ids = [hit.kb_id for hit in hits]
        returned_goal = str(analysis.get("user_goal") or context.user_goal or request.message)
        returned_topic = str(analysis.get("active_topic") or context.active_topic or "general")
        engine_invoked = False

        packet = {
            "message": request.message,
            "conversation": compact_context,
            "analysis": analysis,
            "kb_evidence": kb_evidence,
            "response_rules": {
                "continue_same_user_goal": True,
                "answer_current_follow_up": True,
                "do_not_repeat_answered_questions": True,
                "ask_only_for_missing_information": True,
                "do_not_invent_product_facts": True,
                "do_not_claim_unexecuted_actions": True,
                "locale": request.locale,
            },
        }
        if self.engine.available():
            try:
                generated = await asyncio.to_thread(self.engine.execute, packet)
                available_ids = {item["kb_id"] for item in kb_evidence}
                claimed_ids = {str(item) for item in generated.get("used_kb_ids", [])}
                if not claimed_ids.issubset(available_ids):
                    raise ValueError("unknown_kb_reference")
                answer = str(generated["answer"]).strip()
                returned_goal = str(generated.get("user_goal") or returned_goal).strip()
                returned_topic = str(generated.get("active_topic") or returned_topic).strip()
                unresolved = [str(item).strip() for item in generated.get("unresolved_questions", []) if str(item).strip()]
                used_kb_ids = list(claimed_ids) or used_kb_ids
                clarification = answer if generated.get("needs_clarification") else None
                engine_invoked = True
            except Exception:
                answer = fallback["answer"]
                clarification = fallback["clarification"]

        verification = await self._verify(
            answer=answer,
            analysis=analysis,
            returned_topic=returned_topic,
            used_kb_ids=used_kb_ids,
            available_kb_ids=[item["kb_id"] for item in kb_evidence],
        )
        violations = list(verification.get("violations") or [])
        if not verification.get("passed", False):
            answer = fallback["answer"]
            clarification = fallback["clarification"]
            returned_topic = str(analysis.get("active_topic") or returned_topic)
            used_kb_ids = [hit.kb_id for hit in hits]
            engine_invoked = False

        answer = redact_text(answer).text.strip()
        if contains_internal_implementation(answer):
            violations.append("internal_implementation")
            answer = (
                "内部構成の詳細ではなく、利用方法と問題解決に必要な範囲で案内します。"
                if request.locale == "ja-JP"
                else "I can explain supported use and resolution steps without private implementation details."
            )
            clarification = answer

        status = "awaiting_clarification" if clarification else "completed"
        updated = SessionContext(
            session_id=request.session_id,
            user_goal=returned_goal[:1000],
            active_topic=returned_topic[:160],
            confirmed_details=dict(analysis.get("confirmed_details") or context.confirmed_details),
            unresolved_questions=unresolved,
            last_kb_ids=used_kb_ids,
            turns=context.turns,
        )
        updated = self.cache.append_turns(
            updated,
            user_text=request.message,
            assistant_text=answer,
            message_id=request.message_id,
            kb_ids=used_kb_ids,
        )
        self.cache.save(updated)
        return ConversationOutcome(
            status=status,
            answer=answer,
            kb_ids=used_kb_ids,
            facts=[hit.short_answer for hit in hits],
            engine_invoked=engine_invoked,
            clarification=clarification,
            context_used=bool(analysis.get("context_used")),
            analysis=analysis,
            violations=list(dict.fromkeys(violations)),
        )

    async def _analyze(self, message: str, context: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self.v8.request("analyze_turn", {"message": message, "context": context})
        except V8Unavailable:
            active_topic = str(context.get("active_topic") or "general")
            goal = str(context.get("user_goal") or message)
            query = " ".join(item for item in (message, goal, active_topic) if item and item != "general")
            return {
                "message": message,
                "follow_up": bool(context.get("user_goal")),
                "context_used": bool(context.get("user_goal") or context.get("turns")),
                "active_topic": active_topic,
                "user_goal": goal,
                "confirmed_details": dict(context.get("confirmed_details") or {}),
                "retrieval_query": query,
            }

    async def _verify(self, **payload: Any) -> dict[str, Any]:
        try:
            return await self.v8.request("verify_turn", payload)
        except V8Unavailable:
            answer = str(payload.get("answer") or "").strip()
            return {"answer": answer, "passed": bool(answer), "violations": [] if answer else ["empty_answer"]}


def render_fallback(locale: str, hits: list[Any], analysis: dict[str, Any]) -> dict[str, str | None]:
    if hits:
        sections: list[str] = []
        for hit in hits[:3]:
            text = hit.short_answer.strip()
            if hit.body.strip():
                text += "\n\n" + hit.body.strip()
            if text not in sections:
                sections.append(text)
        return {"answer": "\n\n".join(sections), "clarification": None}
    topic = str(analysis.get("active_topic") or "").strip()
    if locale == "ja-JP":
        prefix = f"{topic}について、" if topic and topic != "general" else ""
        text = prefix + "正確に確認するため、現在の画面・表示されている内容・直前に行った操作を教えてください。"
    else:
        prefix = f"For {topic}, " if topic and topic != "general" else ""
        text = prefix + "tell me the current screen, what is displayed, and the last action you took."
    return {"answer": text, "clarification": text}


ControlledExecutionCore = ConversationCore
ControlOutcome = ConversationOutcome
