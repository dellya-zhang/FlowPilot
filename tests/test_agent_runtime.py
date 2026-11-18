import csv
import io
import json
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app, lifespan
from app.agents.support import support_graph
from app.agents.materialize import delivery_graph
from app.services.versions import digest
from types import SimpleNamespace
from fastapi import HTTPException

@pytest.mark.asyncio
async def test_read_graph_and_confirmation():
    state=await support_graph.ainvoke({"content":"退款政策是什么","trace":[]})
    assert state["citations"] and state["tools"]==["policy_search"]
    assert not state.get("pending_next")
    assert "decision_rules" not in [x["node"] for x in state["trace"]]
    state=await support_graph.ainvoke({"content":"我要退款","trace":[]})
    assert state["pending_next"]["type"]=="refund"
    assert state["rule"]["action"]=="confirm_with_user"

@pytest.mark.asyncio
async def test_delivery_integrity_and_consent():
    snapshot={"title":"服务说明","message":"介绍会员权益","candidate_id":"a","revision":1}
    binding={"snapshot":snapshot,"snapshot_sha256":digest(snapshot),"candidate_id":"a","revision":1}
    row=SimpleNamespace(id=1,segment="new",region="north",consent=True)
    result=await delivery_graph.ainvoke({"binding":binding,"customers":[row],"budget":100,"kind":"pilot"})
    assert result["trace"][-2:]==["terminal_compliance","assemble_csv"]
    with pytest.raises(HTTPException):
        await delivery_graph.ainvoke({"binding":binding,"customers":[row,row],"budget":100,"kind":"full"})
    with pytest.raises(HTTPException):
        await delivery_graph.ainvoke({"binding":{**binding,"snapshot_sha256":"bad"},"customers":[row],"budget":100,"kind":"full"})

@pytest.mark.asyncio
async def test_support_audit_persistence():
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as c:
            sid=(await c.post("/api/v1/support/sessions")).json()["session_id"]
            path=f"/api/v1/support/sessions/{sid}"
            assert (await c.post(path+"/messages",json={"content":"退款政策是什么"})).json()["citations"]
            assert (await c.post(path+"/messages",json={"content":"我要退款"})).json()["needs_confirmation"]
            assert (await c.post(path+"/confirm?approved=false")).status_code==200
            rows=(await c.get(path+"/audit")).json()
            assert len(rows)==3 and rows[0]["decision"]["trace"] and rows[-1]["decision"]["confirmation"]=="cancel"

@pytest.mark.asyncio
async def test_ab_pilot_csv():
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as c:
            cid=(await c.post("/api/v1/campaigns",json={"brief":"会员服务权益介绍","budget":2000})).json()["id"]
            path=f"/api/v1/campaigns/{cid}"
            assert (await c.post(path+"/generate")).status_code==202
            await __import__("asyncio").gather(*list(app.state.tasks))
            ids=[x["id"] for x in (await c.get(path+"/candidates")).json()[:2]]
            exp=await c.post("/api/v1/experiments",json={"campaign_id":cid,"candidate_ids":ids})
            assert exp.status_code==201,exp.text
            route="/api/v1/experiments/"+exp.json()["id"]
            lists=[]
            for x in ids:
                response=await c.get(route+"/arms/"+x+"/download")
                assert response.status_code==200 and response.headers["X-Content-SHA256"]
                lists.append({int(r["customer_id"]) for r in csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")))})
            assert lists[0] and lists[1] and lists[0].isdisjoint(lists[1])


@pytest.mark.asyncio
async def test_support_stream_returns_nodes_then_committed_result():
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            root = "/api/v1/support/sessions"
            sid = (await client.post(root + "?member_id=1")).json()["session_id"]
            path = f"{root}/{sid}/messages"
            response = await client.post(path + "/stream", json={"content": "帮我查订单"})
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            frames = [
                (event.removeprefix("event: "), json.loads(data.removeprefix("data: ")))
                for event, data in (frame.splitlines() for frame in response.text.strip().split("\n\n"))
            ]
            assert frames[0] == ("received", {"session_id": sid})
            assert [payload["node"] for name, payload in frames if name == "node"] == [
                "intent_screening", "retrieve", "compose_reply", "compliance_guard",
            ]
            name, result = frames[-1]
            assert name == "complete" and "FP-0001-A" in result["reply"] and not result["needs_confirmation"]
            history = (await client.get(path)).json()
            assert history[-1] == {"role": "assistant", "content": result["reply"]}
            missing = await client.post(f"{root}/missing/messages/stream", json={"content": "我的订单"})
            assert missing.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("number", ["00000020000101001X", "0000000000000001"])
async def test_terminal_compliance_blocks_sensitive_number_patterns(number):
    snapshot = {"title": "服务说明", "message": f"请核验 {number}", "candidate_id": "a", "revision": 1}
    binding = {"snapshot": snapshot, "snapshot_sha256": digest(snapshot), "candidate_id": "a", "revision": 1}
    with pytest.raises(HTTPException) as blocked:
        await delivery_graph.ainvoke({"binding": binding, "customers": [], "budget": 100, "kind": "full"})
    assert blocked.value.status_code == 422
    assert blocked.value.detail["issues"] == ["possible_personal_information"]
