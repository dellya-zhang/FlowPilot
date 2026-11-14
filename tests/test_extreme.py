"""Adversarial invariants. No live provider requests, real faults in local dependencies."""
import asyncio
import hashlib
from pathlib import Path
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from app.main import app, lifespan
from app.db.base import SessionLocal
from app.models.entities import Artifact, Campaign
from app.core.config import get_settings


async def campaign(c, generated=True):
    row=(await c.post('/api/v1/campaigns',json={'brief':'介绍已知的会员积分服务','budget':1000})).json()
    path='/api/v1/campaigns/'+row['id']
    if generated:
        assert (await c.post(path+'/generate')).status_code==202
        await asyncio.gather(*list(app.state.tasks))
    return path


async def approve(c,path):
    cs=(await c.get(path+'/candidates')).json()
    assert (await c.post(path+'/approve',json={'candidate_id':cs[0]['id']})).status_code==200
    return cs


@pytest.mark.asyncio
@pytest.mark.parametrize('content',['     ','\n\t\r ','\u0000'*6])
async def test_meaningless_input(content):
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            r=await c.post('/api/v1/campaigns',json={'brief':content,'budget':1000})
            assert r.status_code==422


@pytest.mark.asyncio
async def test_storage_failure_rollback(monkeypatch):
    def fail(*args): raise OSError('injected disk full / denied')
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app,raise_app_exceptions=False),base_url='http://test') as c:
            path=await campaign(c);await approve(c,path)
            with monkeypatch.context() as m:
                m.setattr('app.services.campaigns.put_blob',fail)
                r=await c.post(path+'/pilot')
                assert r.status_code==503
            assert (await c.get(path)).json()['status']=='PILOT_READY'
            assert (await c.get(path+'/artifacts')).json()==[]
            assert (await c.post(path+'/pilot')).status_code==200


@pytest.mark.asyncio
async def test_corrupt_or_missing_artifact():
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app,raise_app_exceptions=False),base_url='http://test') as c:
            path=await campaign(c);await approve(c,path)
            art=(await c.post(path+'/pilot')).json()
            async with SessionLocal() as db: obj=await db.get(Artifact,art['id']);key=obj.key
            blob=Path(get_settings().artifact_dir)/key
            original=blob.read_bytes();assert hashlib.sha256(original).hexdigest()==art['sha256']
            blob.write_bytes(b'corrupt,customer,data')
            assert (await c.get('/api/v1/artifacts/'+art['id']+'/download')).status_code==503
            r=await c.post(path+'/finalize');assert r.status_code==503
            assert (await c.get(path)).json()['status']=='PILOT_EXPORTED'
            blob.unlink()
            assert (await c.get('/api/v1/artifacts/'+art['id']+'/download')).status_code==503
            blob.write_bytes(original)
            assert (await c.post(path+'/finalize')).status_code==200


@pytest.mark.asyncio
async def test_experiment_blocks_conflicting_route():
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            path=await campaign(c)
            cs=(await c.get(path+'/candidates')).json()
            exp=(await c.post('/api/v1/experiments',json={'campaign_id':path.rsplit('/',1)[1],
                 'candidate_ids':[x['id'] for x in cs[:2]]})).json()
            assert (await c.post(path+'/approve',json={'candidate_id':cs[2]['id']})).status_code==409
            assert (await c.post('/api/v1/experiments/'+exp['id']+'/promote',params={'candidate_id':cs[0]['id']})).status_code==200
            assert (await c.post(path+'/pilot')).status_code==409


