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
            return {
                "message": message,
                "follow_up": bool(context.get("user_goal")),
                "context_used": bool(context.get("user_goal") or context.get("turns")),
                "active_topic": topic,
                "user_goal": goal,
                "confirmed_details": dict(context.get("confirmed_details") or {}),
                "retrieval_query": f"{message} {goal} {topic}",
            }
        if phase == "verify_turn":
            return {"answer": payload["answer"], "passed": True, "violations": []}
        raise AssertionError(phase)


class RecordingEngine:
    def __init__(self):
        self.packets: list[dict] = []

    def available(self) -> bool:
        return True

    def execute(self, packet: dict) -> dict:
        self.packets.append(packet)
        goal = packet["analysis"].get("user_goal") or packet["conversation"].get("user_goal") or packet["message"]
        return {
            "answer": "購入時刻を引き継いで、決済状態とクレジット付与状態を確認します。",
            "user_goal": goal,
            "active_topic": "credit",
            "unresolved_questions": [],
            "used_kb_ids": ["kb-credit"],
            "needs_clarification": False,
        }


@pytest.mark.asyncio
async def test_follow_up_uses_original_goal_recent_turns_and_kb(tmp_path: Path):
    cache = ConversationCache(tmp_path, ttl_seconds=600, max_sessions=8, max_turns=8)
    engine = RecordingEngine()
    queries: list[str] = []
    hit = KBHit(
        kb_id="kb-credit",
        question="購入したクレジットが反映されません",
        short_answer="決済状態と付与状態を確認します。",
        body="購入時刻を確認し、決済とクレジット付与を順に照合します。",
        score=1.0,
    )

    def search(query: str, *, limit: int):
        queries.append(query)
        return [hit]

    core = ConversationCore(v8=FakeV8(), engine=engine, cache=cache, search=search)
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
        message="昨日の夜に買いました。どこを確認すればいい？",
        locale="ja-JP",
        source="astera-app",
    )

    first_result = await core.execute(request=first)
    second_result = await core.execute(request=second)

    assert first_result.context_used is False
    assert second_result.context_used is True
    assert "購入したクレジットが反映されません" in queries[1]
    assert engine.packets[1]["conversation"]["user_goal"] == "購入したクレジットが反映されません"
    assert len(engine.packets[1]["conversation"]["turns"]) == 2
    assert "購入時刻" in second_result.answer


def test_conversation_cache_is_bounded_and_persistent(tmp_path: Path):
    cache = ConversationCache(tmp_path, ttl_seconds=600, max_sessions=2, max_turns=4)
    context = SessionContext(session_id="session_12345678", user_goal="解決する", active_topic="credit")
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
    assert reloaded.user_goal == "解決する"
    assert reloaded.active_topic == "credit"


def test_runtime_does_not_contain_removed_orchestration_layers():
    root = Path(__file__).resolve().parents[1]
    checked = [root / "runtime", root / "v8"]
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for base in checked
        for path in base.rglob("*")
        if path.is_file() and path.suffix in {".py", ".mjs"}
    )
    assert "class SkillRegistry" not in text
    assert "class RoutineBotSupervisor" not in text
    assert "class WorkerPool" not in text
    assert "execution_contract" not in text
    assert "kagura-engine.js" not in text
