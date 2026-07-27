from __future__ import annotations

from pathlib import Path

import pytest

from runtime.control import ConversationCore
from runtime.conversation import ConversationCache
from runtime.schemas import KBHit, MessagePayload, SessionContext


class FakeV8:
    async def request(self, phase: str, payload: dict, *, timeout: float = 15.0) -> dict:
        if phase == "analyze_turn":
            context = payload["context"]
            message = payload["message"]
            goal = context.get("user_goal") or message
            topic = context.get("active_topic") or "credit"
            parts = [item.strip(" 。") for item in message.replace("？", "?").split("?") if item.strip(" 。")]
            tasks = []
            for index, text in enumerate(parts or [message]):
                intent = "procedure" if "どこ" in text or "方法" in text else "general"
                tasks.append(
                    {
                        "task_id": f"q{index + 1}",
                        "text": text,
                        "subject": topic,
                        "intent": intent,
                        "audience": "registered_user",
                        "answer_shape": "ordered_steps" if intent == "procedure" else "conclusion_and_detail",
                        "search_terms": [topic, text],
                        "required_evidence": ["confirmed_answer", "conditions"],
                        "depends_on": [],
                    }
                )
            return {
                "message": message,
                "follow_up": bool(context.get("user_goal")),
                "context_used": bool(context.get("user_goal") or context.get("turns")),
                "active_topic": topic,
                "user_goal": goal,
                "confirmed_details": dict(context.get("confirmed_details") or {}),
                "retrieval_query": f"{message} {goal} {topic}",
                "question_tasks": tasks,
            }
        if phase == "verify_turn":
            return {"answer": payload["answer"], "passed": True, "violations": []}
        raise AssertionError(phase)


class RecordingEngine:
    def __init__(self, outputs: list[dict] | None = None):
        self.packets: list[dict] = []
        self.outputs = outputs or []

    def available(self) -> bool:
        return True

    def execute(self, packet: dict) -> dict:
        self.packets.append(packet)
        if self.outputs:
            return self.outputs.pop(0)
        tasks = packet["support_packet"]["question_tasks"]
        evidence = packet["support_packet"]["evidence"]
        return {
            "answer": "決済状態とクレジット付与状態を順に確認します。購入時刻も照合してください。",
            "user_goal": packet["support_packet"]["blueprint"]["user_goal"],
            "active_topic": packet["support_packet"]["blueprint"]["active_topic"],
            "answered_task_ids": [item["task_id"] for item in tasks],
            "unresolved_task_ids": [],
            "used_evidence_ids": [item["evidence_id"] for item in evidence],
            "needs_clarification": False,
        }


class NeverEngine(RecordingEngine):
    def execute(self, packet: dict) -> dict:
        raise AssertionError("exact deterministic answer must not call model")


def credit_hit() -> KBHit:
    return KBHit(
        kb_id="kb-credit",
        question="購入したクレジットが反映されません",
        short_answer="決済状態と付与状態を確認します。",
        body="購入時刻を確認し、決済とクレジット付与を順に照合します。",
        score=12.0,
    )


@pytest.mark.asyncio
async def test_follow_up_keeps_original_goal_and_persists_structured_state(tmp_path: Path):
    cache = ConversationCache(tmp_path, ttl_seconds=600, max_sessions=8, max_turns=8)
    queries: list[str] = []

    def search(query: str, *, limit: int):
        queries.append(query)
        return [credit_hit()]

    core = ConversationCore(v8=FakeV8(), engine=NeverEngine(), cache=cache, search=search)
    first = MessagePayload(
        session_id="session_12345678",
        message_id="message_12345678",
        message="購入したクレジットが反映されません",
        locale="ja-JP",
        source="astera-app",
    )
    second = MessagePayload(
        session_id="session_12345678",
        message_id="message_22345678",
        message="どこを確認すればいい？",
        locale="ja-JP",
        source="astera-app",
    )

    first_result = await core.execute(request=first)
    second_result = await core.execute(request=second)
    restored = ConversationCache(tmp_path, ttl_seconds=600, max_sessions=8, max_turns=8).get("session_12345678")

    assert first_result.context_used is False
    assert second_result.context_used is True
    assert restored.user_goal == "購入したクレジットが反映されません"
    assert restored.question_ledger
    assert restored.evidence_cache["kb:kb-credit"]["kb_id"] == "kb-credit"
    assert restored.last_blueprint["sections"]
    assert len(restored.turns) == 4
    assert any("credit" in query for query in queries)