@pytest.mark.asyncio
async def test_cancel_drains_candidates(monkeypatch):
    active=0; entered=asyncio.Event();release=asyncio.Event()
    async def blocked(state):
        nonlocal active
        active+=1;entered.set()
        try: await release.wait()
        finally: active-=1
    monkeypatch.setattr('app.services.campaigns.marketing_graph.ainvoke',blocked)
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            path=await campaign(c,False)
            await c.post(path+'/generate')
            await asyncio.wait_for(entered.wait(),2)
            owned=list(app.state.tasks)
            for t in owned:t.cancel()
            await asyncio.gather(*owned,return_exceptions=True)
            # Cancellation of the parent must await every child, not leave model calls running.
            try: assert active==0
            finally: release.set();await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_burst_generate_and_approval():
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            path=await campaign(c,False)
            rs=await asyncio.gather(*(c.post(path+'/generate') for _ in range(100)))
            assert sum(r.status_code==202 for r in rs)==1
            assert all(r.status_code in (202,409,429) for r in rs)
            await asyncio.gather(*list(app.state.tasks))
            cs=(await c.get(path+'/candidates')).json()
            rs=await asyncio.gather(*(c.post(path+'/approve',json={'candidate_id':cs[i%3]['id']}) for i in range(50)))
            assert sum(r.status_code==200 for r in rs)==1
            assert all(r.status_code in (200,409) for r in rs)


@pytest.mark.asyncio
async def test_competing_confirm_cancel():
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            sid=(await c.post('/api/v1/support/sessions')).json()['session_id'];path='/api/v1/support/sessions/'+sid
            await c.post(path+'/messages',json={'content':'我要退款'})
            rs=await asyncio.gather(*(c.post(path+'/confirm',params={'approved':bool(i%2)}) for i in range(50)))
            assert sum(r.status_code==200 for r in rs)==1
            assert all(r.status_code in (200,409) for r in rs)
            assert len((await c.get(path+'/messages')).json())==3

@pytest.mark.asyncio
async def test_admission_and_global_model_limit(monkeypatch):
    cfg=get_settings();monkeypatch.setattr(cfg,'max_active_campaigns',2);monkeypatch.setattr(cfg,'model_concurrency',2)
    active=0;peak=0;release=asyncio.Event()
    async def slow(state):
        nonlocal active,peak
        active+=1;peak=max(active,peak)
        try:await release.wait()
        finally:active-=1
        return {'title':'合法方案','message':'介绍会员服务','reasoning':'已知事实','score':70}
    monkeypatch.setattr('app.services.campaigns.marketing_graph.ainvoke',slow)
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            paths=[await campaign(c,False) for _ in range(20)]
            try:
                rs=await asyncio.gather(*(c.post(p+'/generate') for p in paths))
                assert sum(r.status_code==202 for r in rs)==2
                assert all(r.status_code in (202,429) for r in rs)
                assert peak<=2
            finally:
                release.set()
                await asyncio.gather(*list(app.state.tasks))
            assert active==0

@pytest.mark.asyncio
async def test_timeout_and_bounded_shutdown(monkeypatch):
    cfg=get_settings();monkeypatch.setattr(cfg,'candidate_timeout_seconds',.03)
    active=0
    async def slow(state):
        nonlocal active
        active+=1
        try:await asyncio.Event().wait()
        finally:active-=1
    monkeypatch.setattr('app.services.campaigns.marketing_graph.ainvoke',slow)
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            path=await campaign(c)
            assert (await c.get(path)).json()['status']=='FAILED'
            assert active==0
    monkeypatch.setattr(cfg,'candidate_timeout_seconds',30)
    monkeypatch.setattr(cfg,'shutdown_grace_seconds',.03)
    async with asyncio.timeout(3):
        async with lifespan(app):
            async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
                path=await campaign(c,False);await c.post(path+'/generate')
                await asyncio.sleep(.01)
    assert active==0


@pytest.mark.asyncio
async def test_event_cursor_boundaries():
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            path=await campaign(c,False)
            for cursor in ['-1','9223372036854775808','9'*5000]:
                assert (await c.get(path+'/events',params={'after':cursor})).status_code==422
                assert (await c.get(path+'/stream',headers={'Last-Event-ID':cursor})).status_code==422


