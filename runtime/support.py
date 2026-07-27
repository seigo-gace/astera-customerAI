from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from .security import contains_internal_implementation, redact_text, sanitize_structure


KNOWN_SUBJECTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("astera-app", re.compile(r"Astera[- ]?app|アステラアプリ|アプリ", re.I)),
    ("webhook-gateway", re.compile(r"Webhook\s*Gateway|webhook|配送|再送|リプレイ", re.I)),
    ("astera", re.compile(r"Astera|アステラ", re.I)),
    ("api", re.compile(r"API|APIキー|エンドポイント|連携", re.I)),
    ("billing", re.compile(r"料金|価格|支払|決済|請求|クレジット|残高|プラン", re.I)),
    ("account", re.compile(r"アカウント|ログイン|認証|パスワード|退会|解約|削除", re.I)),
    ("support", re.compile(r"問い合わせ|質問|回答|サポート|Customer\s*AI", re.I)),
)

INTENTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("comparison", re.compile(r"違い|比較|どちら|何が異なる|vs\b", re.I)),
    ("troubleshooting", re.compile(r"エラー|動かない|できない|反映されない|届かない|失敗|不具合|直し|確認すれば", re.I)),
    ("procedure", re.compile(r"方法|手順|どうやって|どこから|どこを|何を確認|確認方法|設定|使い方|始め方|導入", re.I)),
    ("pricing", re.compile(r"料金|価格|いくら|費用|課金|クレジット", re.I)),
    ("contract", re.compile(r"契約|解約|退会|返金|更新|支払", re.I)),
    ("availability", re.compile(r"使える|使えます|使えない|利用でき|対応|可能|できる|未実装|提供", re.I)),
    ("limitation", re.compile(r"制限|上限|できない|禁止|対象外|条件", re.I)),
    ("definition", re.compile(r"とは|何ですか|何なの|意味|概要", re.I)),
)

ANSWER_SHAPES = {
    "comparison": "comparison",
    "troubleshooting": "resolution_steps",
    "procedure": "ordered_steps",
    "pricing": "current_fact",
    "contract": "conditions_and_steps",
    "availability": "yes_no_with_conditions",
    "limitation": "boundary",
    "definition": "conclusion_and_detail",
    "general": "conclusion_and_detail",
}

REQUIRED_EVIDENCE = {
    "comparison": ("definition", "responsibility", "limitations"),
    "troubleshooting": ("symptom", "cause", "check", "resolution", "completion_check"),
    "procedure": ("prerequisites", "ordered_steps", "completion_check"),
    "pricing": ("current_price", "conditions", "effective_date"),
    "contract": ("current_terms", "conditions", "ordered_steps"),
    "availability": ("implementation_status", "conditions", "limitations"),
    "limitation": ("answer_boundary", "conditions", "exceptions"),
    "definition": ("definition", "purpose", "scope"),
    "general": ("confirmed_answer", "conditions"),
}

STOP_WORDS = {
    "これ", "それ", "その", "この", "あれ", "あと", "また", "さらに", "について", "教えて", "ください",
    "です", "ます", "したい", "できる", "できます", "どう", "どこ", "何", "なぜ", "場合", "もの", "こと",
    "the", "a", "an", "is", "are", "what", "how", "can", "does", "please",
}


@dataclass(slots=True)
class QuestionTask:
    task_id: str
    text: str
    subject: str
    intent: str
    audience: str
    answer_shape: str
    search_terms: list[str]
    required_evidence: list[str]
    depends_on: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SearchTask:
    task_id: str
    query: str
    terms: list[str]
    required_evidence: list[str]
    priority_sources: list[str]
    comparison_conditions: list[str]
    verification_conditions: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvidenceItem:
    evidence_id: str
    kb_id: str
    task_ids: list[str]
    question: str
    short_answer: str
    body: str
    answer_boundary: str
    target: str
    score: float
    source: str = "kb_search"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PreparedSupport:
    normalized_message: str
    analysis_dictionary: dict[str, Any]
    tasks: list[QuestionTask]
    search_tasks: list[SearchTask]
    evidence: list[EvidenceItem]
    blueprint: dict[str, Any]
    processing_grade: str
    model_required: bool

    def as_packet(self) -> dict[str, Any]:
        return {
            "normalized_message": self.normalized_message,
            "analysis_dictionary": self.analysis_dictionary,
            "question_tasks": [item.as_dict() for item in self.tasks],
            "search_tasks": [item.as_dict() for item in self.search_tasks],
            "evidence": [item.as_dict() for item in self.evidence],
            "blueprint": self.blueprint,
            "processing_grade": self.processing_grade,
        }


