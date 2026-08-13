from __future__ import annotations

import uuid

from .answer_quality import FinalAnswerComposer, IntegratedAnswerPlan, RuntimeSatisfactionGate, RuntimeSatisfactionSignals
from .contracts import SearchMode
from .integration import DialogueIntegrator
from .knowledge import GroundingConflictError, GroundingPlanner
from .quality import CompletionGate
from .schemas import FinalResponse, FollowUpKind, ResolutionMode, RoleName, SharedRolePacket
from .search_planner import SearchPlanner
from .security import PublicBoundary
from .skill_runtime import SkillQuery, SkillRegistry
from .state import StateStore
from .task_decomposition import TaskDecomposer
from .writing_skills import WritingRefiner


class InternalAudit:
    def check(self, packet: SharedRolePacket, results) -> list[str]:
        violations = []
        fact_ids = {f.fact_id for f in packet.facts}
        for result in results:
            if any(eid not in fact_ids for eid in result.evidence_ids):
                violations.append("unsupported_claim")
            for resolution in result.task_resolutions:
                if any(eid not in fact_ids for eid in resolution.evidence_ids):
                    violations.append("unsupported_claim")
        return list(dict.fromkeys(violations))


class CustomerAIInternalCore:
    def __init__(
        self,
        *,
        decomposer: TaskDecomposer,
        search: SearchPlanner,
        grounding: GroundingPlanner,
        skills: SkillRegistry,
        roles,
        integrator: DialogueIntegrator,
        gate: CompletionGate,
        state: StateStore,
        japanese,
        max_targeted_retry: int = 1,
    ):
        self.decomposer = decomposer
        self.search = search
        self.grounding = grounding
        self.skills = skills
        self.roles = roles
        self.integrator = integrator
        self.gate = gate
        self.state = state
        self.japanese = japanese
        self.max_targeted_retry = max(0, max_targeted_retry)
        self.audit = InternalAudit()
        self.composer = FinalAnswerComposer()
        self.satisfaction = RuntimeSatisfactionGate()
        self.security = PublicBoundary()
        self.refiner = WritingRefiner()

    @staticmethod
    def _audience(text: str) -> str:
        folded = text.casefold()
        if any(k in folded for k in ("技術者", "開発者", "api", "sdk", "developer", "engineer")):
            return "technical"
        if any(k in folded for k in ("投資家", "出資", "ir", "investor", "法人", "enterprise")):
            return "business"
        return "general"

    async def run(self, session_id: str, message: str) -> FinalResponse:
        request_id = "req_" + uuid.uuid4().hex
        turn_id = "turn_" + uuid.uuid4().hex
        state = self.state.get(session_id)
        prepared = self.japanese.prepare(message, state.as_japanese_context())
        normalized_text = str(prepared["normalized_text"])
        follow_up_kind = self.state.begin_turn(session_id, normalized_text)
        context = {
            "active_topics": list(state.active_topics),
            "last_user_need": state.last_user_need,
            "user_conditions": dict(state.user_conditions),
            "japanese": prepared,
            "follow_up_kind": follow_up_kind.value,
            "prior_need_ids": list(state.last_need_ids),
        }
        contract = None
        try:
            contract = self.decomposer.decompose(normalized_text, context)
            if self.decomposer.requires_semantic_expansion(contract) and hasattr(self.roles, "semantic_decompose"):
                contract = await self.roles.semantic_decompose(normalized_text, contract)
            bound_tasks = self.state.bind_tasks(session_id, contract.need_tasks, follow_up_kind)
            contract = contract.model_copy(update={"need_tasks": bound_tasks})
            state = self.state.get(session_id)
            reusable = self.state.reusable_facts(session_id, contract.need_tasks, follow_up_kind)
            grounding_plan = self.search.plan(contract, SearchMode.RUNTIME_GROUNDING)
            search_tasks = [] if follow_up_kind == FollowUpKind.CLARIFICATION and reusable else contract.need_tasks
            facts = await self.grounding.build_shared_facts(search_tasks, grounding_plan, reusable_facts=reusable)
            self.state.record_evidence(session_id, contract.need_tasks, facts)
            self.state.update(
                session_id,
                active_topics=[t.intent for t in contract.need_tasks],
                last_user_need=contract.target,
                user_conditions=dict(state.user_conditions),
            )
        except GroundingConflictError:
            if contract is not None:
                self.state.complete_turn(
                    session_id,
                    contract.need_tasks,
                    resolved_task_ids=set(),
                    unresolved_task_ids={t.task_id for t in contract.need_tasks},
                    satisfaction_blockers={"grounding_conflict"},
                )
            return self._failure(request_id, session_id, turn_id, ResolutionMode.BLOCKED_CURRENT_FACT, "grounding_conflict", ["grounding_conflict"])
        except Exception:
            if contract is not None:
                self.state.complete_turn(
                    session_id,
                    contract.need_tasks,
                    resolved_task_ids=set(),
                    unresolved_task_ids={t.task_id for t in contract.need_tasks},
                    satisfaction_blockers={"preflight_runtime_failure"},
                )
            return self._failure(request_id, session_id, turn_id, ResolutionMode.RUNTIME_FAILURE, "runtime_failure", ["preflight_runtime_failure"])

        language = "ja" if any("\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff" for ch in normalized_text) else "en"
        audience = self._audience(normalized_text)
        skill_plan = self.search.plan(contract, SearchMode.SKILL_SEARCH)
        capsules = self.skills.select(
            SkillQuery(
                language=language,
                audience=audience,
                tasks=tuple(contract.need_tasks),
                has_evidence=bool(facts),
                text_length=len(normalized_text),
            )
        )
        packet = SharedRolePacket(
            request_id=request_id,
            session_id=session_id,
            turn_id=turn_id,
            user_message=message,
            normalized_need=contract.target,
            audience=audience,
            tasks=contract.need_tasks,
            user_conditions=dict(state.user_conditions),
            language_hints={
                "term_candidates": prepared["term_candidates"],
                "ellipsis": prepared["ellipsis"],
                "search_terms": grounding_plan.search_terms,
                "skill_terms": skill_plan.search_terms,
                "follow_up_kind": follow_up_kind.value,
            },
            facts=facts,
            completion_conditions=contract.completion_conditions,
        )
        try:
            results = await self.roles.run_all(packet, capsules)
            integrated = self.integrator.integrate(results)
        except Exception:
            self.state.complete_turn(
                session_id,
                contract.need_tasks,
                resolved_task_ids=set(),
                unresolved_task_ids={t.task_id for t in contract.need_tasks},
                satisfaction_blockers={"role_runtime_failure"},
            )
            return self._failure(request_id, session_id, turn_id, ResolutionMode.RUNTIME_FAILURE, "runtime_failure", ["role_runtime_failure"])

        external = self.audit.check(packet, results)
        quality = self.gate.evaluate(packet, integrated, external_violations=external)
        retries = 0
        while not quality.passed and retries < self.max_targeted_retry:
            targets = sorted(
                set(integrated.missing_task_ids)
                | set(integrated.contradiction_task_ids)
                | set(quality.missing_evidence_task_ids)
            )
            if not targets:
                break
            repair_packet = packet.model_copy(update={"repair_targets": targets})
            try:
                repaired = await self.roles.retry_role(RoleName.CONSTRUCTIVE, repair_packet, capsules)
                if not hasattr(self.roles, "validate_draft"):
                    raise RuntimeError("repair_validation_required")
                validators = await self.roles.validate_draft(repair_packet, capsules, repaired)
                results = [repaired, *validators]
                integrated = self.integrator.integrate(results)
                external = self.audit.check(repair_packet, results)
                quality = self.gate.evaluate(repair_packet, integrated, external_violations=external)
            except Exception:
                break
            retries += 1

        missing_inputs = tuple(item for task in contract.need_tasks for item in task.required_user_inputs if item)
        plan = IntegratedAnswerPlan(
            needs=tuple(contract.need_tasks),
            resolutions=integrated.resolutions,
            blocked_task_ids=tuple(integrated.contradiction_task_ids),
            missing_evidence_task_ids=quality.missing_evidence_task_ids,
            missing_user_inputs=missing_inputs if quality.resolution_score < 1.0 else (),
        )
        composed = self.composer.compose(plan)
        answer = self.refiner.refine(composed.answer or "") if composed.answer else ""
        terminology = self.japanese.terminology_violations(answer) if answer else []
        security = self.security.check_output(answer=answer, forbidden_literals=packet.forbidden_claims, unexecuted_completion_claim=False)
        major = [t for t in contract.need_tasks if t.priority == "primary"]
        resolved = set(composed.resolved_task_ids)
        all_major = all(t.task_id in resolved for t in major)
        actionable = all(
            (not t.actionability_required)
            or any(r.task_id == t.task_id and bool(r.action_steps) for r in integrated.resolutions)
            for t in contract.need_tasks
        )
        evidence_complete = not quality.missing_evidence_task_ids
        fp_ok = "false_premise_uncorrected" not in quality.violations
        sat_ok, sat_violations = self.satisfaction.evaluate(
            composed.mode,
            RuntimeSatisfactionSignals(
                all_major,
                evidence_complete,
                True,
                actionable,
                fp_ok,
                unsupported_claim_count=int("unsupported_claim" in quality.violations),
                terminology_violation_count=len(terminology),
            ),
        )
        violations = list(
            dict.fromkeys(
                [
                    *quality.violations,
                    *sat_violations,
                    *security.violations,
                    *("terminology_violation" for _ in terminology),
                ]
            )
        )
        passed = bool(quality.passed and sat_ok and security.passed and not terminology)
        unresolved_task_ids = set(composed.unresolved_task_ids)
        if not passed:
            unresolved_task_ids.update(t.task_id for t in contract.need_tasks if t.task_id not in resolved)
        self.state.complete_turn(
            session_id,
            contract.need_tasks,
            resolved_task_ids=resolved,
            unresolved_task_ids=unresolved_task_ids,
            evidence_gaps=set(quality.missing_evidence_task_ids),
            satisfaction_blockers=set(violations),
        )

        failure_class = None
        if not passed:
            if "grounding_conflict" in violations:
                failure_class = "grounding_conflict"
            elif {"major_need_missing", "evidence_incomplete", "conversation_not_resolved"}.intersection(violations):
                failure_class = "coverage_defect"
            elif not security.passed:
                failure_class = "safety_rejection"
            else:
                failure_class = "runtime_failure"
        return FinalResponse(
            request_id=request_id,
            session_id=session_id,
            turn_id=turn_id,
            answer=answer or composed.answer,
            answered_task_ids=list(composed.resolved_task_ids),
            unresolved_task_ids=list(composed.unresolved_task_ids),
            evidence_ids=list(integrated.evidence_ids),
            resolution_score=quality.resolution_score,
            passed=passed,
            resolution_mode=composed.mode,
            clarification_questions=list(composed.clarification_questions),
            failure_class=failure_class,
            violations=violations,
        )

    @staticmethod
    def _failure(request_id, session_id, turn_id, mode, failure_class, violations):
        return FinalResponse(
            request_id=request_id,
            session_id=session_id,
            turn_id=turn_id,
            answer=None,
            answered_task_ids=[],
            unresolved_task_ids=[],
            evidence_ids=[],
            resolution_score=0.0,
            passed=False,
            resolution_mode=mode,
            failure_class=failure_class,
            violations=violations,
        )
