import pytest
from runtime.schemas import ResolutionMode
@pytest.mark.asyncio
async def test_story_resolves_without_unnecessary_clarification(runtime_parts):
    work,_,_,_=runtime_parts; result=await work.run("story","料金を教えて"); assert result.passed; assert result.resolution_mode==ResolutionMode.RESOLVED; assert result.clarification_questions==[]; assert result.unresolved_task_ids==[]
@pytest.mark.asyncio
async def test_targeted_retry_repairs_only_failed_path(runtime_parts):
    work,canonical,live,state=runtime_parts; state["repair_first"]=True; result=await work.run("repair","複合質問"); assert result.passed; assert state["constructive"]==2; assert canonical.calls==1 and live.calls==1
