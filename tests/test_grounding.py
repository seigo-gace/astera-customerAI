import pytest
from runtime.knowledge import GroundingConflictError,GroundingPlanner
from runtime.schemas import GroundedFact,NeedTask
class Store:
    def __init__(self,facts): self.facts=facts
    async def find_for_tasks(self,tasks): return list(self.facts)
    async def current_facts(self,tasks): return list(self.facts)
TASK=[NeedTask(task_id="t1",text="q",intent="x",completion_condition="done")]
@pytest.mark.asyncio
async def test_live_overrides_canonical():
    facts=await GroundingPlanner(Store([GroundedFact(fact_id="f",value="old",source_id="c",authority="canonical")]),Store([GroundedFact(fact_id="f",value="new",source_id="l",authority="live")])).build_shared_facts(TASK); assert facts[0].value=="new"
@pytest.mark.asyncio
async def test_same_authority_conflict_fails():
    with pytest.raises(GroundingConflictError): await GroundingPlanner(Store([GroundedFact(fact_id="f",value="a",source_id="c1",authority="canonical"),GroundedFact(fact_id="f",value="b",source_id="c2",authority="canonical")]),Store([])).build_shared_facts(TASK)
@pytest.mark.asyncio
async def test_private_legacy_undecided_are_filtered():
    facts=await GroundingPlanner(Store([GroundedFact(fact_id="a",value="1",source_id="x",authority="canonical",public=False),GroundedFact(fact_id="b",value="1",source_id="x",authority="canonical",legacy=True),GroundedFact(fact_id="c",value="1",source_id="x",authority="canonical",undecided=True),GroundedFact(fact_id="d",value="ok",source_id="x",authority="canonical")]),Store([])).build_shared_facts(TASK); assert [i.fact_id for i in facts]==["d"]
