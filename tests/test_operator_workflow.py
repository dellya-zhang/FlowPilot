"""Version, handoff and observation invariants with isolated SQL + Mock only."""
import asyncio
import csv
import io
from datetime import datetime,timedelta,timezone
import pytest
from httpx import AsyncClient,ASGITransport
from sqlalchemy import delete
from app.main import app,lifespan
from app.db.base import SessionLocal
from app.models.entities import Customer,Candidate,CandidatePlan
from app.schemas.contracts import PlanningInput


def config(**kw):
    return PlanningInput(**{'known_benefits':'积分查询、专属客服；不提供额外优惠','unit_cost_cny':.2,**kw}).model_dump()


async def generated(client,**kw):
    r=await client.post('/api/v1/campaigns',json={'brief':'向新会员介绍已确认的服务权益','audience':'new','budget':1000,'planning':config(**kw)})
    assert r.status_code==201,r.text
    path='/api/v1/campaigns/'+r.json()['id']
    assert (await client.post(path+'/generate')).status_code==202
    await asyncio.gather(*list(app.state.tasks))
    rows=(await client.get(path+'/candidates')).json()
    assert len(rows)>=2,rows
    return path,rows


@pytest.mark.asyncio
async def test_revision_history_stale_approval_and_bound_csv():
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as c:
            path,rows=await generated(c);x=rows[0];editpath=path+'/candidates/'+x['id']+'/revisions'
            r=await c.post(editpath,json={'expected_revision':1,'message':'了解积分查询，请自愿联系专属客服。','reason':'删去重复内容，便于阅读'})
            assert r.status_code==201,r.text
            assert r.json()['revision']==2
            history=(await c.get(editpath)).json()
            assert [h['revision'] for h in history]==[1,2]
            assert history[0]['snapshot']['message']==x['message']
            assert (await c.post(path+'/approve',json={'candidate_id':x['id'],'expected_revision':1})).status_code==409
            assert (await c.post(path+'/approve',json={'candidate_id':x['id'],'expected_revision':2})).status_code==200
            assert (await c.post(editpath,json={'expected_revision':2,'message':'另一个版本','reason':'审批后尝试修改'})).status_code==409
            # A later database edit must not change the snapshot already approved.
            async with SessionLocal() as db:
                candidate=await db.get(Candidate,x['id']);candidate.message='之后的数据库内容';await db.commit()
            artifact=(await c.post(path+'/pilot')).json()
            assert artifact['binding']['revision']==2
            assert artifact['binding']['snapshot']['message']=='了解积分查询，请自愿联系专属客服。'
            download=await c.get('/api/v1/artifacts/'+artifact['id']+'/download')
            csvrows=list(csv.DictReader(io.StringIO(download.content.decode('utf-8-sig'))))
            assert all(r['plan_revision']=='2' and r['message']=='了解积分查询，请自愿联系专属客服。' for r in csvrows)
            assert (await c.post(path+'/pilot')).json()['id']==artifact['id']


@pytest.mark.asyncio
async def test_competing_revisions_and_invalid_edits():
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as c:
            path,rows=await generated(c);url=path+'/candidates/'+rows[0]['id']+'/revisions'
            payload={'expected_revision':1,'reason':'删除不必要文字','message':'积分查询与专属客服，可自愿了解。'}
            responses=await asyncio.gather(*(c.post(url,json=payload) for _ in range(5)))
            assert sorted(r.status_code for r in responses)==[201,409,409,409,409]
            assert len((await c.get(url)).json())==2
            for edit in [{'channel':'sms'},{'message':'保证收益'},{'contacts_per_user':4}]:
                r=await c.post(url,json={**payload,'expected_revision':2,**edit})
                assert r.status_code==422,r.text


