import pytest
@pytest.mark.asyncio
async def test_three_roles_share_one_grounding_read(runtime_parts):
    work,canonical,live,_=runtime_parts; result=await work.run("s1","Asteraとは？"); assert result.passed; assert result.answer=="確認済み回答"; assert canonical.calls==1; assert live.calls==1; assert result.request_id.startswith("req_")
