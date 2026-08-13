import json
import httpx
import pytest
from runtime.contracts import CapabilityCapsule
from runtime.hf_client import HFChatClient
from runtime.schemas import NeedTask,RoleName,SharedRolePacket,GroundedFact
from runtime.shared_head import SharedHeadRolePool

@pytest.mark.asyncio
async def test_shared_head_uses_two_wave_dialogue_and_one_model():
    seen=[]
    async def handler(request):
        payload=json.loads(request.content); user=json.loads(payload["messages"][1]["content"]); seen.append(user)
        role=user["role"]
        if role in {"adversarial","evidence_bound"}:
            assert user["constructive_draft"]["task_resolutions"][0]["public_text"]=="ok"
        body={"role":role,"evidence_ids":["f1"],"task_resolutions":([{"task_id":"t1","public_text":"ok","evidence_ids":["f1"]}] if role=="constructive" else []),"completion_state":"complete"}
        return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps(body)}}]})
    transport=httpx.MockTransport(handler); async_client=httpx.AsyncClient(transport=transport)
    client=HFChatClient(token="",client=async_client); pool=SharedHeadRolePool(client)
    packet=SharedRolePacket(request_id="r",session_id="s",turn_id="t",user_message="raw",normalized_need="need",audience="general",tasks=[NeedTask(task_id="t1",text="q",intent="general",required_facts=["evidence_required"],completion_condition="done")],facts=[GroundedFact(fact_id="f1",value="v",source_id="s",authority="canonical")])
    skills=[CapabilityCapsule(skill_id="write",text="clear",score=50,capabilities=["clarity"]),CapabilityCapsule(skill_id="ev",text="evidence",score=40,capabilities=["evidence"])]
    out=await pool.run_all(packet,skills)
    assert {x.role for x in out}==set(RoleName)
    assert len(seen)==3 and seen[0]["role"]=="constructive" and {x["role"] for x in seen[1:]}=={"adversarial","evidence_bound"}
    assert "clear" in seen[0]["capabilities"]
    assert "clear" in next(x for x in seen if x["role"]=="adversarial")["capabilities"]
    assert "evidence" in next(x for x in seen if x["role"]=="evidence_bound")["capabilities"]
    assert all("required_output_schema" not in json.dumps(x) for x in seen)
    await async_client.aclose()