@pytest.mark.asyncio
async def test_frequency_axis_controls_reference_and_cost(monkeypatch):
    async def forbidden(*args,**kwargs):
        raise AssertionError("Frequency experiments must use the operator reference, without model calls")
    monkeypatch.setattr("app.agents.marketing.draft",forbidden)
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as c:
            path,rows=await generated(c,comparison_axis='frequency',reference_message='欢迎查询积分或联系专属客服。')
            assert {r['message'] for r in rows}=={'欢迎查询积分或联系专属客服。'}
            assert [r['plan_data']['strategy']['contacts_per_user'] for r in rows]==[1,2,3]
            costs=[r['plan_data']['cost_estimate']['total_cny'] for r in rows]
            assert costs[1]==round(2*costs[0],4) and costs[2]==round(3*costs[0],4)
            x=rows[0]
            await c.post(path+'/candidates/'+x['id']+'/revisions',json={'expected_revision':1,'message':'修改后的另一份统一文案。','reason':'试验混杂变量拦截'})
            expinput={'campaign_id':path.split('/')[-1],'candidate_ids':[r['id'] for r in rows[:2]]}
            r=await c.post('/api/v1/experiments',json=expinput)
            assert r.status_code==422 and '混杂' in r.text
            await c.post(path+'/candidates/'+rows[1]['id']+'/revisions',json={'expected_revision':1,'message':'修改后的另一份统一文案。','reason':'统一非比较变量'})
            r=await c.post('/api/v1/experiments',json={**expinput,'expected_revisions':{rows[0]['id']:2,rows[1]['id']:1}})
            assert r.status_code==409
            r=await c.post('/api/v1/experiments',json={**expinput,'expected_revisions':{rows[0]['id']:2,rows[1]['id']:2}})
            assert r.status_code==201,r.text
            eid=r.json()['id'];report=(await c.get('/api/v1/experiments/'+eid)).json()
            assert report['binding']['comparison']['validated']
            assert report['binding']['snapshots'][x['id']]['revision']==2
            assert (await c.post(path+'/candidates/'+x['id']+'/revisions',json={'expected_revision':2,'title':'不能修改','reason':'实验期间冻结'})).status_code==409
            await c.post('/api/v1/experiments/'+eid+'/promote',params={'candidate_id':x['id']})
            full=(await c.post(path+'/finalize')).json()
            assert full['binding']['revision']==2


@pytest.mark.asyncio
async def test_channels_use_confirmed_availability_and_prices():
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as c:
            path,rows=await generated(c,comparison_axis='channel',reference_message='统一的会员权益通知。',available_channels=['sms','in_app'],unit_cost_cny=1000,channel_costs={'sms':.03,'in_app':.01})
            assert len(rows)==2
            assert [r['plan_data']['strategy']['channel'] for r in rows]==['sms','in_app']
            assert all(r['plan_data']['cost_estimate']['within_budget'] for r in rows)
            assert len({r['message'] for r in rows})==1


@pytest.mark.asyncio
@pytest.mark.parametrize('extra',[{'comparison_axis':'frequency'},{'comparison_axis':'channel','reference_message':'统一的文案','available_channels':['sms']}])
async def test_missing_experimental_controls_require_clarification(extra):
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as c:
            r=await c.post('/api/v1/campaigns',json={'brief':'介绍会员权益','budget':1000,'planning':config(**extra)})
            assert r.json()['status']=='NEEDS_CLARIFICATION'
            assert (await c.post('/api/v1/campaigns/'+r.json()['id']+'/generate')).status_code==409


@pytest.mark.asyncio
async def test_audience_snapshot_excludes_new_members_and_revocations():
    revoked=None
    try:
        async with lifespan(app):
            async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as c:
                path,rows=await generated(c)
                await c.post(path+'/approve',json={'candidate_id':rows[0]['id']})
                async with SessionLocal() as db:
                    revoked=await db.get(Customer,1);old_consent=revoked.consent;revoked.consent=False
                    db.add(Customer(id=999999,segment='new',region='east',consent=True));await db.commit()
                a=(await c.post(path+'/pilot')).json();b=(await c.post(path+'/finalize')).json()
                found=set()
                for artifact in [a,b]:
                    content=(await c.get('/api/v1/artifacts/'+artifact['id']+'/download')).content.decode('utf-8-sig')
                    found.update(int(r['customer_id']) for r in csv.DictReader(io.StringIO(content)))
                assert 1 not in found and 999999 not in found
    finally:
        async with SessionLocal() as db:
            if revoked is not None:
                current=await db.get(Customer,1);current.consent=old_consent
            await db.execute(delete(Customer).where(Customer.id==999999));await db.commit()


