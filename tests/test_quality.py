from runtime.integration import IntegratedRoleResult
from runtime.quality import CompletionGate
from runtime.schemas import NeedTask,SharedRolePacket,TaskResolution
def packet(actionable=False): return SharedRolePacket(request_id="r",session_id="s",turn_id="t",user_message="q",normalized_need="q",audience="general",tasks=[NeedTask(task_id="t1",text="q",intent="x",required_facts=["f1"],completion_condition="done",actionability_required=actionable)])
def test_complete_resolution_passes():
    i=IntegratedRoleResult((TaskResolution(task_id="t1",public_text="ok",evidence_ids=["f1"]),),(),(),(),(),("f1",)); assert CompletionGate().evaluate(packet(),i).passed
def test_missing_evidence_fails():
    i=IntegratedRoleResult((TaskResolution(task_id="t1",public_text="ok"),),(),(),(),(),()); r=CompletionGate().evaluate(packet(),i); assert not r.passed and "evidence_incomplete" in r.violations
