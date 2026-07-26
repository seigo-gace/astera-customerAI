from __future__ import annotations

import asyncio
import inspect
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Literal

SkillHandler = Callable[[dict[str, Any]], dict[str, Any] | Awaitable[dict[str, Any]]]
SkillStatus = Literal["ACTIVE", "QUARANTINED", "DEPRECATED"]


@dataclass(frozen=True, slots=True)
class SkillContract:
    skill_id: str
    title: str
    stage: Literal["intake", "evidence", "compose", "guard", "insight"]
    purpose: str
    execution_type: Literal["script", "v8", "bot", "engine"] = "script"
    input_keys: tuple[str, ...] = ()
    output_keys: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    risk_level: int = 0
    side_effect: Literal["none", "read", "write", "network", "destructive"] = "none"
    tags: tuple[str, ...] = ()
    validation_status: SkillStatus = "ACTIVE"
    source_material: str = ""


@dataclass(slots=True)
class SkillResult:
    skill_id: str
    status: Literal["complete", "partial", "blocked", "failed"]
    output: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    uncertainty: list[str] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "status": self.status,
            "output": self.output,
            "evidence_refs": self.evidence_refs,
            "uncertainty": self.uncertainty,
            "error": self.error,
        }


class SkillRegistry:
    """Metadata-routed `$` skill registry. No model is used for routine routing."""

    def __init__(self) -> None:
        self._skills: dict[str, tuple[SkillContract, SkillHandler]] = {}

    def register(self, contract: SkillContract, handler: SkillHandler) -> None:
        if not contract.skill_id.startswith("$"):
            raise ValueError("structured skill id must start with '$'")
        if contract.skill_id in self._skills:
            raise ValueError(f"duplicate skill id: {contract.skill_id}")
        self._skills[contract.skill_id] = (contract, handler)

    def contracts(self) -> list[SkillContract]:
        return [item[0] for item in self._skills.values()]

    def active_ids(self) -> list[str]:
        return sorted(contract.skill_id for contract, _ in self._skills.values() if contract.validation_status == "ACTIVE")

    def select(self, stage: str, *, required_tags: Iterable[str] = ()) -> list[str]:
        wanted = set(required_tags)
        ranked: list[tuple[int, str]] = []
        for contract, _ in self._skills.values():
            if contract.validation_status != "ACTIVE" or contract.stage != stage:
                continue
            ranked.append((-len(wanted.intersection(contract.tags)), contract.skill_id))
        return [skill_id for _, skill_id in sorted(ranked)]

    async def execute(self, skill_ids: Iterable[str], context: dict[str, Any]) -> list[SkillResult]:
        async def run(skill_id: str) -> SkillResult:
            item = self._skills.get(skill_id)
            if item is None:
                return SkillResult(skill_id=skill_id, status="blocked", error="skill_not_registered")
            contract, handler = item
            if contract.validation_status != "ACTIVE":
                return SkillResult(skill_id=skill_id, status="blocked", error="skill_not_active")
            missing = [key for key in contract.input_keys if key not in context]
            if missing:
                return SkillResult(skill_id=skill_id, status="blocked", error=f"missing_inputs:{','.join(missing)}")
            try:
                value = handler(context)
                if inspect.isawaitable(value):
                    value = await value
                if not isinstance(value, dict):
                    raise TypeError("skill output must be a dict")
                absent = [key for key in contract.output_keys if key not in value]
                if absent:
                    return SkillResult(skill_id=skill_id, status="partial", output=value, uncertainty=[f"missing_output:{key}" for key in absent])
                return SkillResult(skill_id=skill_id, status="complete", output=value, evidence_refs=list(value.get("evidence_refs", [])))
            except Exception as exc:
                return SkillResult(skill_id=skill_id, status="failed", error=f"{type(exc).__name__}:{exc}")

        return await asyncio.gather(*(run(skill_id) for skill_id in skill_ids))


