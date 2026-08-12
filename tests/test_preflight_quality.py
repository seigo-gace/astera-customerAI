import pytest
from runtime.v8_bridge import V8Bridge
class Empty:
    async def analyze_need(self,message,context): return {"tasks":[]}
    async def compare_results(self,packet,results): return {}
@pytest.mark.asyncio
async def test_preflight_without_tasks_is_rejected():
    with pytest.raises(ValueError): await V8Bridge(Empty()).analyze_need("q",{})
