from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

from .conversation import ConversationCache
from .schemas import SessionContext
from .security import contains_internal_implementation, redact_text
from .support import FeedbackStore, PreparedSupport, SupportRuntime, validate_response
from .v8 import V8Unavailable


MISSING_KB_ANSWER = "現在、該当する正確な案内情報が登録されていません"


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
    processing_grade: str
    question_tasks: list[dict[str, Any]]
    blueprint: dict[str, Any]
    repair_attempted: bool
    feedback_candidate_id: str | None
    execution: dict[str, Any]


class ConversationCore:
    """Astera-derived support pipeline specialized for Customer AI response handling."""

    def __init__(
        self,
        *,
        v8: Any,
        engine: Any,
        cache: ConversationCache,
        search: Callable[..., list[Any]],
        feedback_store: FeedbackStore | None = None,
    ) -> None:
        self.v8 = v8
        self.engine = engine
        self.cache = cache
        self.search = search
        self.support = SupportRuntime(search=search)
        self.feedback_store = feedback_store

    async def execute(self, *, request: Any) -> ConversationOutcome:
        context = self.cache.get(request.session_id)
        compact_context = self.cache.compact(context)
        analysis = await self._analyze(request.message, compact_context, request.source)
        prepared = await self.support.prepare(
            message=request.message,
            locale=request.locale,
            source=request.source,
            context=compact_context,
            analysis=analysis,
        )

        answer, answered_task_ids, unresolved_task_ids, used_evidence_ids = self._deterministic_state(prepared)
        returned_goal = str(analysis.get("user_goal") or context.user_goal or request.message).strip()
        returned_topic = str(analysis.get("active_topic") or context.active_topic or "general").strip()
        engine_invoked = False
        repair_attempted = False
        first_attempt_violations: list[str] = []

        packet = self._engine_packet(request=request, prepared=prepared)
        if prepared.model_required and prepared.evidence and self.engine.available():
            generated = await self._execute_engine(packet)
            if generated is not None:
                answer, returned_goal, returned_topic, answered_task_ids, unresolved_task_ids, used_evidence_ids = self._apply_engine_output(
                    generated=generated,
                    fallback_answer=answer,
                    returned_goal=returned_goal,
                    returned_topic=returned_topic,
                    default_answered=answered_task_ids,
                    default_unresolved=unresolved_task_ids,
                    default_evidence=used_evidence_ids,
                )
                engine_invoked = True

        if unresolved_task_ids:
            answer, answered_task_ids, unresolved_task_ids, used_evidence_ids = self._deterministic_state(prepared)

        verification = await self._verify_response(
            answer=answer,
            analysis=analysis,
            prepared=prepared,
            returned_topic=returned_topic,
            answered_task_ids=answered_task_ids,
            unresolved_task_ids=unresolved_task_ids,
            used_evidence_ids=used_evidence_ids,
        )
        validation = validate_response(
            answer=answer,
            prepared=prepared,
            answered_task_ids=answered_task_ids,
            unresolved_task_ids=unresolved_task_ids,
            used_evidence_ids=used_evidence_ids,
            external_violations=verification.get("violations") or [],
        )

        if not validation.passed and engine_invoked:
            first_attempt_violations = list(validation.violations)
            repair_attempted = True
            repair_packet = {
                **packet,
                "repair": {
                    "attempt": 1,
                    "previous_answer": answer[:12000],
                    "violations": validation.violations,
                    "required_answered_task_ids": [
                        item.task_id
                        for item in prepared.tasks
                        if item.task_id not in prepared.blueprint.get("unresolved_task_ids", [])
                    ],
                    "required_unresolved_task_ids": list(prepared.blueprint.get("unresolved_task_ids", [])),
                    "allowed_evidence_ids": [item.evidence_id for item in prepared.evidence],
                },
            }
            repaired = await self._execute_engine(repair_packet)
            if repaired is not None:
                answer, returned_goal, returned_topic, answered_task_ids, unresolved_task_ids, used_evidence_ids = self._apply_engine_output(
                    generated=repaired,
                    fallback_answer=prepared.blueprint["deterministic_answer"],
                    returned_goal=returned_goal,
                    returned_topic=returned_topic,
                    default_answered=[
                        section["task_id"]
                        for section in prepared.blueprint["sections"]
                        if section["resolved"]
                    ],
                    default_unresolved=list(prepared.blueprint.get("unresolved_task_ids", [])),
                    default_evidence=list(prepared.blueprint.get("evidence_ids", [])),
                )
            if unresolved_task_ids:
                answer, answered_task_ids, unresolved_task_ids, used_evidence_ids = self._deterministic_state(prepared)
            verification = await self._verify_response(
                answer=answer,
                analysis=analysis,
                prepared=prepared,
                returned_topic=returned_topic,
                answered_task_ids=answered_task_ids,
                unresolved_task_ids=unresolved_task_ids,
                used_evidence_ids=used_evidence_ids,
            )
            validation = validate_response(
                answer=answer,
                prepared=prepared,
                answered_task_ids=answered_task_ids,
                unresolved_task_ids=unresolved_task_ids,
                used_evidence_ids=used_evidence_ids,
                external_violations=verification.get("violations") or [],
            )

        if not validation.passed:
            answer, answered_task_ids, unresolved_task_ids, used_evidence_ids = self._deterministic_state(prepared)
            verification = await self._verify_response(
                answer=answer,
                analysis=analysis,
                prepared=prepared,
                returned_topic=str(analysis.get("active_topic") or returned_topic),
                answered_task_ids=answered_task_ids,
                unresolved_task_ids=unresolved_task_ids,
                used_evidence_ids=used_evidence_ids,
            )
            validation = validate_response(
                answer=answer,
                prepared=prepared,
                answered_task_ids=answered_task_ids,
                unresolved_task_ids=unresolved_task_ids,
                used_evidence_ids=used_evidence_ids,
                external_violations=verification.get("violations") or [],
            )
            returned_topic = str(analysis.get("active_topic") or returned_topic)
            engine_invoked = False

        answer = redact_text(answer).text.strip()
        if contains_internal_implementation(answer):
            answer = MISSING_KB_ANSWER
            validation.violations.append("internal_implementation")
            unresolved_task_ids = [item.task_id for item in prepared.tasks]
            answered_task_ids = []
            used_evidence_ids = []

        clarification = self._clarification(unresolved_task_ids)
        status = "awaiting_clarification" if unresolved_task_ids else "completed"
        question_ledger = self._updated_ledger(
            context,
            request.message_id,
            prepared,
            answered_task_ids,
            unresolved_task_ids,
        )
        evidence_cache = self._updated_evidence_cache(context, prepared)
        answered_ledger_ids = [f"{request.message_id}:{task_id}" for task_id in answered_task_ids]
        updated = SessionContext(
            session_id=request.session_id,
            user_goal=returned_goal[:1000],
            active_topic=returned_topic[:160],
            confirmed_details=dict(analysis.get("confirmed_details") or context.confirmed_details),
            unresolved_questions=[
                item.text for item in prepared.tasks if item.task_id in unresolved_task_ids
            ],
            last_kb_ids=[item.kb_id for item in prepared.evidence],
            turns=context.turns,
            answered_question_ids=list(
                dict.fromkeys([*context.answered_question_ids, *answered_ledger_ids])
            ),
            question_ledger=question_ledger,
            evidence_cache=evidence_cache,
            last_blueprint=prepared.blueprint,
        )
        updated = self.cache.append_turns(
            updated,
            user_text=request.message,
            assistant_text=answer,
            message_id=request.message_id,
            kb_ids=[item.kb_id for item in prepared.evidence],
        )
        self.cache.save(updated)

        candidate_id = None
        if self.feedback_store is not None:
            candidate_id = self.feedback_store.record(
                session_id=request.session_id,
                message=request.message,
                prepared=prepared,
                validation=validation,
                status=status,
            )

        final_violations = list(dict.fromkeys(validation.violations))
        execution = {
            "pipeline": "astera-customerai-master-v2-kb-only",
            "processing_grade": prepared.processing_grade,
            "question_count": len(prepared.tasks),
            "search_task_count": len(prepared.search_tasks),
            "evidence_count": len(prepared.evidence),
            "engine_required": prepared.model_required,
            "engine_invoked": engine_invoked,
            "repair_attempted": repair_attempted,
            "repair_limit": 1,
            "first_attempt_violations": first_attempt_violations,
            "answered_task_ids": answered_task_ids,
            "unresolved_task_ids": unresolved_task_ids,
            "used_evidence_ids": used_evidence_ids,
            "feedback_candidate_id": candidate_id,
        }
        return ConversationOutcome(
            status=status,
            answer=answer,
            kb_ids=[item.kb_id for item in prepared.evidence],
            facts=[item.short_answer for item in prepared.evidence],
            engine_invoked=engine_invoked,
            clarification=clarification,
            context_used=bool(analysis.get("context_used")),
            analysis=analysis | {"analysis_dictionary": prepared.analysis_dictionary},
            violations=final_violations,
            processing_grade=prepared.processing_grade,
            question_tasks=[item.as_dict() for item in prepared.tasks],
            blueprint=prepared.blueprint,
            repair_attempted=repair_attempted,
            feedback_candidate_id=candidate_id,
            execution=execution,
        )

    async def _analyze(self, message: str, context: dict[str, Any], source: str) -> dict[str, Any]:
        try:
            return await self.v8.request(
                "analyze_turn",
                {"message": message, "context": context, "source": source},
            )
        except V8Unavailable:
            active_topic = str(context.get("active_topic") or "general")
            goal = str(context.get("user_goal") or message)
            query = " ".join(
                item for item in (message, goal, active_topic) if item and item != "general"
            )
            return {
                "message": message,
                "follow_up": bool(context.get("user_goal")),
                "context_used": bool(context.get("user_goal") or context.get("turns")),
                "active_topic": active_topic,
                "user_goal": goal,
                "confirmed_details": dict(context.get("confirmed_details") or {}),
                "retrieval_query": query,
            }

    async def _execute_engine(self, packet: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return await asyncio.to_thread(self.engine.execute, packet)
        except Exception:
            return None

    async def _verify_response(
        self,
        *,
        answer: str,
        analysis: dict[str, Any],
        prepared: PreparedSupport,
        returned_topic: str,
        answered_task_ids: list[str],
        unresolved_task_ids: list[str],
        used_evidence_ids: list[str],
    ) -> dict[str, Any]:
        payload = {
            "answer": answer,
            "analysis": analysis,
            "returned_topic": returned_topic,
            "question_task_ids": [item.task_id for item in prepared.tasks],
            "answered_task_ids": answered_task_ids,
            "unresolved_task_ids": unresolved_task_ids,
            "used_evidence_ids": used_evidence_ids,
            "available_evidence_ids": [item.evidence_id for item in prepared.evidence],
        }
        try:
            return await self.v8.request("verify_turn", payload)
        except V8Unavailable:
            return {
                "answer": answer,
                "passed": bool(answer.strip()),
                "violations": [] if answer.strip() else ["empty_answer"],
            }

    @staticmethod
    def _engine_packet(*, request: Any, prepared: PreparedSupport) -> dict[str, Any]:
        return {
            "message": prepared.normalized_message[:8000],
            "support_packet": prepared.as_packet(),
            "response_rules": {
                "answer_only_from_customerai_master_v2": True,
                "answer_only_from_supplied_evidence": True,
                "do_not_use_conversation_history_as_fact": True,
                "do_not_use_memory_or_general_knowledge": True,
                "do_not_speculate_or_decorate": True,
                "answer_each_question_task": True,
                "do_not_escalate_to_staff": True,
                "missing_kb_answer": MISSING_KB_ANSWER,
                "do_not_claim_unexecuted_actions": True,
                "do_not_expose_private_implementation": True,
                "locale": request.locale,
            },
        }

    @staticmethod
    def _apply_engine_output(
        *,
        generated: dict[str, Any],
        fallback_answer: str,
        returned_goal: str,
        returned_topic: str,
        default_answered: list[str],
        default_unresolved: list[str],
        default_evidence: list[str],
    ) -> tuple[str, str, str, list[str], list[str], list[str]]:
        answer = str(generated.get("answer") or fallback_answer).strip()
        goal = str(generated.get("user_goal") or returned_goal).strip()
        topic = str(generated.get("active_topic") or returned_topic).strip()
        answered = [
            str(item)
            for item in generated.get("answered_task_ids", default_answered)
            if str(item)
        ]
        unresolved = [
            str(item)
            for item in generated.get("unresolved_task_ids", default_unresolved)
            if str(item)
        ]
        evidence = [
            str(item)
            for item in generated.get("used_evidence_ids", default_evidence)
            if str(item)
        ]
        return answer, goal, topic, answered, unresolved, evidence

    @classmethod
    def _deterministic_state(
        cls, prepared: PreparedSupport
    ) -> tuple[str, list[str], list[str], list[str]]:
        answered = [
            section["task_id"]
            for section in prepared.blueprint["sections"]
            if section["resolved"]
        ]
        unresolved = list(prepared.blueprint.get("unresolved_task_ids", []))
        sections: list[str] = []
        multiple = len(prepared.blueprint.get("sections", [])) > 1
        for index, section in enumerate(prepared.blueprint.get("sections", []), start=1):
            body = (
                str(section.get("body") or "").strip()
                if section.get("resolved")
                else MISSING_KB_ANSWER
            )
            if multiple:
                sections.append(f"### {index}. {section.get('heading', '')}\n\n{body}")
            else:
                sections.append(body)
        answer = "\n\n".join(part for part in sections if part).strip()
        if not answer:
            answer = MISSING_KB_ANSWER
        return (
            answer,
            answered,
            unresolved,
            list(prepared.blueprint.get("evidence_ids", [])),
        )

    @staticmethod
    def _clarification(unresolved_task_ids: list[str]) -> str | None:
        return MISSING_KB_ANSWER if unresolved_task_ids else None

    @staticmethod
    def _updated_ledger(
        context: SessionContext,
        message_id: str,
        prepared: PreparedSupport,
        answered_task_ids: list[str],
        unresolved_task_ids: list[str],
    ) -> list[dict[str, Any]]:
        new_rows = []
        for task in prepared.tasks:
            new_rows.append(
                {
                    "ledger_id": f"{message_id}:{task.task_id}",
                    "message_id": message_id,
                    "task": task.as_dict(),
                    "status": (
                        "answered"
                        if task.task_id in answered_task_ids
                        else "unresolved"
                        if task.task_id in unresolved_task_ids
                        else "pending"
                    ),
                    "evidence_ids": [
                        item.evidence_id
                        for item in prepared.evidence
                        if task.task_id in item.task_ids
                    ],
                }
            )
        return [*context.question_ledger, *new_rows][-24:]

    @staticmethod
    def _updated_evidence_cache(
        context: SessionContext, prepared: PreparedSupport
    ) -> dict[str, dict[str, Any]]:
        cache = dict(context.evidence_cache)
        for item in prepared.evidence:
            cache[item.evidence_id] = {
                "kb_id": item.kb_id,
                "question": item.question[:600],
                "short_answer": item.short_answer[:1200],
                "answer_boundary": item.answer_boundary[:600],
                "task_ids": item.task_ids,
            }
        return dict(list(cache.items())[-16:])


ControlledExecutionCore = ConversationCore
ControlOutcome = ConversationOutcome
