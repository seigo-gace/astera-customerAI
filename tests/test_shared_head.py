import json

import httpx
import pytest

from runtime.contracts import CapabilityCapsule
from runtime.hf_client import HFChatClient, HF_MODEL_4B, HF_MODEL_8B
from runtime.schemas import GroundedFact, NeedTask, RoleName, SharedRolePacket
from runtime.shared_head import ThreeRoleModelPool


@pytest.mark.asyncio
async def test_three_role_pool_routes_4b_4b_8b_in_two_waves():
    seen = []

    async def handler(request):
        payload = json.loads(request.content)
        user = json.loads(payload["messages"][1]["content"])
        seen.append((user["role"], payload["model"], user["constructive_draft"]))
        role = user["role"]
        if role in {"adversarial", "evidence_bound"}:
            assert user["constructive_draft"]["task_resolutions"][0]["public_text"] == "ok"
        body = {
            "role": role,
            "evidence_ids": ["f1"],
            "task_resolutions": (
                [{"task_id": "t1", "public_text": "ok", "evidence_ids": ["f1"]}]
                if role == "constructive"
                else []
            ),
            "completion_state": "complete",
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(body)}}]})

    transport = httpx.MockTransport(handler)
    shared_http = httpx.AsyncClient(transport=transport)
    pool = ThreeRoleModelPool(
        {
            RoleName.CONSTRUCTIVE: HFChatClient(token="", model_id=HF_MODEL_4B, client=shared_http),
            RoleName.ADVERSARIAL: HFChatClient(token="", model_id=HF_MODEL_4B, client=shared_http),
            RoleName.EVIDENCE_BOUND: HFChatClient(token="", model_id=HF_MODEL_8B, client=shared_http),
        }
    )
    packet = SharedRolePacket(
        request_id="r",
        session_id="s",
        turn_id="t",
        user_message="raw",
        normalized_need="need",
        audience="general",
        tasks=[
            NeedTask(
                task_id="t1",
                text="q",
                intent="general",
                required_facts=["evidence_required"],
                completion_condition="done",
            )
        ],
        facts=[GroundedFact(fact_id="f1", value="v", source_id="s", authority="canonical")],
    )
    skills = [
        CapabilityCapsule(skill_id="write", text="clear", score=50, capabilities=["clarity"]),
        CapabilityCapsule(skill_id="ev", text="evidence", score=40, capabilities=["evidence"]),
    ]
    out = await pool.run_all(packet, skills)
    assert {x.role for x in out} == set(RoleName)
    assert seen[0][:2] == ("constructive", HF_MODEL_4B)
    assert {entry[:2] for entry in seen[1:]} == {
        ("adversarial", HF_MODEL_4B),
        ("evidence_bound", HF_MODEL_8B),
    }
    assert seen[0][2] is None
    await shared_http.aclose()