def _execution_contract_skill(context: dict[str, Any]) -> dict[str, Any]:
    request = context["request"]
    analysis = context.get("analysis", {})
    sub_questions = list(analysis.get("sub_questions") or [request.message])
    return {
        "execution_contract": {
            "task_id": context["job_id"],
            "goal": "Resolve every confirmed customer-support issue in the current turn without inventing facts.",
            "scope": sub_questions,
            "constraints": [
                "Use only confirmed KB or verified action results for factual claims.",
                "Do not expose private implementation details.",
                "Do not ask again for information already present in the state capsule.",
                "Do not mark unexecuted actions as completed.",
            ],
            "allowed_actions": ["read_kb", "request_clarification", "prepare_action_request", "compose_answer"],
            "forbidden_actions": ["invent_fact", "execute_business_action", "expose_secret", "expose_internal_design"],
            "required_output_schema": {
                "answer": "string",
                "used_evidence_ids": "string[]",
                "covered_question_indexes": "int[]",
                "clarification": "string|null",
                "unresolved": "string[]",
            },
            "evidence_requirements": ["Every factual sentence must map to a verified evidence id."],
            "stop_conditions": ["all_sub_questions_resolved", "clarification_required", "unsafe_or_unverified_boundary"],
            "uncertainty_rule": "Return a clarification or explicitly unresolved item instead of guessing.",
            "engine_policy": {
                "allow": False,
                "reason": "deterministic_first",
                "max_calls": 1,
                "must_receive_skill_results": True,
                "must_receive_evidence": True,
            },
        }
    }


def _state_capsule_skill(context: dict[str, Any]) -> dict[str, Any]:
    request = context["request"]
    analysis = context.get("analysis", {})
    previous = dict(context.get("session") or {})
    return {
        "state_capsule": {
            "session_id": request.session_id,
            "active_topic": analysis.get("intent") or previous.get("intent") or "general",
            "confirmed_values": {**dict(previous.get("confirmed_values") or {}), **dict(analysis.get("entities") or {})},
            "missing_values": list(previous.get("missing_values") or []),
            "pending_action": previous.get("pending_action"),
            "last_kb_ids": list(previous.get("last_kb_ids") or []),
            "emotion": analysis.get("human_state", {}).get("mode") or previous.get("emotion") or "stable",
            "resolution": previous.get("resolution") or "unresolved",
        }
    }


def _answer_boundary_skill(context: dict[str, Any]) -> dict[str, Any]:
    hits = context.get("hits") or []
    boundaries = [getattr(hit, "answer_boundary", "") or "" for hit in hits]
    blocked = [item for item in boundaries if item.strip()]
    return {
        "answer_boundaries": blocked,
        "requires_action": any("確認" in item or "action" in item.lower() for item in blocked),
        "evidence_refs": [f"kb:{getattr(hit, 'kb_id', '')}" for hit in hits if getattr(hit, "kb_id", "")],
    }


def _deterministic_renderer_skill(context: dict[str, Any]) -> dict[str, Any]:
    hits = context.get("hits") or []
    locale = context["request"].locale
    if not hits:
        clarification = (
            "確認できる情報が不足しています。対象の機能、現在の画面、表示されているエラーを教えてください。"
            if locale == "ja-JP"
            else "I need a little more confirmed context. Tell me the feature, current screen, and shown error."
        )
        return {"draft": clarification, "clarification": clarification, "covered_question_indexes": [], "requires_language_engine": False, "evidence_refs": []}
    sections: list[str] = []
    evidence_refs: list[str] = []
    for hit in hits:
        short_answer = str(getattr(hit, "short_answer", "") or "").strip()
        body = str(getattr(hit, "body", "") or "").strip()
        text = short_answer + (f"\n\n{body}" if body else "")
        if text and text not in sections:
            sections.append(text)
        kb_id = str(getattr(hit, "kb_id", "") or "")
        if kb_id:
            evidence_refs.append(f"kb:{kb_id}")
    analysis = context.get("analysis", {})
    sub_questions = list(analysis.get("sub_questions") or [])
    requires_engine = len(sections) > 1 and (
        len(sub_questions) > 1
        or analysis.get("ambiguity", 0) > 0
        or analysis.get("human_state", {}).get("mode") in {"high_pressure", "supportive"}
    )
    return {
        "draft": "\n\n".join(sections),
        "clarification": None,
        "covered_question_indexes": list(range(len(sub_questions))) if sub_questions else [0],
        "requires_language_engine": requires_engine,
        "evidence_refs": evidence_refs,
    }


