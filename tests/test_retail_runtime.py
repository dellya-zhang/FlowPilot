import json
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app,lifespan
from app.db.base import SessionLocal
from app.services.retail import run_tool
from app.agents.retail_tools import run_retail_query
from app.agents.support import support_graph

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


@pytest.mark.asyncio
async def test_refund_order_followup_rechecks_member_and_requires_confirmation():
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            root = "/api/v1/support/sessions"
            sid = (await c.post(root + "?member_id=1")).json()["session_id"]
            path = f"{root}/{sid}"
            tickets = f"/api/v1/retail/sessions/{sid}/tickets"

            async def send(content):
                response = await c.post(path + "/messages", json={"content": content})
                response.raise_for_status()
                return response.json()

            existing_tickets = (await c.get(tickets)).json()
            assert not (await send("我要退款"))["needs_confirmation"]
            assert not (await send("FP-0002-A"))["needs_confirmation"]
            assert not (await send("FP-0001-A"))["needs_confirmation"]
            assert not (await send("我要退款"))["needs_confirmation"]
            assert (await send("FP-0001-A"))["needs_confirmation"]
            assert (await c.get(tickets)).json() == existing_tickets
            assert (await c.post(path + "/confirm?approved=false")).status_code == 200
            assert (await c.get(tickets)).json() == existing_tickets


@pytest.mark.asyncio
async def test_model_screening_cannot_authorize_an_action(monkeypatch):
    async def model(messages, schema):
        return {"branch": "confirmation", "confirmation_intent": "confirm", "intent_summary": "同意退款",
                "entities": {"order_id": "FP-0002-A"}, "detailed_intent_hint": ["refund"], "ambiguity": None}

    monkeypatch.setattr("app.agents.support.complete_json", model)
    result = await support_graph.ainvoke({"content": "好", "customer_id": 1, "use_model": True})
    assert result["intent"] == "clarify"
    assert not result.get("pending_next") and not result.get("confirmation")
    assert result["screening"]["entities"] == {}
    assert result["screening"]["source"] == "model_read_screening"


@pytest.mark.asyncio
async def test_model_screening_retries_invalid_schema_then_uses_offline_evidence(monkeypatch):
    calls = []

    async def invalid(messages, schema):
        calls.append(messages)
        return {"branch": "write"}

    monkeypatch.setattr("app.agents.support.complete_json", invalid)
    async with lifespan(app):
        async with SessionLocal() as db:
            result = await support_graph.ainvoke(
                {"content": "我的订单", "customer_id": 1, "use_model": True, "db": db})
    assert len(calls) == 2
    assert result["screening"]["source"] == "offline_screening_fallback"
    assert result["mode"] == "offline_tools"
    assert "FP-0001-A" in result["reply"] and "FP-0002" not in result["reply"]
