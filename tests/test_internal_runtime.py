import pytest
from runtime.bootstrap import RuntimeDependencies, build_work
from runtime.schemas import GroundedFact, RoleName, RoleResult, TaskResolution

class Canonical:
    def __init__(self): self.calls=0
    async def find_for_tasks(self,tasks): self.calls+=1; return [GroundedFact(fact_id="f1",value="確認済み回答",source_id="canon",source_ids=["canon"],authority="canonical")]
class Live:
    def __init__(self): self.calls=0
    async def current_facts(self,tasks): self.calls+=1; return []
class Head:
    def __init__(self): self.calls=[]
    async def run_all(self,packet,skills):
        self.calls.append(("all",len(packet.facts),tuple(s.skill_id for s in skills)))
        return [
            RoleResult(role=RoleName.CONSTRUCTIVE,evidence_ids=["f1"],task_resolutions=[TaskResolution(task_id="t1",public_text="確認済み回答",evidence_ids=["f1"])],completion_state="complete"),
            RoleResult(role=RoleName.ADVERSARIAL,evidence_ids=["f1"],completion_state="complete"),
            RoleResult(role=RoleName.EVIDENCE_BOUND,evidence_ids=["f1"],completion_state="complete"),
        ]
    async def validate_draft(self,packet,skills,draft): raise AssertionError("repair_not_expected")
    async def retry_role(self,role,packet,skills): raise AssertionError("retry_not_expected")

@pytest.mark.asyncio
async def test_internal_runtime_uses_single_grounding_and_shared_head():
    canonical=Canonical(); live=Live(); head=Head()
    work=build_work(RuntimeDependencies(canonical_store=canonical,live_state_provider=live,japanese_alias_registry={"Astera":[]},japanese_fuzzy_threshold=90,shared_head=head))
    result=await work.run("s1","Asteraとは？")
    assert result.passed and result.answer=="確認済み回答"
    assert canonical.calls==1 and live.calls==1
    assert head.calls[0][0]=="all" and head.calls[0][1]==1
    assert len(head.calls[0][2])<=8
