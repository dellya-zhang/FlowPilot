import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from app.main import app, lifespan  # noqa: E402
from app.services.rules import evaluate  # noqa: E402


@pytest.mark.asyncio
async def test_marketing_full_flow():
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            result = await client.post("/api/v1/campaigns", json={
                "brief": "为新用户介绍会员服务权益", "audience": "new", "budget": 5000})
            assert result.status_code == 201, result.text
            cid = result.json()["id"]
            assert (await client.post(f"/api/v1/campaigns/{cid}/generate")).status_code == 202
            # Wait for explicitly launched task; avoid arbitrary sleeps.
            if app.state.tasks:
                await __import__("asyncio").gather(*list(app.state.tasks))
            info = (await client.get(f"/api/v1/campaigns/{cid}")).json()
            assert info["status"] == "WAITING_APPROVAL"
            candidates = (await client.get(f"/api/v1/campaigns/{cid}/candidates")).json()
            assert len(candidates) == 3
            assert (await client.post(f"/api/v1/campaigns/{cid}/approve", json={
                "candidate_id": candidates[0]["id"]})).status_code == 200
            pilot = await client.post(f"/api/v1/campaigns/{cid}/pilot")
            assert pilot.status_code == 200, pilot.text
            assert pilot.json()["row_count"] <= 25
            # Repeated pilot does not create duplicates.
            again = await client.post(f"/api/v1/campaigns/{cid}/pilot")
            assert again.json()["id"] == pilot.json()["id"]
            full = await client.post(f"/api/v1/campaigns/{cid}/finalize")
            assert full.status_code == 200, full.text
            assert full.json()["row_count"] > 0
            full_again = await client.post(f"/api/v1/campaigns/{cid}/finalize")
            assert full_again.json()["id"] == full.json()["id"]
            a = await client.get(f"/api/v1/artifacts/{pilot.json()['id']}/download")
            b = await client.get(f"/api/v1/artifacts/{full.json()['id']}/download")
            import csv, io
            ps = {r["customer_id"] for r in csv.DictReader(io.StringIO(a.content.decode("utf-8-sig")))}
            fs = {r["customer_id"] for r in csv.DictReader(io.StringIO(b.content.decode("utf-8-sig")))}
            assert ps.isdisjoint(fs)
            events = (await client.get(f"/api/v1/campaigns/{cid}/events")).json()
            assert any(e["name"] == "generation.finished" for e in events)


@pytest.mark.asyncio
async def test_support_confirmation():
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            sid = (await client.post("/api/v1/support/sessions")).json()["session_id"]
            msg = await client.post(f"/api/v1/support/sessions/{sid}/messages", json={"content":"我想退款"})
            assert msg.json()["needs_confirmation"] is True
            confirmed = await client.post(f"/api/v1/support/sessions/{sid}/confirm?approved=true")
            assert confirmed.json()["session_status"] == "ESCALATED"
            assert (await client.post(f"/api/v1/support/sessions/{sid}/confirm?approved=true")).status_code == 409


def test_rule_dsl():
    context = {"amount": 100, "country": "CN"}
    assert evaluate({"field":"amount","op":"gte","value":100}, context)
    assert evaluate({"field":"country","op":"in","value":["CN","KR"]}, context)
    assert not evaluate({"field":"amount","op":"exec","value":"bad"}, context)