@pytest.mark.asyncio
async def test_multi_question_builds_separate_tasks_and_model_receives_support_packet(tmp_path: Path):
    cache = ConversationCache(tmp_path, ttl_seconds=600, max_sessions=8, max_turns=8)
    engine = RecordingEngine()
    calls: list[str] = []

    def search(query: str, *, limit: int):
        calls.append(query)
        return [credit_hit()]

    core = ConversationCore(v8=FakeV8(), engine=engine, cache=cache, search=search)
    request = MessagePayload(
        session_id="session_multi001",
        message_id="message_multi001",
        message="なぜ反映されない？どこを確認すればいい？",
        locale="ja-JP",
        source="astera-app",
    )
    result = await core.execute(request=request)

    assert result.status == "completed"
    assert result.engine_invoked is True
    assert result.processing_grade == "L2_MULTI_TASK_COMPOSE"
    assert [item["task_id"] for item in result.question_tasks] == ["q1", "q2"]
    assert len(calls) == 2
    packet = engine.packets[0]
    assert packet["support_packet"]["question_tasks"][0]["text"] == "なぜ反映されない"
    assert packet["support_packet"]["blueprint"]["sections"][1]["task_id"] == "q2"
    assert packet["support_packet"]["evidence"][0]["evidence_id"] == "kb:kb-credit"


@pytest.mark.asyncio
async def test_invalid_model_answer_gets_exactly_one_repair(tmp_path: Path):
    cache = ConversationCache(tmp_path, ttl_seconds=600, max_sessions=8, max_turns=8)
    engine = RecordingEngine(
        outputs=[
            {
                "answer": "お問い合わせください。",
                "user_goal": "解決する",
                "active_topic": "credit",
                "answered_task_ids": ["q1"],
                "unresolved_task_ids": [],
                "used_evidence_ids": [],
                "needs_clarification": False,
            },
            {
                "answer": "決済状態を確認し、その後クレジット付与状態を確認してください。",
                "user_goal": "解決する",
                "active_topic": "credit",
                "answered_task_ids": ["q1", "q2"],
                "unresolved_task_ids": [],
                "used_evidence_ids": ["kb:kb-credit"],
                "needs_clarification": False,
            },
        ]
    )

    core = ConversationCore(v8=FakeV8(), engine=engine, cache=cache, search=lambda query, limit: [credit_hit()])
    request = MessagePayload(
        session_id="session_repair01",
        message_id="message_repair01",
        message="なぜ反映されない？どこを確認すればいい？",
        locale="ja-JP",
        source="astera-app",
    )
    result = await core.execute(request=request)

    assert result.status == "completed"
    assert result.repair_attempted is True
    assert len(engine.packets) == 2
    assert engine.packets[1]["repair"]["attempt"] == 1
    assert "generic_non_answer" in engine.packets[1]["repair"]["violations"]
    assert "決済状態" in result.answer


@pytest.mark.asyncio
async def test_exact_kb_answer_uses_deterministic_path_without_model(tmp_path: Path):
    cache = ConversationCache(tmp_path, ttl_seconds=600, max_sessions=8, max_turns=8)
    core = ConversationCore(v8=FakeV8(), engine=NeverEngine(), cache=cache, search=lambda query, limit: [credit_hit()])
    request = MessagePayload(
        session_id="session_exact001",
        message_id="message_exact001",
        message="クレジットが反映されません",
        locale="ja-JP",
        source="astera-app",
    )
    result = await core.execute(request=request)

    assert result.status == "completed"
    assert result.engine_invoked is False
    assert result.processing_grade == "L0_DETERMINISTIC_EXACT"
    assert "購入時刻" in result.answer


def test_conversation_cache_bounds_new_runtime_state(tmp_path: Path):
    cache = ConversationCache(tmp_path, ttl_seconds=600, max_sessions=2, max_turns=4)
    context = SessionContext(
        session_id="session_12345678",
        user_goal="解決する",
        active_topic="credit",
        answered_question_ids=[f"m:q{i}" for i in range(50)],
        question_ledger=[{"ledger_id": f"m:q{i}", "status": "answered"} for i in range(40)],
        evidence_cache={f"kb:{i}": {"kb_id": str(i)} for i in range(30)},
        last_blueprint={"sections": [{"body": "x" * 1000} for _ in range(30)]},
    )
    for index in range(3):
        context = cache.append_turns(
            context,
            user_text=f"質問{index}",
            assistant_text=f"回答{index}",
            message_id=f"message_{index:08d}",
            kb_ids=["kb-credit"],
        )
    cache.save(context)

    reloaded = ConversationCache(tmp_path, ttl_seconds=600, max_sessions=2, max_turns=4).get("session_12345678")
    assert len(reloaded.turns) == 4
    assert len(reloaded.answered_question_ids) == 32
    assert len(reloaded.question_ledger) == 24
    assert len(reloaded.evidence_cache) == 16