@dataclass(slots=True)
class ResponseValidation:
    passed: bool
    violations: list[str]
    answered_task_ids: list[str]
    unresolved_task_ids: list[str]
    used_evidence_ids: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_japanese(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = re.sub(r"[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]", "", normalized)
    normalized = normalized.replace("｡", "。").replace("､", "、")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def detect_subject(text: str, previous: str = "") -> str:
    for subject, pattern in KNOWN_SUBJECTS:
        if pattern.search(text):
            return subject
    return previous or "general"


def detect_intent(text: str) -> str:
    for intent, pattern in INTENTS:
        if pattern.search(text):
            return intent
    return "general"


def detect_audience(text: str, source: str) -> str:
    if re.search(r"開発者|実装|コード|SDK|API|Webhook|JSON|HTTP|GitHub", text, re.I):
        return "developer"
    if re.search(r"法人|企業|スポンサー|投資|提携", text, re.I):
        return "business"
    return "registered_user" if source == "astera-app" else "general_user"


def _split_document(text: str) -> list[str]:
    lines = [re.sub(r"^\s*(?:[-*・]|\d+[.)．、]|[①-⑳])\s*", "", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line] or [text]
    candidates: list[tuple[str, bool]] = []
    for line in lines:
        for raw_part in re.split(r"(?<=[?？!！。])\s*", line):
            for raw_item in re.split(r"\s*(?:それと|さらに|加えて|あと、|また、)\s*", raw_part):
                item = raw_item.strip()
                if not item:
                    continue
                question_mark = item.endswith(("?", "？"))
                cleaned = item.strip(" \t。?？")
                if not cleaned:
                    continue
                actionable = question_mark or detect_intent(cleaned) != "general" or bool(re.search(r"教えて|知りたい|確認したい", cleaned))
                candidates.append((cleaned, actionable))
    selected = [item for item, actionable in candidates if actionable]
    if not selected:
        selected = [item for item, _ in candidates]
    deduped: list[str] = []
    for item in selected:
        if item not in deduped:
            deduped.append(item)
    return deduped[:8] or [text]


def extract_search_terms(text: str, *, subject: str, intent: str, active_topic: str = "") -> list[str]:
    working = normalize_japanese(text)
    working = re.sub(r"(?:について|という|できますか|できるか|教えてください|教えて|とは|何ですか|どうですか)", " ", working)
    working = re.sub(r"[?？!！。、,/:：()（）\[\]「」『』\-]+", " ", working)
    working = re.sub(r"(?:は|が|を|に|で|と|や|へ|から|まで|より|の)", " ", working)
    candidates = re.findall(r"[A-Za-z][A-Za-z0-9_.-]{1,40}|[一-龯ぁ-んァ-ヶー]{2,24}", working)
    seeded = [subject, active_topic, intent, *candidates]
    terms: list[str] = []
    for term in seeded:
        value = str(term or "").strip().lower()
        if not value or value == "general" or value in STOP_WORDS or value in terms:
            continue
        terms.append(value)
    return terms[:12]


def build_analysis_dictionary(
    *, message: str, context: dict[str, Any], analysis: dict[str, Any], tasks: list[QuestionTask]
) -> dict[str, Any]:
    missing: list[str] = []
    details = dict(analysis.get("confirmed_details") or context.get("confirmed_details") or {})
    if any(item.intent == "troubleshooting" for item in tasks):
        if not details.get("error_code"):
            missing.append("shown_error_or_symptom")
        if not details.get("relative_time"):
            missing.append("occurrence_timing")
    premises = [str(key) for key in details]
    if context.get("user_goal"):
        premises.append("existing_user_goal")
    return {
        "purpose": str(analysis.get("user_goal") or context.get("user_goal") or message),
        "targets": sorted({item.subject for item in tasks}),
        "conditions": details,
        "constraints": [
            "use_confirmed_kb_only_for_product_facts",
            "do_not_claim_unexecuted_actions",
            "do_not_expose_private_implementation",
            "answer_every_detected_question_or_mark_it_unresolved",
        ],
        "missing_information": list(dict.fromkeys(missing)),
        "premises": list(dict.fromkeys(premises)),
        "uncertainty": [] if tasks else ["question_decomposition_failed"],
    }


def _task_from_dict(item: dict[str, Any], index: int, *, source: str, previous_subject: str, active_topic: str) -> QuestionTask:
    text = normalize_japanese(str(item.get("text") or item.get("question") or ""))
    subject = str(item.get("subject") or detect_subject(text, previous_subject))
    intent = str(item.get("intent") or detect_intent(text))
    terms = [str(value).strip() for value in item.get("search_terms", []) if str(value).strip()]
    if not terms:
        terms = extract_search_terms(text, subject=subject, intent=intent, active_topic=active_topic)
    required = item.get("required_evidence") or REQUIRED_EVIDENCE.get(intent, REQUIRED_EVIDENCE["general"])
    return QuestionTask(
        task_id=str(item.get("task_id") or f"q{index + 1}"),
        text=text,
        subject=subject,
        intent=intent,
        audience=str(item.get("audience") or detect_audience(text, source)),
        answer_shape=str(item.get("answer_shape") or ANSWER_SHAPES.get(intent, "conclusion_and_detail")),
        search_terms=terms,
        required_evidence=[str(value) for value in required],
        depends_on=[str(value) for value in item.get("depends_on", [])],
    )


def decompose_questions(
    *, message: str, source: str, context: dict[str, Any], analysis: dict[str, Any]
) -> list[QuestionTask]:
    previous_subject = str(context.get("active_topic") or analysis.get("active_topic") or "")
    active_topic = str(analysis.get("active_topic") or previous_subject)
    supplied = analysis.get("question_tasks")
    if isinstance(supplied, list) and supplied:
        tasks = [
            _task_from_dict(item, index, source=source, previous_subject=previous_subject, active_topic=active_topic)
            for index, item in enumerate(supplied)
            if isinstance(item, dict)
        ]
        if tasks:
            return tasks[:8]
    return [
        _task_from_dict({"text": part}, index, source=source, previous_subject=previous_subject, active_topic=active_topic)
        for index, part in enumerate(_split_document(message))
    ]


def plan_search(tasks: Iterable[QuestionTask], *, user_goal: str, active_topic: str) -> list[SearchTask]:
    del user_goal
    plans: list[SearchTask] = []
    for task in tasks:
        terms = list(task.search_terms)
        for value in (task.subject, task.intent, active_topic):
            value = str(value or "").strip().lower()
            if value and value != "general" and value not in terms:
                terms.append(value)
        plans.append(
            SearchTask(
                task_id=task.task_id,
                query=(" ".join(terms[:12]) or task.text)[:1000],
                terms=terms[:12],
                required_evidence=task.required_evidence,
                priority_sources=["approved_notion_kb", "runtime_kb_snapshot", "bounded_session_evidence"],
                comparison_conditions=["same_product_scope"] if task.intent == "comparison" else [],
                verification_conditions=["public_status", "implementation_status", "answer_boundary", "evidence_path"],
            )
        )
    return plans


class EvidenceRetriever:
    def __init__(self, search: Callable[..., list[Any]], *, max_parallel: int = 4, limit_per_task: int = 5):
        self.search = search
        self.max_parallel = max(1, min(max_parallel, 8))
        self.limit_per_task = max(1, min(limit_per_task, 8))

    async def retrieve(self, plans: list[SearchTask]) -> list[EvidenceItem]:
        semaphore = asyncio.Semaphore(self.max_parallel)

        async def run(plan: SearchTask) -> tuple[SearchTask, list[Any]]:
            async with semaphore:
                hits = await asyncio.to_thread(self.search, plan.query, limit=self.limit_per_task)
                return plan, hits

        results = await asyncio.gather(*(run(plan) for plan in plans))
        merged: dict[str, EvidenceItem] = {}
        for plan, hits in results:
            for hit in hits:
                kb_id = str(getattr(hit, "kb_id", ""))
                if not kb_id:
                    continue
                item = merged.get(kb_id)
                if item is None:
                    item = EvidenceItem(
                        evidence_id=f"kb:{kb_id}", kb_id=kb_id, task_ids=[],
                        question=str(getattr(hit, "question", ""))[:1000],
                        short_answer=str(getattr(hit, "short_answer", ""))[:2000],
                        body=str(getattr(hit, "body", ""))[:5000],
                        answer_boundary=str(getattr(hit, "answer_boundary", ""))[:1000],
                        target=str(getattr(hit, "target", ""))[:300],
                        score=float(getattr(hit, "score", 0.0)), source="kb_search",
                    )
                    merged[kb_id] = item
                if plan.task_id not in item.task_ids:
                    item.task_ids.append(plan.task_id)
        return sorted(merged.values(), key=lambda item: (-item.score, item.kb_id))


def _cached_evidence(context: dict[str, Any], tasks: list[QuestionTask]) -> list[EvidenceItem]:
    cached = context.get("evidence_cache")
    if not isinstance(cached, dict):
        return []
    result: list[EvidenceItem] = []
    for evidence_id, raw in list(cached.items())[-16:]:
        if not isinstance(raw, dict):
            continue
        combined = normalize_japanese(" ".join(str(raw.get(key) or "") for key in ("question", "short_answer", "answer_boundary"))).lower()
        task_ids: list[str] = []
        for task in tasks:
            terms = [task.subject.lower(), *[term.lower() for term in task.search_terms]]
            if any(term and term != "general" and term in combined for term in terms):
                task_ids.append(task.task_id)
        if not task_ids:
            continue
        kb_id = str(raw.get("kb_id") or str(evidence_id).removeprefix("kb:"))
        result.append(
            EvidenceItem(
                evidence_id=str(evidence_id), kb_id=kb_id, task_ids=task_ids,
                question=str(raw.get("question") or "")[:1000],
                short_answer=str(raw.get("short_answer") or "")[:2000],
                body="", answer_boundary=str(raw.get("answer_boundary") or "")[:1000],
                target="", score=0.1, source="session_evidence_cache",
            )
        )
    return result


def _merge_evidence(fresh: list[EvidenceItem], cached: list[EvidenceItem]) -> list[EvidenceItem]:
    merged = {item.evidence_id: item for item in fresh}
    for item in cached:
        existing = merged.get(item.evidence_id)
        if existing is None:
            merged[item.evidence_id] = item
            continue
        existing.task_ids = list(dict.fromkeys([*existing.task_ids, *item.task_ids]))
    return sorted(merged.values(), key=lambda item: (-item.score, item.evidence_id))


def _clarification_for(task: QuestionTask) -> str:
    if task.intent == "troubleshooting":
        return "表示されている症状またはエラー、現在の画面、直前に行った操作を確認すると原因と解決手順を絞れます。"
    if task.intent in {"pricing", "contract"}:
        return "料金・契約条件は現在の正本情報で確認する必要があるため、対象プランまたは契約状態を特定します。"
    if task.intent == "comparison":
        return "比較対象と、重視する条件を特定すると正確に違いを整理できます。"
    if task.intent == "procedure":
        return "対象の機能、現在の画面、実行したい操作を確認すると正しい順序で案内できます。"
    return "対象の製品または機能と、確認したい点を特定すると正確に回答できます。"


def _render_task_section(task: QuestionTask, evidence: list[EvidenceItem]) -> tuple[str, list[str], list[dict[str, Any]]]:
    matched = [item for item in evidence if task.task_id in item.task_ids][:4]
    if not matched:
        return _clarification_for(task), [], []
    blocks: list[str] = []
    evidence_blocks: list[dict[str, Any]] = []
    used: list[str] = []
    for item in matched:
        conclusion = item.short_answer.strip()
        detail = item.body.strip()
        text = conclusion
        if detail and detail not in conclusion:
            text += "\n\n" + detail
        if task.answer_shape == "comparison" and len(matched) > 1:
            text = f"#### {item.question or item.kb_id}\n\n{text}"
        if text and text not in blocks:
            blocks.append(text)
        used.append(item.evidence_id)
        evidence_blocks.append(
            {
                "evidence_id": item.evidence_id,
                "question": item.question,
                "conclusion": conclusion,
                "detail": detail,
                "answer_boundary": item.answer_boundary,
                "source": item.source,
            }
        )
    return "\n\n".join(blocks), list(dict.fromkeys(used)), evidence_blocks


def _selected_runtime_skills(tasks: list[QuestionTask], context_used: bool) -> list[str]:
    selected = ["japanese_normalization", "question_task_decomposition", "evidence_binding", "answer_coverage_validation"]
    if context_used:
        selected.append("conversation_reference_resolution")
    if len(tasks) > 1:
        selected.extend(["multi_question_management", "sectioned_document_composition"])
    for task in tasks:
        selected.append(f"audience_{task.audience}")
        selected.append(f"shape_{task.answer_shape}")
    return list(dict.fromkeys(selected))


def build_blueprint(
    *, tasks: list[QuestionTask], evidence: list[EvidenceItem], user_goal: str,
    active_topic: str, locale: str, context_used: bool = False
) -> dict[str, Any]:
    sections: list[dict[str, Any]] = []
    deterministic: list[str] = []
    unresolved: list[str] = []
    all_used: list[str] = []
    for index, task in enumerate(tasks, start=1):
        body, used, evidence_blocks = _render_task_section(task, evidence)
        if not used:
            unresolved.append(task.task_id)
        all_used.extend(used)
        sections.append(
            {
                "task_id": task.task_id, "heading": task.text, "answer_shape": task.answer_shape,
                "body": body, "evidence_ids": used, "evidence_blocks": evidence_blocks,
                "resolved": bool(used), "audience": task.audience,
            }
        )
        deterministic.append(body if len(tasks) == 1 else f"### {index}. {task.text}\n\n{body}")
    intents = {item.intent for item in tasks}
    model_required = len(tasks) > 1 or "comparison" in intents or len(evidence) > 3
    return {
        "user_goal": user_goal, "active_topic": active_topic, "locale": locale,
        "sections": sections, "unresolved_task_ids": unresolved,
        "selected_runtime_skills": _selected_runtime_skills(tasks, context_used),
        "prohibited_claims": [
            "facts_not_present_in_evidence", "unexecuted_action_completion",
            "private_implementation_details", "model_or_provider_identity",
        ],
        "required_style": ["conclusion_first", "specific", "polite", "resolve_to_next_step", "no_repeated_questions"],
        "deterministic_answer": "\n\n".join(deterministic).strip(),
        "evidence_ids": list(dict.fromkeys(all_used)), "model_required": model_required,
    }


def classify_processing_grade(tasks: list[QuestionTask], evidence: list[EvidenceItem], blueprint: dict[str, Any]) -> str:
    if blueprint.get("unresolved_task_ids"):
        return "L3_CONTEXT_REQUIRED"
    if len(tasks) > 1:
        return "L2_MULTI_TASK_COMPOSE"
    if len(evidence) > 1 or tasks[0].intent in {"comparison", "troubleshooting", "procedure"}:
        return "L1_STRUCTURED_COMPOSE"
    return "L0_DETERMINISTIC_EXACT"


class SupportRuntime:
    def __init__(self, *, search: Callable[..., list[Any]], max_parallel_search: int = 4):
        self.retriever = EvidenceRetriever(search, max_parallel=max_parallel_search)

    async def prepare(
        self, *, message: str, locale: str, source: str,
        context: dict[str, Any], analysis: dict[str, Any]
    ) -> PreparedSupport:
        normalized = normalize_japanese(message)
        tasks = decompose_questions(message=normalized, source=source, context=context, analysis=analysis)
        user_goal = str(analysis.get("user_goal") or context.get("user_goal") or normalized)
        active_topic = str(analysis.get("active_topic") or context.get("active_topic") or detect_subject(normalized))
        dictionary = build_analysis_dictionary(message=normalized, context=context, analysis=analysis, tasks=tasks)
        plans = plan_search(tasks, user_goal=user_goal, active_topic=active_topic)
        fresh = await self.retriever.retrieve(plans)
        evidence = _merge_evidence(fresh, _cached_evidence(context, tasks))
        blueprint = build_blueprint(
            tasks=tasks, evidence=evidence, user_goal=user_goal, active_topic=active_topic,
            locale=locale, context_used=bool(context.get("user_goal") or context.get("turns")),
        )
        grade = classify_processing_grade(tasks, evidence, blueprint)
        return PreparedSupport(
            normalized_message=normalized, analysis_dictionary=dictionary,
            tasks=tasks, search_tasks=plans, evidence=evidence, blueprint=blueprint,
            processing_grade=grade, model_required=bool(blueprint.get("model_required")),
        )


def validate_response(
    *, answer: str, prepared: PreparedSupport, answered_task_ids: Iterable[str],
    unresolved_task_ids: Iterable[str], used_evidence_ids: Iterable[str],
    external_violations: Iterable[str] = ()
) -> ResponseValidation:
    text = str(answer or "").strip()
    answered = list(dict.fromkeys(str(value) for value in answered_task_ids if str(value)))
    unresolved = list(dict.fromkeys(str(value) for value in unresolved_task_ids if str(value)))
    used = list(dict.fromkeys(str(value) for value in used_evidence_ids if str(value)))
    task_ids = {item.task_id for item in prepared.tasks}
    evidence_ids = {item.evidence_id for item in prepared.evidence}
    violations = list(external_violations)
    if not text:
        violations.append("empty_answer")
    if not set(answered).issubset(task_ids) or not set(unresolved).issubset(task_ids):
        violations.append("unknown_task_reference")
    if task_ids.difference(answered).difference(unresolved):
        violations.append("question_coverage_missing")
    if not set(used).issubset(evidence_ids):
        violations.append("unknown_evidence_reference")
    if evidence_ids and answered and not used:
        violations.append("evidence_grounding_missing")
    if re.search(r"(?:情報がありません|回答できません|お問い合わせください|I (?:cannot|can't) answer)", text, re.I):
        violations.append("generic_non_answer")
    if re.search(r"(?:返金しました|削除しました|解約しました|処理しました|設定しました|refunded|deleted|cancelled)", text, re.I):
        violations.append("unverified_action_claim")
    if contains_internal_implementation(text):
        violations.append("internal_implementation")
    return ResponseValidation(
        passed=not violations, violations=list(dict.fromkeys(violations)),
        answered_task_ids=answered, unresolved_task_ids=unresolved, used_evidence_ids=used,
    )


class FeedbackStore:
    """Stores anonymized review candidates; it never publishes to Notion automatically."""

    def __init__(self, root: Path):
        self.root = root / "feedback"
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "candidate-index.json"
        self._lock = threading.Lock()

    def record(
        self, *, session_id: str, message: str, prepared: PreparedSupport,
        validation: ResponseValidation, status: str
    ) -> str | None:
        safe_message = redact_text(normalize_japanese(message)).text[:1000]
        safe_tasks = sanitize_structure([item.as_dict() for item in prepared.tasks])
        safe_search_tasks = sanitize_structure([item.as_dict() for item in prepared.search_tasks])
        safe_kb_ids = [redact_text(item.kb_id).text[:160] for item in prepared.evidence]
        fingerprint_source = json.dumps(
            {"message": safe_message, "tasks": safe_tasks, "kb_ids": sorted(safe_kb_ids)},
            ensure_ascii=False, sort_keys=True,
        )
        fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
        candidate_id = "qic_" + fingerprint[:24]
        with self._lock:
            index = self._read_index()
            if fingerprint in index:
                return None
            gap_types: list[str] = []
            if prepared.blueprint.get("unresolved_task_ids"):
                gap_types.append("kb_content_gap")
            if any(plan.query and not any(plan.task_id in item.task_ids for item in prepared.evidence) for plan in prepared.search_tasks):
                gap_types.append("search_term_or_content_gap")
            if validation.violations:
                gap_types.append("answer_quality_gap")
            row = {
                "candidate_id": candidate_id, "fingerprint": fingerprint,
                "created_at": datetime.now(UTC).isoformat(),
                "session_hash": hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24],
                "normalized_question": safe_message,
                "question_tasks": safe_tasks, "search_tasks": safe_search_tasks,
                "matched_kb_ids": sorted(safe_kb_ids),
                "gap_types": list(dict.fromkeys(gap_types)), "status": status,
                "validation_violations": sanitize_structure(validation.violations),
                "approval_required": True, "auto_publish": False,
            }
            daily = self.root / (datetime.now(UTC).date().isoformat() + ".jsonl")
            with daily.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            index[fingerprint] = candidate_id
            if len(index) > 10000:
                index = dict(list(index.items())[-10000:])
            temporary = self.index_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(index, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            temporary.replace(self.index_path)
        return candidate_id

    def _read_index(self) -> dict[str, str]:
        if not self.index_path.exists():
            return {}
        try:
            value = json.loads(self.index_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}