@pytest.mark.parametrize('rule',[None,[],{'field':[],'op':'eq','value':1},{'field':'x','op':{},'value':1}])
def test_hostile_rule_shapes(rule):
    from app.services.rules import evaluate
    assert evaluate(rule,{'x':1}) is False


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['huge','invalid_json','negative_usage','empty_choices'])
async def test_hostile_provider_response(monkeypatch,tmp_path,mode):
    import json,httpx
    from app.agents.provider import draft
    cfg=get_settings()
    for key,val in {'ai_provider':'deepseek','deepseek_api_key':'dummy','llm_audit_path':str(tmp_path/'budget.jsonl')}.items():
        monkeypatch.setattr(cfg,key,val)
    def handle(req):
        if mode=='huge':return httpx.Response(200,content=b'x'*100000)
        if mode=='invalid_json':return httpx.Response(200,content=b'not json')
        result={'choices':[],'usage':{'total_tokens':-1000}}
        if mode=='negative_usage':result['choices']=[{'finish_reason':'stop','message':{'content':'{"title":"x","message":"x","reasoning":"x"}'}}]
        return httpx.Response(200,json=result)
    original=httpx.AsyncClient
    monkeypatch.setattr('app.agents.provider.httpx.AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handle),**kw))
    if mode=='negative_usage':await draft('合法活动需求','all',1)
    else:
        with pytest.raises(Exception):await draft('合法活动需求','all',1)
    rows=[json.loads(x) for x in (tmp_path/'budget.jsonl').read_text().splitlines()]
    assert rows[-1]['charged_upper_cny']==rows[0]['reserve_cny']

@pytest.mark.asyncio
async def test_streamed_large_body_and_hostile_inputs():
    async def chunks():
        for _ in range(20):yield b'x'*8192
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            assert (await c.post('/api/v1/campaigns',content=chunks())).status_code==413
            assert (await c.post('/api/v1/campaigns',content=b'{bad json')).status_code==422
            for brief in ['x'*2001,'\x00'*10,' '*20]:
                assert (await c.post('/api/v1/campaigns',json={'brief':brief,'budget':1000})).status_code==422
            sql="'; DROP TABLE campaigns; --"
            r=await c.post('/api/v1/campaigns',json={'brief':sql,'budget':1000})
            assert r.status_code==201
            assert (await c.get('/api/v1/campaigns')).status_code==200


@pytest.mark.asyncio
async def test_database_unavailable_has_controlled_response(monkeypatch):
    from sqlalchemy.exc import OperationalError
    from app.db.base import session_dep
    class Broken:
        async def scalars(self,*args,**kw):
            raise OperationalError('private SQL text',{},Exception('private backend password'))
    async def dependency():yield Broken()
    async with lifespan(app):
        app.dependency_overrides[session_dep]=dependency
        try:
            async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
                r=await c.get('/api/v1/campaigns')
                assert r.status_code==503 and r.headers['Retry-After']=='2'
                assert 'password' not in r.text and 'SQL' not in r.text
        finally:app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_concurrent_budget_reservations(monkeypatch,tmp_path):
    import json,httpx
    from app.agents.provider import draft,BudgetExceeded
    cfg=get_settings()
    for key,val in {'ai_provider':'deepseek','deepseek_api_key':'dummy','llm_max_calls':2,
                    'llm_audit_path':str(tmp_path/'concurrent.jsonl')}.items():monkeypatch.setattr(cfg,key,val)
    calls=0
    def handle(req):
        nonlocal calls
        calls+=1
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"title":"ok","message":"ok","reasoning":"ok"}'}}],
            'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}})
    original=httpx.AsyncClient
    monkeypatch.setattr('app.agents.provider.httpx.AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handle),**kw))
    results=await asyncio.gather(*(draft('并发预算验证需求','all',1) for _ in range(30)),return_exceptions=True)
    assert calls==2
    assert sum(isinstance(x,BudgetExceeded) for x in results)==28
    rows=[json.loads(x) for x in (tmp_path/'concurrent.jsonl').read_text().splitlines()]
    assert sum(r['event']=='reserved' for r in rows)==2

@pytest.mark.asyncio
async def test_slow_body_read_deadline():
    from app.core.body_limit import RequestBodyLimit
    async def downstream(*args):raise AssertionError('slow request must not reach application')
    async def receive():await asyncio.Event().wait()
    sent=[]
    async def send(message):sent.append(message)
    guard=RequestBodyLimit(downstream,read_timeout_seconds=.02)
    async with asyncio.timeout(1):
        await guard({'type':'http','path':'/api/v1/campaigns'},receive,send)
    assert sent[0]['status']==408


@pytest.mark.asyncio
async def test_slow_storage_does_not_block_health(monkeypatch):
    import threading
    started=threading.Event();release=threading.Event()
    from app.services.campaigns import put_blob
    def slow(key,body):
        started.set()
        assert release.wait(timeout=3)
        return put_blob(key,body)
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            path=await campaign(c);await approve(c,path)
            monkeypatch.setattr('app.services.campaigns.put_blob',slow)
            task=asyncio.create_task(c.post(path+'/pilot'))
            try:
                assert await asyncio.to_thread(started.wait,1)
                async with asyncio.timeout(.5):
                    assert (await c.get('/api/v1/health')).status_code==200
            finally:
                release.set();assert (await task).status_code==200

@pytest.mark.asyncio
async def test_generation_commit_failure_is_atomic(monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.exc import OperationalError
    async def fail_commit(self):raise OperationalError('injected',{},Exception('unavailable'))
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            path=await campaign(c,False)
            with monkeypatch.context() as m:
                m.setattr(AsyncSession,'commit',fail_commit)
                assert (await c.post(path+'/generate')).status_code==503
            assert (await c.get(path)).json()['status']=='CREATED'
            assert (await c.get(path+'/events')).json()==[]
            assert not app.state.tasks and app.state.pending_admissions==0
            assert (await c.post(path+'/generate')).status_code==202
            await asyncio.gather(*list(app.state.tasks))

@pytest.mark.asyncio
async def test_non_ascii_authorization_is_rejected(monkeypatch):
    monkeypatch.setattr(get_settings(),'access_token','test-secret')
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            assert (await c.get('/api/v1/campaigns',headers={'Authorization':b'Bearer \xff'})).status_code==401

@pytest.mark.asyncio
async def test_lease_rejects_stale_owner_and_preserves_new_owner():
    import time
    from sqlalchemy import update
    from app.models.entities import ExecutionLease,Candidate
    from app.services.leases import claim,renew,release,fence,LostLease
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            path=await campaign(c,False);cid=path.rsplit('/',1)[1]
            first=await claim(cid)
            assert first and await claim(cid) is None
            async with SessionLocal() as db:
                await db.execute(update(ExecutionLease).where(ExecutionLease.campaign_id==cid).values(expires_at=time.time()-1))
                await db.commit()
            second=await claim(cid);assert second and second!=first
            assert not await renew(cid,first)
            async with SessionLocal() as db:
                db.add(Candidate(campaign_id=cid,variant=1,title='stale',message='stale',segment='all',reasoning='stale',score=0))
                with pytest.raises(LostLease):await fence(db,cid,first)
            assert (await c.get(path+'/candidates')).json()==[]
            await release(cid,first)
            async with SessionLocal() as db:
                lease=await db.get(ExecutionLease,cid)
                assert lease.owner==second and lease.expires_at>time.time()
                await fence(db,cid,second)
            await release(cid,second)


def test_budget_corruption_fails_closed(tmp_path):
    from app.services.budget import reserve_call
    journal=tmp_path/'corrupt.jsonl';journal.write_text('{broken')
    with pytest.raises(ValueError):reserve_call(journal,.1,'mock',18,10)
    assert journal.read_text()=='{broken'


def test_budget_settlement_is_idempotent(tmp_path):
    import json
    from app.services.budget import reserve_call,settle_call,BudgetExceeded
    journal=tmp_path/'budget.jsonl'
    call=reserve_call(journal,.5,'mock',1,10)
    event={'event':'settled','call_id':call,'charged_upper_cny':.01}
    settle_call(journal,event);settle_call(journal,event)
    assert len(journal.read_text().splitlines())==2
    with pytest.raises(BudgetExceeded):reserve_call(journal,.5,'mock',1,10)