@pytest.mark.asyncio
async def test_concurrency_and_experiment():
    import asyncio, csv, io
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            root='/api/v1/campaigns'
            cid=(await c.post(root,json={'brief':'欢迎新会员并介绍服务权益','budget':2000})).json()['id']
            path=f'{root}/{cid}'
            rs=await asyncio.gather(*(c.post(path+'/generate') for _ in range(5)))
            assert sorted(r.status_code for r in rs)==[202,409,409,409,409]
            await asyncio.gather(*list(app.state.tasks))
            ids=[x['id'] for x in (await c.get(path+'/candidates')).json()[:2]]
            res=await c.post('/api/v1/experiments',json={'campaign_id':cid,'candidate_ids':ids})
            assert res.status_code==201,res.text
            exp=res.json(); eid=exp['id']; arms=exp['arms']; ep='/api/v1/experiments/'+eid
            assert set(arms[ids[0]]).isdisjoint(arms[ids[1]])
            event={'event_id':eid+'event','candidate_id':ids[0],'customer_id':arms[ids[0]][0]}
            assert not (await c.post(ep+'/feedback',json=event)).json()['duplicate']
            assert (await c.post(ep+'/feedback',json=event)).json()['duplicate']
            assert (await c.post(ep+'/feedback',json={**event,'candidate_id':ids[1]})).status_code==422
            assert (await c.get(ep)).json()['arms'][0]['conversions']==1
            assert (await c.post(ep+'/promote',params={'candidate_id':ids[0]})).status_code==200
            rs=await asyncio.gather(*(c.post(path+'/finalize') for _ in range(5)))
            assert all(r.status_code==200 for r in rs),[r.text for r in rs]
            assert len({r.json()['id'] for r in rs})==1
            blob=(await c.get('/api/v1/artifacts/'+rs[0].json()['id']+'/download')).content
            users={int(r['customer_id']) for r in csv.DictReader(io.StringIO(blob.decode('utf-8-sig')))}
            assert users.isdisjoint(set(arms[ids[0]])|set(arms[ids[1]]))


@pytest.mark.asyncio
async def test_support_policy_and_history():
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            sid=(await c.post('/api/v1/support/sessions')).json()['session_id']
            path=f'/api/v1/support/sessions/{sid}'
            policy=(await c.post(path+'/messages',json={'content':'退款政策是什么'})).json()
            assert not policy['needs_confirmation']
            assert policy['facts'][0]['source']=='refund_policy'
            await c.post(path+'/messages',json={'content':'我要退款'})
            assert (await c.post(path+'/messages',json={'content':'查订单'})).json()['needs_confirmation']
            assert (await c.post(path+'/messages',json={'content':'确认'})).json()['session_status']=='ESCALATED'
            assert len((await c.get(path+'/messages')).json())==8


@pytest.mark.parametrize('op,value,expected',[('eq',100,True),('ne',100,False),('gt',99,True),('gte',100,True),('lt',101,True),('lte',100,True),('in',[100],True),('unknown',0,False)])
def test_operators(op,value,expected):
    assert evaluate({'field':'amount','op':op,'value':value},{'amount':100}) is expected


def test_malformed_rule():
    assert not evaluate({}, {})
    assert not evaluate({'field':'x','op':'gt','value':4},{'x':'string'})

@pytest.mark.asyncio
async def test_partial_and_total_failure(monkeypatch):
    import asyncio
    from app.agents.marketing import marketing_graph
    original=marketing_graph.ainvoke
    async def partial(state):
        if state['variant']==2:
            raise ValueError('simulated provider failure')
        return await original(state)
    async def broken(state):
        raise ValueError('simulated total failure')
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            for implementation, expected, count in [(partial,'WAITING_APPROVAL',2),(broken,'FAILED',0)]:
                monkeypatch.setattr(marketing_graph,'ainvoke',implementation)
                cid=(await c.post('/api/v1/campaigns',json={'brief':'会员权益活动失败隔离验证','budget':2000})).json()['id']
                path=f'/api/v1/campaigns/{cid}'
                await c.post(path+'/generate')
                await asyncio.gather(*list(app.state.tasks))
                assert (await c.get(path)).json()['status']==expected
                assert len((await c.get(path+'/candidates')).json())==count


@pytest.mark.asyncio
async def test_auth_and_input_validation(monkeypatch):
    from app.core.config import get_settings
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            monkeypatch.setattr(get_settings(),'access_token','test-secret')
            assert (await c.get('/api/v1/campaigns')).status_code==401
            assert (await c.get('/api/v1/health')).status_code==200
            c.headers['Authorization']='Bearer test-secret'
            r=await c.get('/api/v1/campaigns')
            assert r.status_code==200
            assert r.headers['X-Request-ID']
            assert (await c.post('/api/v1/campaigns',json={'brief':'x','budget':-1})).status_code==422
            assert (await c.get('/api/v1/campaigns/nonexistent')).status_code==404

