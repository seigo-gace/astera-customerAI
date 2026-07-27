from __future__ import annotations

from pathlib import Path

import pytest

from runtime.schemas import KBHit
from runtime.support import FeedbackStore, SupportRuntime, validate_response


def hit(kb_id: str, question: str, short: str) -> KBHit:
    return KBHit(
        kb_id=kb_id,
        question=question,
        short_answer=short,
        body=short + " 詳細な条件と手順を説明します。",
        score=10.0,
        answer_boundary="実際の契約状態や処理結果は正本Systemで確認する",
        target="一般利用者",
    )


@pytest.mark.asyncio
async def test_support_runtime_prepares_analysis_search_evidence_and_blueprint():
    searches: list[str] = []

    def search(query: str, *, limit: int):
        searches.append(query)
        if "webhook" in query.lower():
            return [hit("kb-webhook", "Webhook Gatewayとは", "Webhookの受信と配送を管理します。")]
        return [hit("kb-astera", "Asteraとは", "AIへ判断材料を渡すSystemです。")]

    runtime = SupportRuntime(search=search, max_parallel_search=2)
    prepared = await runtime.prepare(
        message="Asteraとは何ですか？Webhook Gatewayとの違いは？",
        locale="ja-JP",
        source="astera-hp",
        context={"user_goal": "製品の違いを理解する", "active_topic": "astera"},
        analysis={
            "user_goal": "製品の違いを理解する",
            "active_topic": "astera",
            "question_tasks": [
                {
                    "task_id": "q1",
                    "text": "Asteraとは何ですか",
                    "subject": "astera",
                    "intent": "definition",
                    "search_terms": ["astera", "概要"],
                },
                {
                    "task_id": "q2",
                    "text": "Webhook Gatewayとの違いは",
                    "subject": "webhook-gateway",
                    "intent": "comparison",
                    "search_terms": ["webhook", "astera", "違い"],
                },
            ],
        },
    )

    assert prepared.processing_grade == "L2_MULTI_TASK_COMPOSE"
    assert len(prepared.tasks) == 2
    assert len(searches) == 2
    assert {item.kb_id for item in prepared.evidence} == {"kb-astera", "kb-webhook"}
    assert prepared.blueprint["sections"][0]["resolved"] is True
    assert prepared.blueprint["sections"][1]["answer_shape"] == "comparison"
    assert prepared.analysis_dictionary["purpose"] == "製品の違いを理解する"


@pytest.mark.asyncio
async def test_missing_evidence_creates_specific_unresolved_section():
    runtime = SupportRuntime(search=lambda query, limit: [])
    prepared = await runtime.prepare(
        message="ログインできない。どこを確認すればいい？",
        locale="ja-JP",
        source="astera-app",
        context={},
        analysis={},
    )

    assert prepared.processing_grade == "L3_CONTEXT_REQUIRED"
    assert prepared.blueprint["unresolved_task_ids"]
    assert "現在の画面" in prepared.blueprint["deterministic_answer"]
    assert "回答できません" not in prepared.blueprint["deterministic_answer"]


def test_validation_requires_every_task_to_be_answered_or_unresolved():
    from runtime.support import PreparedSupport, QuestionTask, SearchTask

    tasks = [
        QuestionTask("q1", "質問1", "astera", "general", "general_user", "conclusion_and_detail", ["astera"], ["confirmed_answer"]),
        QuestionTask("q2", "質問2", "api", "general", "developer", "conclusion_and_detail", ["api"], ["confirmed_answer"]),
    ]
    prepared = PreparedSupport(
        normalized_message="質問1？質問2？",
        analysis_dictionary={},
        tasks=tasks,
        search_tasks=[SearchTask("q1", "astera", ["astera"], [], [], [], [])],
        evidence=[],
        blueprint={"unresolved_task_ids": ["q2"], "sections": [], "evidence_ids": []},
        processing_grade="L2_MULTI_TASK_COMPOSE",
        model_required=True,
    )
    validation = validate_response(
        answer="質問1へ回答します。",
        prepared=prepared,
        answered_task_ids=["q1"],
        unresolved_task_ids=[],
        used_evidence_ids=[],
    )
    assert validation.passed is False
    assert "question_coverage_missing" in validation.violations


@pytest.mark.asyncio
async def test_feedback_store_anonymizes_and_deduplicates(tmp_path: Path):
    runtime = SupportRuntime(search=lambda query, limit: [])
    prepared = await runtime.prepare(
        message="メールは user@example.com です。未回答の質問があります",
        locale="ja-JP",
        source="astera-app",
        context={},
        analysis={},
    )
    validation = validate_response(
        answer=prepared.blueprint["deterministic_answer"],
        prepared=prepared,
        answered_task_ids=[],
        unresolved_task_ids=prepared.blueprint["unresolved_task_ids"],
        used_evidence_ids=[],
    )
    store = FeedbackStore(tmp_path)
    first = store.record(
        session_id="session_feedback1",
        message="メールは user@example.com です。未回答の質問があります",
        prepared=prepared,
        validation=validation,
        status="awaiting_clarification",
    )
    second = store.record(
        session_id="session_feedback1",
        message="メールは user@example.com です。未回答の質問があります",
        prepared=prepared,
        validation=validation,
        status="awaiting_clarification",
    )

    assert first and first.startswith("qic_")
    assert second is None
    content = next(store.root.glob("*.jsonl")).read_text(encoding="utf-8")
    assert "user@example.com" not in content
    assert '"approval_required": true' in content
    assert '"auto_publish": false' in content
