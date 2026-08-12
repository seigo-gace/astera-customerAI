from __future__ import annotations
import pytest
from runtime.bootstrap import RuntimeDependencies, build_work
from runtime.schemas import GroundedFact, RoleName, RoleResult, TaskResolution
class FakeV8:
    async def analyze_need(self,message,context): return {"normalized_need":message,"audience":"general","tasks":[{"task_id":"t1","text":message,"intent":"general","required_facts":["f1"],"completion_condition":"answer t1","priority":"primary","response_shape":"direct","actionability_required":False}]}
    async def compare_results(self,packet,results): return {"violations":[]}
class FakeKagrra:
    async def preprocess(self,message,context): return {"intent":"general"}
    async def audit(self,packet,results,integrated): return {"violations":[]}
class FakeCanonical:
    def __init__(self): self.calls=0
    async def find_for_tasks(self,tasks): self.calls+=1; return [GroundedFact(fact_id="f1",value="確認済み回答",source_id="canon",authority="canonical")]
class FakeLive:
    def __init__(self): self.calls=0; self.facts=[]
    async def current_facts(self,tasks): self.calls+=1; return list(self.facts)
class FakeRoleBackend:
    def __init__(self,role,state=None): self.role=role; self.state=state if state is not None else {}
    async def generate_role(self,role,packet):
        assert role==self.role
        if role==RoleName.CONSTRUCTIVE:
            count=self.state.get("constructive",0); self.state["constructive"]=count+1
            if self.state.get("repair_first") and count==0: return RoleResult(role=role,missing_needs=["t1"],task_resolutions=[TaskResolution(task_id="t1",unresolved_reason="repair_required")],completion_state="partial")
            return RoleResult(role=role,claims=["確認済み回答"],evidence_ids=["f1"],task_resolutions=[TaskResolution(task_id="t1",public_text="確認済み回答",evidence_ids=["f1"])],completion_state="complete")
        return RoleResult(role=role,evidence_ids=["f1"],completion_state="complete")
@pytest.fixture
def runtime_parts():
    canonical=FakeCanonical(); live=FakeLive(); state={}
    deps=RuntimeDependencies(v8_adapter=FakeV8(),kagrra_adapter=FakeKagrra(),canonical_store=canonical,live_state_provider=live,backend_factory=lambda role:FakeRoleBackend(role,state),japanese_alias_registry={"Astera":["アステラ","astera"]},japanese_fuzzy_threshold=90.0,max_targeted_retry=1)
    return build_work(deps),canonical,live,state