@pytest.mark.asyncio
async def test_recovery_and_event_cursor():
    import asyncio
    from app.db.base import SessionLocal
    from app.models.entities import Campaign
    # Simulate persisted GENERATING state left by a prior process.
    async with lifespan(app):
        async with SessionLocal() as db:
            campaign=Campaign(brief='中断后恢复的会员活动',audience='all',budget=2000,status='GENERATING')
            db.add(campaign);await db.commit();cid=campaign.id
    async with lifespan(app):
        await asyncio.gather(*list(app.state.tasks))
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            path=f'/api/v1/campaigns/{cid}'
            assert (await c.get(path)).json()['status']=='WAITING_APPROVAL'
            assert len((await c.get(path+'/candidates')).json())==3
            events=(await c.get(path+'/events')).json()
            cursor=events[1]['id']
            replay=(await c.get(path+'/events',params={'after':cursor})).json()
            assert replay and all(e['id']>cursor for e in replay)
            assert (await c.post(path+'/approve',json={'candidate_id':'foreign'})).status_code==422
            assert (await c.post(path+'/finalize')).status_code==409

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['success','truncated','schema','http429','timeout'])
async def test_provider_contract_and_budget(monkeypatch,tmp_path,mode):
    import json,httpx
    from app.agents.provider import draft,BudgetExceeded
    from app.core.config import get_settings
    cfg=get_settings()
    for key,value in {'ai_provider':'deepseek','deepseek_api_key':'test-dummy-key',
                      'llm_audit_path':str(tmp_path/'audit.jsonl'),'llm_max_calls':1}.items():
        monkeypatch.setattr(cfg,key,value)
    def handle(request):
        body=json.loads(request.content)
        assert body['max_tokens']==600
        assert body['thinking']=={'type':'disabled'}
        if mode=='timeout':raise httpx.ReadTimeout('simulated')
        if mode=='http429':return httpx.Response(429,json={'error':{'message':'private upstream body'}})
        value={'title':'标题','message':'合法会员权益','reasoning':'已知事实'}
        if mode=='schema':value['extra']='unexpected'
        return httpx.Response(200,json={'choices':[{'finish_reason':'length' if mode=='truncated' else 'stop',
            'message':{'content':json.dumps(value)}}],'usage':{'prompt_tokens':100,'completion_tokens':50,'total_tokens':150}})
    original=httpx.AsyncClient
    monkeypatch.setattr('app.agents.provider.httpx.AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handle),**kw))
    if mode=='success':
        assert (await draft('会员活动','all',1))['title']=='标题'
    else:
        with pytest.raises(Exception):await draft('会员活动','all',1)
    with pytest.raises(BudgetExceeded):await draft('不能再调用','all',2)
    audit=(tmp_path/'audit.jsonl').read_text('utf-8')
    assert 'test-dummy-key' not in audit and '会员活动' not in audit
    assert len(audit.splitlines())==2

@pytest.mark.asyncio
async def test_budget_preflight_and_title_policy(monkeypatch):
    import asyncio
    async def bad_title(state):
        return {'title':'保证收益','message':'欢迎了解会员权益','reasoning':'test','score':70}
    from app.agents.marketing import marketing_graph
    monkeypatch.setattr(marketing_graph,'ainvoke',bad_title)
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            assert (await c.post('/api/v1/campaigns',json={'brief':'预算超限前置检查','budget':100001})).status_code==422
            cid=(await c.post('/api/v1/campaigns',json={'brief':'标题违规不应入库','budget':2000})).json()['id']
            path=f'/api/v1/campaigns/{cid}'
            await c.post(path+'/generate')
            await asyncio.gather(*list(app.state.tasks))
            assert (await c.get(path)).json()['status']=='FAILED'
            assert (await c.get(path+'/candidates')).json()==[]


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["我不想退款", "不要退款", "退款怎么申请", "退款", "如何申请退款"])
async def test_support_non_actionable_refund(content):
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            sid = (await client.post("/api/v1/support/sessions")).json()["session_id"]
            response = await client.post(f"/api/v1/support/sessions/{sid}/messages", json={"content": content})
            assert response.status_code == 200
            assert response.json()["needs_confirmation"] is False
            assert (await client.post(f"/api/v1/support/sessions/{sid}/confirm?approved=true")).status_code == 409
