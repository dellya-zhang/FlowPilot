"""Contracts for structured planning: real graph/SQL, no paid model calls."""
import asyncio
import csv
import io
import math
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app, lifespan
from app.schemas.contracts import PlanningInput
from app.services.planning import plan_document


def settings(**overrides):
    return {**PlanningInput(known_benefits='积分查询与专属客服，不提供额外优惠', region='east', unit_cost_cny=.2, enable_holdout=True).model_dump(), **overrides}


async def create(client, planning):
    response = await client.post('/api/v1/campaigns',json={'brief':'向新会员介绍已有服务权益','audience':'new','budget':1000,'planning':planning})
    assert response.status_code==201,response.text
    return '/api/v1/campaigns/'+response.json()['id']


@pytest.mark.asyncio
async def test_structured_plan_drives_exports_and_freezes():
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as client:
            path=await create(client,settings())
            assert (await client.post(path+'/generate')).status_code==202
            await asyncio.gather(*list(app.state.tasks))
            candidates=(await client.get(path+'/candidates')).json()
            assert len(candidates)==3
            plan=candidates[0]['plan_data']
            assert len(plan['trace'])==7
            assert plan['segment']['estimated_size']>plan['segment']['treatment_size']>0
            assert plan['segment']['holdout_size']==math.floor(plan['segment']['estimated_size']*.1)
            assert plan['goal']['budget_cny']==1000
            assert plan['cost_estimate']['total_cny']==round(plan['segment']['treatment_size']*.2,4)
            assert sum(r['ratio'] for r in plan['strategy']['pilot_schedule'])==1
            assert len({c['plan_data']['strategy']['message_focus'] for c in candidates})==3
            assert (await client.put(path+'/planning',json=settings(region='north'))).status_code==409
            await client.post(path+'/approve',json={'candidate_id':candidates[0]['id']})
            pilot=(await client.post(path+'/pilot')).json()
            full=(await client.post(path+'/finalize')).json()
            assert pilot['row_count']==math.ceil(plan['segment']['treatment_size']*.1)
            assert pilot['row_count']+full['row_count']==plan['segment']['treatment_size']
            ids=[]
            for artifact in [pilot,full]:
                response=await client.get('/api/v1/artifacts/'+artifact['id']+'/download')
                rows=list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
                assert all(r['region']=='east' and r['segment']=='new' for r in rows)
                ids.append({r['customer_id'] for r in rows})
            assert ids[0].isdisjoint(ids[1])
            assert (await client.get(path+'/candidates')).json()[0]['plan_data']==plan


@pytest.mark.asyncio
async def test_clarification_and_preflight_make_zero_model_calls(monkeypatch):
    calls=[]
    async def forbidden(*args,**kwargs):
        calls.append(1)
        raise AssertionError('Model must not be called')
    monkeypatch.setattr('app.agents.marketing.draft',forbidden)
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as client:
            path=await create(client,settings(known_benefits=''))
            assert (await client.get(path)).json()['status']=='NEEDS_CLARIFICATION'
            assert (await client.get(path+'/planning')).json()['questions']
            assert (await client.post(path+'/generate')).status_code==409
            assert (await client.put(path+'/planning',json=settings(unit_cost_cny=1000))).json()['status']=='CREATED'
            assert (await client.post(path+'/generate')).status_code==422
            assert (await client.get(path)).json()['status']=='CREATED'
            assert not calls


@pytest.mark.asyncio
async def test_ab_preserves_holdout_and_restores_experiment_id():
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as client:
            path=await create(client,settings(region='all',pilot_ratio=.2))
            await client.post(path+'/generate');await asyncio.gather(*list(app.state.tasks))
            candidates=(await client.get(path+'/candidates')).json()
            count=candidates[0]['plan_data']['segment']['treatment_size']
            exp=(await client.post('/api/v1/experiments',json={'campaign_id':path.split('/')[-1],'candidate_ids':[c['id'] for c in candidates[:2]]})).json()
            assert all(len(users)==math.ceil(count*.2/2) for users in exp['arms'].values())
            assert (await client.get(path+'/planning')).json()['experiment_id']==exp['id']
            assert (await client.post(path+'/approve',json={'candidate_id':candidates[0]['id']})).status_code==409
            await client.post('/api/v1/experiments/'+exp['id']+'/promote',params={'candidate_id':candidates[0]['id']})
            full=(await client.post(path+'/finalize')).json()
            assert full['row_count']==count-sum(map(len,exp['arms'].values()))


@pytest.mark.asyncio
async def test_provider_receives_budget_and_strategy(monkeypatch):
    from app.agents.provider import draft
    from app.core.config import get_settings
    import json
    captured=[]
    async def completion(messages,schema):
        captured.append(json.loads(messages[1]['content']))
        return {'outcome':'draft','title':'已有权益','message':'了解积分查询','reasoning':'已知事实'}
    monkeypatch.setattr(get_settings(),'ai_provider','deepseek')
    monkeypatch.setattr('app.agents.provider.complete_json',completion)
    plan=plan_document({'brief':'介绍会员权益','budget':1234,'audience':'new','variant':1,'planning':settings(),'audience_count':30})
    await draft('介绍会员权益','new',1,budget=1234,plan=plan)
    assert captured[0]['budget_cny']==1234
    assert captured[0]['plan_constraints']['offer_strategy']['verified_benefits']==settings()['known_benefits']


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome',['clarification','refusal'])
async def test_nondraft_never_becomes_candidate(monkeypatch,outcome):
    async def result(*args,**kwargs):
        return {'outcome':outcome,'title':'不能形成方案','message':'请补充信息','reasoning':'缺少事实'}
    monkeypatch.setattr('app.agents.marketing.draft',result)
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as client:
            path=await create(client,settings())
            await client.post(path+'/generate');await asyncio.gather(*list(app.state.tasks))
            assert (await client.get(path+'/candidates')).json()==[]
            assert (await client.get(path)).json()['status']==('NEEDS_CLARIFICATION' if outcome=='clarification' else 'FAILED')


@pytest.mark.parametrize('updates',[{'pilot_ratio':.9},{'window_days':1},{'channel':'unknown'},{'unit_cost_cny':-1},{'holdout_ratio':.99}])
def test_planning_constraints_reject_invalid(updates):
    with pytest.raises(ValueError):PlanningInput(**settings(**updates))


def test_planned_model_output_requires_explicit_kind():
    from app.agents.provider import PlannedDraftOutput
    with pytest.raises(ValueError):
        PlannedDraftOutput(title='标题',message='正文',reasoning='理由')
