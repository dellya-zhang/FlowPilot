import pytest
from app.agents.marketing import marketing_graph

@pytest.mark.asyncio
async def test_compliance_retry(monkeypatch):
    calls=[]
    async def fake(brief,*args,**kwargs):
        calls.append(brief)
        return {"outcome":"draft","title":"会员权益","message":"保证收益" if len(calls)==1 else "欢迎查询会员积分","reasoning":"已知权益"}
    monkeypatch.setattr("app.agents.marketing.draft",fake)
    result=await marketing_graph.ainvoke({"brief":"介绍会员积分","audience":"new","budget":1000,"variant":1,"audience_count":5,"holdout_count":0})
    assert len(calls)==2 and "prohibited_claim" in calls[1]
    assert result["compliance_retry"]==1 and result["plan_data"]["compliance"]["decision"]=="PASS"

@pytest.mark.asyncio
async def test_compliance_retry_budget(monkeypatch):
    calls=[]
    async def fake(*args,**kwargs):
        calls.append(1)
        return {"outcome":"draft","title":"会员权益","message":"保证收益","reasoning":"已知权益"}
    monkeypatch.setattr("app.agents.marketing.draft",fake)
    with pytest.raises(ValueError,match="Content blocked"):
        await marketing_graph.ainvoke({"brief":"介绍会员积分","audience":"new","budget":1000,"variant":1,"audience_count":5,"holdout_count":0})
    assert len(calls)==3