def _output_guard_skill(context: dict[str, Any]) -> dict[str, Any]:
    answer = str(context.get("answer") or "").strip()
    violations: list[str] = []
    patterns = (
        (re.compile(r"\b(?:as an ai|as a language model|model provider|qwen|hugging face)\b", re.I), "engine_identity"),
        (re.compile(r"(?:/internal/|\.env\b|src/(?:system|component|feature|part)/)", re.I), "internal_implementation"),
        (re.compile(r"(?:完了しました|成功しました|返金しました|削除しました|refunded|deleted)", re.I), "unverified_action_claim"),
    )
    for pattern, code in patterns:
        if pattern.search(answer):
            violations.append(code)
    return {"guard_passed": not violations and bool(answer), "violations": violations}


def _question_insight_skill(context: dict[str, Any]) -> dict[str, Any]:
    request = context["request"]
    hits = context.get("hits") or []
    status = context.get("status") or "completed"
    if not hits:
        classification = "missing_page"
    elif status == "awaiting_clarification":
        classification = "missing_follow_up"
    elif len(hits) > 1:
        classification = "known_composite"
    else:
        classification = "known_exact"
    normalized = " ".join(request.message.split())[:500]
    return {
        "insight": {
            "classification": classification,
            "normalized_question": normalized,
            "matched_kb_ids": [getattr(hit, "kb_id", "") for hit in hits],
            "safe_auto_update_level": "candidate_A" if classification == "known_exact" else "C",
            "requires_resolved_feedback": True,
            "requires_confirmed_source": classification not in {"known_exact", "known_composite"},
        }
    }


def build_default_registry() -> SkillRegistry:
    registry = SkillRegistry()
    material = "seigo-gace/modular-catalog"
    registry.register(SkillContract("$customer-ai.execution-contract", "Controlled Execution Contract", "intake", "Fix goal, scope, constraints, evidence, stop and uncertainty rules before any engine call.", input_keys=("job_id", "request", "analysis"), output_keys=("execution_contract",), tags=("control", "contract", "evidence"), source_material=f"{material}: KAGRRA controlled execution structure"), _execution_contract_skill)
    registry.register(SkillContract("$customer-ai.state-capsule", "State Capsule", "intake", "Preserve confirmed conversation state and prevent repeated questions.", input_keys=("request", "analysis", "session"), output_keys=("state_capsule",), tags=("state", "memory", "conversation"), source_material=f"{material}: human-context and state-boundary patterns"), _state_capsule_skill)
    registry.register(SkillContract("$customer-ai.answer-boundary", "Answer Boundary", "evidence", "Separate confirmed answer material from action/status checks.", input_keys=("hits",), output_keys=("answer_boundaries", "requires_action"), tags=("evidence", "boundary", "action"), source_material=f"{material}: evaluator contracts and deterministic primitives"), _answer_boundary_skill)
    registry.register(SkillContract("$customer-ai.deterministic-renderer", "Deterministic Answer Renderer", "compose", "Compose an answer from confirmed KB before considering a language engine.", input_keys=("request", "hits", "analysis"), output_keys=("draft", "requires_language_engine"), tags=("compose", "script", "low-cost"), source_material=f"{material}: LLM output-system fallback boundary"), _deterministic_renderer_skill)
    registry.register(SkillContract("$customer-ai.output-guard", "Output Guard", "guard", "Reject engine identity, internal implementation leakage, and unverified action claims.", input_keys=("answer",), output_keys=("guard_passed", "violations"), tags=("security", "output", "completion"), source_material=f"{material}: safe-json and output validation patterns"), _output_guard_skill)
    registry.register(SkillContract("$customer-ai.question-insight", "Question Insight", "insight", "Classify KB gaps without treating user statements as confirmed product facts.", input_keys=("request", "hits", "status"), output_keys=("insight",), tags=("kb", "bot", "improvement"), source_material=f"{material}: deterministic classification and evidence boundary patterns"), _question_insight_skill)
    return registry
