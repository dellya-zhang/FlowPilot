import json
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app,lifespan
from app.db.base import SessionLocal
from app.services.retail import run_tool
from app.agents.retail_tools import run_retail_query

@pytest.mark.asyncio
async def test_retail_tools_and_ticket():
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as c:
            root="/api/v1/support/sessions"
            sid=(await c.post(root+"?member_id=1")).json()["session_id"]
            path=root+"/"+sid
            r=await c.post(path+"/messages",json={"content":"帮我查订单"})
            assert "FP-0001-A" in r.json()["reply"] and "FP-0002" not in r.json()["reply"]
            assert not r.json()["needs_confirmation"]
            r=await c.post(path+"/messages",json={"content":"我要退款 FP-0002-A"})
            assert not r.json()["needs_confirmation"]
            r=await c.post(path+"/messages",json={"content":"我要退款 FP-0001-A"})
            assert r.json()["needs_confirmation"]
            assert (await c.post(path+"/confirm?approved=true")).status_code==200
            assert (await c.post(path+"/confirm?approved=true")).status_code==409
            tickets=(await c.get("/api/v1/retail/sessions/"+sid+"/tickets")).json()
            assert len(tickets)==1 and tickets[0]["status"]=="PENDING_REVIEW"

@pytest.mark.asyncio
async def test_native_tool_round_trip(monkeypatch):
    calls=[]
    async def model(messages,tools):
        calls.append(messages.copy())
        msg={"role":"assistant","content":None}
        if len(calls)==1:msg["tool_calls"]=[{"id":"one","type":"function","function":{"name":"list_orders","arguments":"{}"}}]
        return {"message":msg,"call_id":str(len(calls)),"response_id":"fixture","model":"test"}
    monkeypatch.setattr("app.agents.retail_tools.complete_tools",model)
    async with lifespan(app):
        async with SessionLocal() as db:
            result=await run_retail_query(db,1,"我的订单",True)
            assert result["mode"]=="model_tools" and "FP-0001-A" in result["reply"]
            assert calls[1][-1]["role"]=="tool" and calls[1][-1]["tool_call_id"]=="one"
            assert (await run_tool(db,1,"query_logistics",{"order_id":"FP-0002-A"}))["error"]
            with pytest.raises(ValueError):await run_tool(db,1,"list_orders",{"customer_id":2})

@pytest.mark.asyncio
async def test_retail_population_filter():
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as c:
            r=await c.post("/api/v1/campaigns",json={"brief":"沉睡会员召回活动","audience":"dormant","budget":3000,
                "planning":{"known_benefits":"积分查询","min_days_since_purchase":30,"min_lifetime_spend_cny":500}})
            assert r.status_code==201
            cid=r.json()["id"]
            assert (await c.post("/api/v1/campaigns/"+cid+"/generate")).status_code==202
            await __import__("asyncio").gather(*list(app.state.tasks))
            candidates=(await c.get("/api/v1/campaigns/"+cid+"/candidates")).json()
            assert candidates and 0<candidates[0]["plan_data"]["segment"]["treatment_size"]<167