@pytest.mark.asyncio
async def test_reviews_idempotency_sources_and_no_invented_savings():
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as c:
            path,rows=await generated(c)
            payload={'event_id':'review-'+path.split('/')[-1],'candidate_id':rows[0]['id'],'revision':1,'decision':'adopted','reason':'权益描述清晰，可供试点','minutes_spent':4,'source':'manual_observation'}
            assert (await c.post(path+'/reviews',json=payload)).status_code==200
            assert (await c.post(path+'/reviews',json=payload)).json()['duplicate']
            assert (await c.post(path+'/reviews',json={**payload,'minutes_spent':9})).status_code==409
            summary=(await c.get(path+'/evaluation')).json();groups={g['source']:g for g in summary['groups']}
            assert groups['manual_observation']['review_count']==1
            assert groups['manual_observation']['reported_minutes_difference'] is None
            assert groups['demo']['review_count']==0
            assert all(g['observed_conversion_rate'] is None for g in summary['groups'])
            await c.post(path+'/reviews',json={**payload,'event_id':payload['event_id']+'-paired','baseline_minutes':10,'minutes_spent':12})
            groups={g['source']:g for g in (await c.get(path+'/evaluation')).json()['groups']}
            assert groups['manual_observation']['reported_minutes_difference']==-2
            assert (await c.post(path+'/reviews',json={**payload,'event_id':payload['event_id']+'-bad','revision':99})).status_code==422


@pytest.mark.asyncio
async def test_observation_membership_delivery_window_source_and_dedup(monkeypatch):
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as c:
            path,rows=await generated(c)
            await c.post(path+'/approve',json={'candidate_id':rows[0]['id']})
            artifact=(await c.post(path+'/pilot')).json();uid=artifact['binding']['audience_ids'][0]
            now=datetime.now(timezone.utc)
            payload={'event_id':'obs-'+path.split('/')[-1],'candidate_id':rows[0]['id'],'revision':1,'customer_id':uid,'phase':'pilot','outcome':'converted','source':'manual_observation','occurred_at':now.isoformat(),'actual_cost_cny':None,'contact_number':None}
            assert (await c.post(path+'/observations',json=payload)).status_code==422
            delivered={**payload,'event_id':payload['event_id']+'-delivery','outcome':'delivered','actual_cost_cny':.2,'contact_number':1}
            assert (await c.post(path+'/observations',json={**delivered,'customer_id':999999})).status_code==422
            assert (await c.post(path+'/observations',json={**delivered,'occurred_at':(now-timedelta(days=3)).isoformat()})).status_code==422
            assert (await c.post(path+'/observations',json=delivered)).status_code==200
            assert (await c.post(path+'/observations',json=delivered)).json()['duplicate']
            assert (await c.post(path+'/observations',json={**delivered,'event_id':delivered['event_id']+'-new'})).json()['duplicate']
            assert (await c.post(path+'/observations',json={**delivered,'actual_cost_cny':1})).status_code==409
            assert (await c.post(path+'/observations',json={**payload,'source':'demo'})).status_code==422
            assert (await c.post(path+'/observations',json={**payload,'revision':2})).status_code==422
            monkeypatch.setattr('app.api.operations.now_utc',lambda:now+timedelta(days=20))
            late={**payload,'occurred_at':(now+timedelta(days=8)).isoformat()}
            result=await c.post(path+'/observations',json=late)
            assert result.status_code==200 and not result.json()['within_window'],result.text
            summary=(await c.get(path+'/evaluation')).json();groups={g['source']:g for g in summary['groups']}
            assert groups['manual_observation']['observed_delivered']==1
            assert groups['manual_observation']['late_conversions']==1
            assert groups['manual_observation']['within_window_converted']==0
            assert groups['manual_observation']['reported_contact_cost_cny']==.2
            assert groups['demo']['observed_delivered']==0
            assert summary['breakdown'][0]['followup_complete']


@pytest.mark.asyncio
async def test_csv_formula_prefix_is_escaped():
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as c:
            path,rows=await generated(c);x=rows[0]
            r=await c.post(path+'/candidates/'+x['id']+'/revisions',json={'expected_revision':1,'message':'=测试前导字符，不含任何公式指令','reason':'验证CSV按文本导出'})
            assert r.status_code==201
            await c.post(path+'/approve',json={'candidate_id':x['id'],'expected_revision':2})
            a=(await c.post(path+'/pilot')).json()
            text=(await c.get('/api/v1/artifacts/'+a['id']+'/download')).content.decode('utf-8-sig')
            assert all(r['message'].startswith("'=") for r in csv.DictReader(io.StringIO(text)))

