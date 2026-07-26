from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .security import contains_internal_implementation, redact_text, sanitize_structure
from .skills import SkillRegistry, SkillResult
from .v8 import V8Unavailable


@dataclass(slots=True)
class ControlOutcome:
    status: str
    answer: str
    kb_ids: list[str]
    facts: list[str]
    engine_invoked: bool
    clarification: str | None
    state: dict[str, Any]
    insight: dict[str, Any]
    violations: list[str]
    execution: dict[str, Any]


class ControlledExecutionCore:
    """Owns routing, skills, evidence, engine permission, verification, and completion."""

    def __init__(self, *, v8: Any, engine: Any, skills: SkillRegistry):
        self.v8 = v8
        self.engine = engine
        self.skills = skills

    async def execute(self, *, job_id: str, request: Any, session: dict[str, Any], search: Callable[..., list[Any]]) -> ControlOutcome:
        analysis = await self._analyze(request, session)
        intake_context = {"job_id": job_id, "request": request, "analysis": analysis, "session": session}
        intake = await self.skills.execute(self.skills.select("intake", required_tags=("control", "state")), intake_context)
        intake_map = self._result_map(intake)
        contract = dict(intake_map["$customer-ai.execution-contract"].output["execution_contract"])
        state_capsule = dict(intake_map["$customer-ai.state-capsule"].output["state_capsule"])

        query = str(analysis.get("search_query") or request.message)
        hits = search(query, limit=5)
        evidence = [
            {
                "evidence_id": f"kb:{hit.kb_id}",
                "kind": "confirmed_kb",
                "verified": True,
                "question": hit.question,
                "short_answer": hit.short_answer,
                "body": hit.body,
                "answer_boundary": hit.answer_boundary,
                "target": hit.target,
            }
            for hit in hits
        ]
        evidence_context = {**intake_context, "state_capsule": state_capsule, "execution_contract": contract, "hits": hits, "evidence": evidence}
        evidence_results = await self.skills.execute(self.skills.select("evidence", required_tags=("evidence", "boundary")), evidence_context)
        compose_results = await self.skills.execute(self.skills.select("compose", required_tags=("compose", "script")), evidence_context)
        renderer = self._result_map(compose_results)["$customer-ai.deterministic-renderer"].output
        draft = str(renderer.get("draft") or "")
        skill_packet = [item.as_dict() for item in [*intake, *evidence_results, *compose_results]]

        plan = await self._plan(
            request=request.model_dump(mode="json"), analysis=analysis, state_capsule=state_capsule,
            contract=contract, evidence=evidence, skill_results=skill_packet, draft=draft, renderer=renderer,
        )
        allow_engine = bool(renderer.get("requires_language_engine") and plan.get("engine_required") and evidence and self.engine.available())
        contract["engine_policy"] = {
            **dict(contract.get("engine_policy") or {}),
            "allow": allow_engine,
            "reason": plan.get("engine_reason") or ("not_required" if not allow_engine else "controlled_composition"),
        }

        answer = draft
        engine_invoked = False
        engine_output: dict[str, Any] = {}
        if allow_engine:
            packet = {
                "execution_contract": contract,
                "state_capsule": state_capsule,
                "analysis": analysis,
                "skill_results": skill_packet,
                "evidence": evidence,
                "plan": plan,
                "draft": draft,
            }
            try:
                engine_output = self.engine.execute(packet)
                used = set(engine_output.get("used_evidence_ids") or [])
                available = {item["evidence_id"] for item in evidence}
                if not used.issubset(available):
                    raise ValueError("engine_used_unknown_evidence")
                answer = str(engine_output.get("answer") or draft)
                engine_invoked = True
            except Exception:
                answer = draft
                engine_output = {}

        verification = await self._verify(
            request=request.model_dump(mode="json"), answer=answer, analysis=analysis, plan=plan,
            contract=contract, evidence=evidence, engine_output=engine_output, renderer=renderer,
        )
        answer = str(verification.get("answer") or answer)
        guard_context = {**evidence_context, "answer": answer, "verification": verification}
        guard_results = await self.skills.execute(self.skills.select("guard", required_tags=("security", "output")), guard_context)
        guard = self._result_map(guard_results)["$customer-ai.output-guard"].output
        violations = list(dict.fromkeys([*(verification.get("violations") or []), *(guard.get("violations") or [])]))

        safe_answer = redact_text(answer).text.strip()
        if contains_internal_implementation(safe_answer):
            violations.append("internal_implementation")
            safe_answer = (
                "内部構成の詳細は公開していません。利用方法と問題解決に必要な範囲で案内します。"
                if request.locale == "ja-JP"
                else "Private implementation details are not disclosed. I can explain supported use and resolution steps."
            )
        clarification = renderer.get("clarification") or engine_output.get("clarification") or plan.get("clarification")
        completion = verification.get("completion") or {}
        if not evidence and not clarification:
            clarification = (
                "確認できる情報が不足しています。対象の機能、現在の画面、表示されているエラーを教えてください。"
                if request.locale == "ja-JP"
                else "I need a little more confirmed context. Tell me the feature, current screen, and shown error."
            )
        if violations or not guard.get("guard_passed", False) or not completion.get("passed", False):
            if not evidence:
                clarification = clarification or safe_answer
            elif violations:
                safe_answer = draft

        status = "awaiting_clarification" if clarification and not evidence else "completed"
        if status == "awaiting_clarification":
            safe_answer = str(clarification)
        state = {
            **state_capsule,
            "active_topic": analysis.get("intent", state_capsule.get("active_topic", "general")),
            "intent": analysis.get("intent", "general"),
            "confirmed_values": {**dict(state_capsule.get("confirmed_values") or {}), **dict(analysis.get("entities") or {})},
            "missing_values": list(plan.get("missing_values") or []),
            "pending_action": plan.get("action"),
            "last_kb_ids": [hit.kb_id for hit in hits],
            "emotion": analysis.get("human_state", {}).get("mode", "stable"),
            "resolution": "pending_feedback" if status == "completed" else "unresolved",
        }
        insight_context = {**evidence_context, "status": status, "answer": safe_answer, "state": state}
        insight_results = await self.skills.execute(self.skills.select("insight", required_tags=("kb", "bot")), insight_context)
        insight = self._result_map(insight_results)["$customer-ai.question-insight"].output["insight"]
        all_skills = [*skill_packet, *[item.as_dict() for item in guard_results], *[item.as_dict() for item in insight_results]]
        execution = {
            "control_core": "$controlled-execution-core-derived",
            "selected_skill_ids": [item["skill_id"] for item in all_skills],
            "evidence_ids": [item["evidence_id"] for item in evidence],
            "engine_allowed": allow_engine,
            "engine_invoked": engine_invoked,
            "engine_reason": contract["engine_policy"]["reason"],
            "v8_parallel_workers": analysis.get("worker_results", []),
            "completion": completion,
        }
        return ControlOutcome(
            status=status, answer=safe_answer, kb_ids=[hit.kb_id for hit in hits], facts=[hit.short_answer for hit in hits],
            engine_invoked=engine_invoked, clarification=str(clarification) if clarification else None, state=state,
            insight=insight, violations=list(dict.fromkeys(violations)), execution=sanitize_structure(execution),
        )

    async def _analyze(self, request: Any, session: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self.v8.request("analyze", {"message": request.message, "locale": request.locale, "session": session})
        except V8Unavailable:
            return fallback_analysis(request.message, request.locale)

    async def _plan(self, **payload: Any) -> dict[str, Any]:
        try:
            return await self.v8.request("plan", payload)
        except V8Unavailable:
            return fallback_plan(payload)

    async def _verify(self, **payload: Any) -> dict[str, Any]:
        try:
            return await self.v8.request("verify", payload)
        except V8Unavailable:
            answer = str(payload.get("answer") or "").strip()
            return {"answer": answer, "violations": [], "completion": {"passed": bool(answer), "missing": []}}

    @staticmethod
    def _result_map(results: list[SkillResult]) -> dict[str, SkillResult]:
        mapped = {item.skill_id: item for item in results}
        blocked = [item for item in results if item.status in {"blocked", "failed"}]
        if blocked:
            raise RuntimeError("structured_skill_failure:" + ",".join(item.skill_id for item in blocked))
        return mapped


def fallback_analysis(message: str, locale: str) -> dict[str, Any]:
    lowered = message.lower()
    intent = "credit" if any(word in lowered for word in ("credit", "クレジット", "残高")) else "general"
    mode = "high_pressure" if any(word in lowered for word in ("怒", "ふざけ", "困", "not working")) else "stable"
    sub_questions = [item.strip() for item in message.replace("？", "?").split("?") if item.strip()] or [message]
    return {"message": message.strip(), "intent": intent, "entities": {}, "sub_questions": sub_questions, "search_query": message, "ambiguity": 0, "human_state": {"mode": mode, "response_policy": ["deterministic_first"]}, "worker_results": ["python_fallback"], "locale": locale}


def fallback_plan(payload: dict[str, Any]) -> dict[str, Any]:
    analysis = payload.get("analysis") or {}
    renderer = payload.get("renderer") or {}
    evidence = payload.get("evidence") or []
    engine_required = bool(renderer.get("requires_language_engine") and evidence and (len(analysis.get("sub_questions") or []) > 1 or analysis.get("human_state", {}).get("mode") == "high_pressure"))
    return {"engine_required": engine_required, "engine_reason": "multi_evidence_composition" if engine_required else "deterministic_sufficient", "missing_values": [], "clarification": renderer.get("clarification"), "action": None}
