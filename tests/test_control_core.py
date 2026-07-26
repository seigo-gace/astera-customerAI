from __future__ import annotations

from pathlib import Path

import pytest

from runtime.control import ControlledExecutionCore
from runtime.schemas import KBHit, MessagePayload
from runtime.skills import SkillContract, SkillRegistry, build_default_registry


class FakeV8:
    async def request(self, phase: str, payload: dict, *, timeout: float = 15.0) -> dict:
        if phase == "analyze":
            return {
                "message": payload["message"],
                "search_query": payload["message"],
                "intent": "credit",
                "entities": {},
                "sub_questions": [payload["message"]],
                "ambiguity": 0,
                "human_state": {"mode": "stable"},
                "worker_results": ["normalize", "human_context", "route", "decompose", "entities", "safety"],
            }
        if phase == "plan":
            return {"engine_required": False, "engine_reason": "deterministic_skills_sufficient", "missing_values": [], "clarification": None, "action": None, "required_question_indexes": [0]}
        if phase == "verify":
            return {"answer": payload["answer"], "violations": [], "completion": {"passed": True, "missing": []}}
        raise AssertionError(phase)


class EngineMustNotRun:
    def available(self) -> bool:
        return True

    def execute(self, packet: dict) -> dict:
        raise AssertionError("language engine was called before deterministic skills were exhausted")


@pytest.mark.asyncio
async def test_control_core_completes_with_structured_skills_without_engine():
    core = ControlledExecutionCore(v8=FakeV8(), engine=EngineMustNotRun(), skills=build_default_registry())
    request = MessagePayload(session_id="session_12345678", message_id="message_12345678", message="購入したクレジットが反映されません", locale="ja-JP", source="astera-app")
    hit = KBHit(kb_id="kb-credit", question=request.message, short_answer="決済状態と付与状態を確認します。", body="購入時刻を確認し、決済とクレジット付与を順に照合します。", score=1.0)
    result = await core.execute(job_id="job_12345678", request=request, session={}, search=lambda query, limit: [hit])
    assert result.status == "completed"
    assert result.engine_invoked is False
    assert "$customer-ai.execution-contract" in result.execution["selected_skill_ids"]
    assert "$customer-ai.deterministic-renderer" in result.execution["selected_skill_ids"]
    assert len(result.execution["v8_parallel_workers"]) == 6
    assert "決済状態" in result.answer


@pytest.mark.asyncio
async def test_quarantined_structured_skill_cannot_execute():
    registry = SkillRegistry()
    registry.register(SkillContract(skill_id="$test.quarantined", title="Quarantined", stage="intake", purpose="test", validation_status="QUARANTINED"), lambda context: {"ok": True})
    result = await registry.execute(["$test.quarantined"], {})
    assert result[0].status == "blocked"
    assert result[0].error == "skill_not_active"


def test_customer_ai_runtime_has_no_astera_engine_dependency():
    root = Path(__file__).resolve().parents[1]
    checked = [root / "runtime", root / "v8", root / ".env.example", root / "scripts" / "provision_hf.py"]
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for base in checked
        for path in ([base] if base.is_file() else base.rglob("*"))
        if path.is_file() and path.suffix in {".py", ".mjs", ".example"}
    )
    assert "kagura-engine.js" not in text
    assert "CUSTOMER_AI_ASTERA_" not in text
    assert "AsteraBootstrap" not in text