@pytest.mark.asyncio
async def test_batch_observations_report_partial_success_and_safe_retry():
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as c:
            path,rows=await generated(c)
            await c.post(path+'/approve',json={'candidate_id':rows[0]['id']})
            artifact=(await c.post(path+'/pilot')).json();uid=artifact['binding']['audience_ids'][0]
            payload={'event_id':'batch-'+path.split('/')[-1],'candidate_id':rows[0]['id'],'revision':1,'customer_id':uid,'phase':'pilot','outcome':'delivered','source':'demo','occurred_at':datetime.now(timezone.utc).isoformat(),'actual_cost_cny':None,'contact_number':1}
            batch={'rows':[payload,{**payload,'event_id':payload['event_id']+'-invalid','customer_id':999999}]}
            response=await c.post(path+'/observations/batch',json=batch)
            assert response.status_code==200,response.text
            assert response.json()['accepted']==1 and response.json()['rejected']==1
            assert (await c.post(path+'/observations/batch',json=batch)).json()['results'][0]['duplicate']
            summary=(await c.get(path+'/evaluation')).json()
            assert summary['groups'][0]['observed_delivered']==1
            assert summary['groups'][0]['reported_contact_cost_cny'] is None
            assert (await c.post(path+'/observations/batch',json={'rows':[payload]*101})).status_code==422


@pytest.mark.asyncio
async def test_observation_frequency_deviation_and_immature_result():
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as c:
            path,rows=await generated(c,comparison_axis='frequency',reference_message='欢迎自愿了解会员权益。')
            candidate=rows[2]
            await c.post(path+'/approve',json={'candidate_id':candidate['id']})
            artifact=(await c.post(path+'/pilot')).json();uid=artifact['binding']['audience_ids'][0]
            payload={'event_id':'deviation-'+path.split('/')[-1],'candidate_id':candidate['id'],'revision':1,'customer_id':uid,'phase':'pilot','outcome':'delivered','source':'demo','occurred_at':datetime.now(timezone.utc).isoformat(),'contact_number':1}
            assert (await c.post(path+'/observations',json=payload)).status_code==200
            summary=(await c.get(path+'/evaluation')).json()
            assert summary['cohorts'][0]['planned_contacts']==3
            assert summary['breakdown'][0]['frequency_deviations']==1
            assert not summary['breakdown'][0]['followup_complete']
            assert summary['breakdown'][0]['status']=='preliminary'

@pytest.mark.asyncio
async def test_repeated_contacts_do_not_inflate_delivered_users_or_conversion_rate():
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as c:
            path,rows=await generated(c,comparison_axis='frequency',reference_message='可自愿了解会员权益。')
            x=rows[2]
            await c.post(path+'/approve',json={'candidate_id':x['id'],'expected_revision':1,'decision_reason':'只做受控流程验证，保留三次触达的风险记录'})
            a=(await c.post(path+'/pilot')).json();uid=a['binding']['audience_ids'][0];now=datetime.now(timezone.utc)
            payload={'event_id':'receipt-'+path.split('/')[-1],'candidate_id':x['id'],'revision':1,'customer_id':uid,'phase':'pilot','outcome':'delivered','source':'demo','occurred_at':now.isoformat(),'contact_number':1,'actual_cost_cny':.2}
            assert (await c.post(path+'/observations',json=payload)).status_code==200
            assert (await c.post(path+'/observations',json={**payload,'event_id':payload['event_id']+'-3','contact_number':3})).status_code==422
            second={**payload,'event_id':payload['event_id']+'-2','contact_number':2,'occurred_at':(now+timedelta(seconds=1)).isoformat()}
            assert (await c.post(path+'/observations',json=second)).status_code==200
            assert (await c.post(path+'/observations',json=second)).json()['duplicate']
            converted={**payload,'event_id':payload['event_id']+'-converted','contact_number':None,'outcome':'converted','actual_cost_cny':None,'occurred_at':(now+timedelta(seconds=2)).isoformat()}
            assert (await c.post(path+'/observations',json=converted)).status_code==200
            third={**payload,'event_id':payload['event_id']+'-3','contact_number':3,'occurred_at':(now+timedelta(seconds=3)).isoformat()}
            assert (await c.post(path+'/observations',json=third)).status_code==200
            result=(await c.get(path+'/evaluation')).json();demo=result['groups'][0]
            assert demo['observed_delivered']==1 and demo['observed_contacts']==3
            assert demo['within_window_converted']==1 and demo['observed_conversion_rate']==1
            assert demo['reported_contact_cost_cny']==.6
            assert result['breakdown'][0]['frequency_deviations']==0
            assert result['decision']['decision_reason']=='只做受控流程验证，保留三次触达的风险记录'
