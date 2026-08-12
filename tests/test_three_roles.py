import pytest
from runtime.model import ResidentRolePool
from runtime.schemas import NeedTask,RoleName,RoleResult,SharedRolePacket
class Backend:
    def __init__(self,role): self.role=role
    async def generate_role(self,role,packet): return RoleResult(role=role,completion_state="complete")
def packet(): return SharedRolePacket(request_id="r",session_id="s",turn_id="t",user_message="q",normalized_need="q",audience="general",tasks=[NeedTask(task_id="t1",text="q",intent="general",completion_condition="done")])
@pytest.mark.asyncio
async def test_all_three_roles_are_resident_and_returned(): assert {i.role for i in await ResidentRolePool(lambda role:Backend(role)).run_all(packet())}==set(RoleName)
@pytest.mark.asyncio
async def test_role_mismatch_is_rejected():
    class Wrong(Backend):
        async def generate_role(self,role,packet): return RoleResult(role=RoleName.ADVERSARIAL)
    with pytest.raises(ValueError): await ResidentRolePool(lambda role:Wrong(role)).retry_role(RoleName.CONSTRUCTIVE,packet())
