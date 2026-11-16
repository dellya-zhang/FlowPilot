"""Operator revisions and observations. No operation here sends marketing messages."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from app.db.base import session_dep
from app.models.entities import (Campaign,Candidate,CandidatePlan,PlanRevision,Experiment,ExperimentBinding,
    Artifact,ArtifactBinding,DecisionBinding,OperatorReview,ExecutionObservation)
from app.schemas.contracts import InputModel
from app.services.campaigns import require_campaign, emit
from app.services.versions import checked_snapshot,current_snapshot,save_revision,revised_snapshot,digest

def now_utc():
    return datetime.now(timezone.utc)


router=APIRouter(prefix='/api/v1',tags=['operator-workflow'])


class CandidateEdit(InputModel):
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=3,max_length=1000)
    title: str | None = Field(default=None,min_length=1,max_length=150)
    message: str | None = Field(default=None,min_length=1,max_length=400)
    channel: Literal['manual','sms','in_app'] | None = None
    contacts_per_user: int | None = Field(default=None,ge=1,le=3)


class ReviewInput(InputModel):
    event_id: str = Field(min_length=1,max_length=80)
    candidate_id: str
    revision: int = Field(ge=1)
    decision: Literal['adopted','edited','rejected']
    reason: str = Field(min_length=3,max_length=1000)
    minutes_spent: float = Field(ge=0,le=1440,allow_inf_nan=False)
    baseline_minutes: float | None = Field(default=None,ge=0,le=1440,allow_inf_nan=False)
    source: Literal['demo','manual_observation'] = 'demo'


class ObservationInput(InputModel):
    event_id: str = Field(min_length=1,max_length=80)
    candidate_id: str
    revision: int = Field(ge=1)
    customer_id: int = Field(ge=1)
    phase: Literal['pilot','full']
    outcome: Literal['delivered','converted']
    source: Literal['demo','manual_observation'] = 'demo'
    occurred_at: datetime
    contact_number: int | None = Field(default=None,ge=1,le=10)
    actual_cost_cny: float | None = Field(default=None,ge=0,le=100000,allow_inf_nan=False)

    @field_validator('occurred_at')
    @classmethod
    def require_timezone(cls,value):
        if value.tzinfo is None:raise ValueError('Timestamp needs timezone')
        return value.astimezone(timezone.utc)


async def lock_campaign(db,cid):
    await db.execute(update(Campaign).where(Campaign.id==cid).values(updated_at=Campaign.updated_at))
    return await require_campaign(db,cid)


async def own_candidate(db,cid,candidate_id):
    candidate=await db.get(Candidate,candidate_id)
    if not candidate or candidate.campaign_id!=cid:raise HTTPException(404,'当前计划中没有此候选')
    return candidate


async def flush_record(db):
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409,'记录ID或用户触达序号已被其他请求使用') from None


@router.get('/campaigns/{cid}/candidates/{candidate_id}/revisions')
async def revisions(cid:str,candidate_id:str,db=Depends(session_dep)):
    candidate=await own_candidate(db,cid,candidate_id)
    rows=(await db.scalars(select(PlanRevision).where(PlanRevision.candidate_id==candidate_id).order_by(PlanRevision.revision))).all()
    return [{'revision':r.revision,'reason':r.reason,'snapshot':r.snapshot,'snapshot_sha256':digest(r.snapshot),'created_at':r.created_at.isoformat()+'Z'} for r in rows] or [{'revision':1,'reason':'历史方案基线','snapshot':await current_snapshot(db,candidate),'created_at':None}]


@router.post('/campaigns/{cid}/candidates/{candidate_id}/revisions',status_code=201)
async def revise(cid:str,candidate_id:str,data:CandidateEdit,db=Depends(session_dep)):
    campaign=await lock_campaign(db,cid)
    candidate=await own_candidate(db,cid,candidate_id)
    if campaign.status!='WAITING_APPROVAL' or await db.scalar(select(Experiment.id).where(Experiment.campaign_id==cid)):
        raise HTTPException(409,'审批或创建实验后方案已冻结；请创建新计划进行下一轮修改')
    snapshot=await checked_snapshot(db,candidate,data.expected_revision)
    updated=revised_snapshot(snapshot,data,campaign.budget)
    candidate.title=updated['title'];candidate.message=updated['message']
    plan=await db.get(CandidatePlan,candidate_id);plan.data=deepcopy(updated['plan_data'])
    await save_revision(db,updated,data.reason)
    await emit(db,cid,'candidate.revised',{'candidate_id':candidate_id,'revision':updated['revision'],'reason':data.reason})
    return updated


async def validate_version(db,cid,candidate_id,revision):
    candidate=await own_candidate(db,cid,candidate_id)
    row=await db.get(PlanRevision,(candidate_id,revision))
    if row:return row.snapshot
    current=await current_snapshot(db,candidate)
    if current['revision']!=revision:raise HTTPException(422,'方案版本不存在')
    await save_revision(db,current,'历史方案版本基线')
    return current


@router.post('/campaigns/{cid}/reviews')
async def review(cid:str,data:ReviewInput,db=Depends(session_dep)):
    campaign=await lock_campaign(db,cid)
    if campaign.status not in ('WAITING_APPROVAL','PILOT_READY','PILOT_EXPORTED','COMPLETED'):raise HTTPException(409,'方案生成完成后才能记录评价')
    payload=data.model_dump(mode='json')
    old=await db.get(OperatorReview,data.event_id)
    if old:
        if old.campaign_id!=cid or old.data!=payload:raise HTTPException(409,'复用的记录ID对应不同内容')
        return {'accepted':True,'duplicate':True}
    await validate_version(db,cid,data.candidate_id,data.revision)
    db.add(OperatorReview(id=data.event_id,campaign_id=cid,data=payload))
    await flush_record(db)
    await emit(db,cid,'operator.reviewed',{'candidate_id':data.candidate_id,'revision':data.revision,'decision':data.decision,'source':data.source})
    return {'accepted':True,'duplicate':False}


async def cohorts(db,cid):
    result=[]
    for artifact in (await db.scalars(select(Artifact).where(Artifact.campaign_id==cid))).all():
        binding=await db.get(ArtifactBinding,artifact.id)
        if binding:
            d=binding.data;plan=d['snapshot'].get('plan_data') or {}
            result.append({'available_since':d.get('exported_at',d.get('bound_at')),'planned_contacts':plan.get('strategy',{}).get('contacts_per_user',1),'phase':artifact.kind,'candidate_id':d['candidate_id'],'revision':d['revision'],
                'customer_ids':d['audience_ids'],'window_days':plan.get('strategy',{}).get('ab_experiment',{}).get('window_days',7)})
    exp=await db.scalar(select(Experiment).where(Experiment.campaign_id==cid))
    if exp:
        binding=await db.get(ExperimentBinding,exp.id)
        if binding:
            for candidate_id,users in exp.arms.items():
                snapshot=binding.data['snapshots'][candidate_id];plan=snapshot.get('plan_data') or {}
                result.append({'available_since':binding.data.get('bound_at'),'planned_contacts':plan.get('strategy',{}).get('contacts_per_user',1),'phase':'pilot','candidate_id':candidate_id,'revision':snapshot['revision'],
                    'customer_ids':users,'window_days':plan.get('strategy',{}).get('ab_experiment',{}).get('window_days',7)})
    return result


@router.post('/campaigns/{cid}/observations')
async def observe(cid:str,data:ObservationInput,db=Depends(session_dep)):
    await lock_campaign(db,cid)
    payload=data.model_dump(mode='json')
    old=await db.get(ExecutionObservation,data.event_id)
    if old:
        original={k:v for k,v in old.data.items() if k!='within_window'}
        if old.campaign_id!=cid or original!=payload:raise HTTPException(409,'复用的回流ID对应不同内容')
        return {'accepted':True,'duplicate':True,'within_window':old.data['within_window']}
    match=next((c for c in await cohorts(db,cid) if (c['phase'],c['candidate_id'],c['revision'])==(data.phase,data.candidate_id,data.revision) and data.customer_id in c['customer_ids']),None)
    if not match:raise HTTPException(422,'用户不属于此方案版本的已交付名单或实验臂')
    if match.get('available_since') and data.occurred_at<datetime.fromisoformat(match['available_since'])-timedelta(minutes=1):raise HTTPException(422,'观察时间不能早于名单交付或实验建立')
    if data.occurred_at>now_utc()+timedelta(minutes=1):raise HTTPException(422,'不接受未来时间的观察记录')
    previous=await db.scalar(select(ExecutionObservation).where(ExecutionObservation.campaign_id==cid,ExecutionObservation.candidate_id==data.candidate_id,ExecutionObservation.revision==data.revision,ExecutionObservation.customer_id==data.customer_id,ExecutionObservation.outcome==data.outcome,ExecutionObservation.contact_number==(data.contact_number or 0)))
    if previous:
        if any(previous.data[k]!=payload[k] for k in ('source','phase','actual_cost_cny','contact_number')):raise HTTPException(409,'同一用户的此类记录已存在，不能覆盖来源或费用')
        return {'accepted':True,'duplicate':True,'within_window':previous.data['within_window']}
    if data.outcome=='delivered' and data.contact_number is None:raise HTTPException(422,'请填写这是该用户第几次实际触达')
    if data.outcome=='converted' and data.contact_number is not None:raise HTTPException(422,'转化记录不填写触达次数')
    delivery_query=select(ExecutionObservation).where(
        ExecutionObservation.campaign_id==cid,ExecutionObservation.candidate_id==data.candidate_id,
        ExecutionObservation.revision==data.revision,ExecutionObservation.customer_id==data.customer_id,
        ExecutionObservation.outcome=='delivered')
    deliveries=list((await db.scalars(delivery_query)).all())
    if any(r.data['source']!=data.source for r in deliveries):raise HTTPException(422,'同一用户的触达与转化必须保持同一记录来源')
    within=True
    if data.outcome=='delivered':
        prior={r.contact_number for r in deliveries}
        if not set(range(1,data.contact_number)).issubset(prior):raise HTTPException(422,'请从第1次起逐次记录触达，不能跳号')
        if deliveries and data.occurred_at<max(datetime.fromisoformat(r.data['occurred_at'].replace('Z','+00:00')) for r in deliveries):raise HTTPException(422,'后续触达不能早于已经记录的触达')
    else:
        if data.actual_cost_cny is not None:raise HTTPException(422,'实际触达费用应记录在送达观察上')
        if not deliveries:raise HTTPException(422,'请先记录该用户的送达观察，再记录转化')
        delivered_at=min(datetime.fromisoformat(r.data['occurred_at'].replace('Z','+00:00')) for r in deliveries)
        if data.occurred_at<delivered_at:raise HTTPException(422,'转化不能早于首次送达')
        within=data.occurred_at<=delivered_at+timedelta(days=match['window_days'])
    payload['within_window']=within
    db.add(ExecutionObservation(id=data.event_id,campaign_id=cid,candidate_id=data.candidate_id,revision=data.revision,customer_id=data.customer_id,outcome=data.outcome,contact_number=data.contact_number or 0,data=payload))
    await flush_record(db)
    await emit(db,cid,'execution.observed',{'candidate_id':data.candidate_id,'revision':data.revision,'outcome':data.outcome,'source':data.source,'within_window':within})
    return {'accepted':True,'duplicate':False,'within_window':within}


class ObservationBatch(InputModel):
    rows: list[ObservationInput] = Field(min_length=1,max_length=100)


@router.post('/campaigns/{cid}/observations/batch')
async def observe_batch(cid:str,data:ObservationBatch,db=Depends(session_dep)):
    await require_campaign(db,cid)
    results=[]
    for row in data.rows:
        try:
            result=await observe(cid,row,db)
            results.append({'event_id':row.event_id,'status':200,**result})
        except HTTPException as exc:
            await db.rollback()
            results.append({'event_id':row.event_id,'status':exc.status_code,'accepted':False,'detail':exc.detail})
    return {'results':results,'accepted':sum(r['accepted'] for r in results),'rejected':sum(not r['accepted'] for r in results),
            'note':'逐条提交；有效记录保留，失败记录不写入。accepted包含幂等重复项。'}


@router.get('/campaigns/{cid}/evaluation')
async def evaluation(cid:str,db=Depends(session_dep)):
    await require_campaign(db,cid)
    reviews=[r.data for r in (await db.scalars(select(OperatorReview).where(OperatorReview.campaign_id==cid).order_by(OperatorReview.created_at))).all()]
    observations=[r.data for r in (await db.scalars(select(ExecutionObservation).where(ExecutionObservation.campaign_id==cid))).all()]
    groups=[]
    for source in ('demo','manual_observation'):
        sources=[r for r in reviews if r['source']==source]
        paired=[r for r in sources if r['baseline_minutes'] is not None]
        records=[r for r in observations if r['source']==source]
        deliveries=[r for r in records if r['outcome']=='delivered']
        conversions=[r for r in records if r['outcome']=='converted' and r['within_window']]
        groups.append({'source':source,'review_count':len(sources),'decisions':{d:sum(r['decision']==d for r in sources) for d in ('adopted','edited','rejected')},
            'paired_time_samples':len(paired),'reported_minutes_difference':round(sum(r['baseline_minutes']-r['minutes_spent'] for r in paired),4) if paired else None,
            'observed_delivered':len({(r['candidate_id'],r['revision'],r['customer_id']) for r in deliveries}),'observed_contacts':len(deliveries),'within_window_converted':len(conversions),'late_conversions':sum(r['outcome']=='converted' and not r['within_window'] for r in records),
            'observed_conversion_rate':len(conversions)/len({(r['candidate_id'],r['revision'],r['customer_id']) for r in deliveries}) if deliveries else None,
            'reported_contact_cost_cny':round(sum(r['actual_cost_cny'] for r in deliveries),4) if deliveries and all(r['actual_cost_cny'] is not None for r in deliveries) else None})
    breakdown=[]
    for source,candidate_id,revision,phase in sorted({(r['source'],r['candidate_id'],r['revision'],r['phase']) for r in observations}):
        rows=[r for r in observations if (r['source'],r['candidate_id'],r['revision'],r['phase'])==(source,candidate_id,revision,phase)]
        delivered=[r for r in rows if r['outcome']=='delivered']
        converted=[r for r in rows if r['outcome']=='converted' and r['within_window']]
        cohort=next((c for c in await cohorts(db,cid) if (c['candidate_id'],c['revision'],c['phase'])==(candidate_id,revision,phase)),None)
        days=cohort['window_days'] if cohort else 7
        users={r['customer_id'] for r in delivered}
        first_delivery={uid:min(datetime.fromisoformat(r['occurred_at'].replace('Z','+00:00')) for r in delivered if r['customer_id']==uid) for uid in users}
        matured=sum(t+timedelta(days=days)<=now_utc() for t in first_delivery.values())
        breakdown.append({'source':source,'candidate_id':candidate_id,'revision':revision,'phase':phase,
            'observed_delivered':len(users),'observed_contacts':len(delivered),'within_window_converted':len(converted),
            'observed_conversion_rate':len(converted)/len(users) if users else None,
            'planned_users':len(cohort['customer_ids']) if cohort else None,
            'matured_deliveries':matured,'followup_complete':bool(users) and matured==len(users),
            'frequency_deviations':sum(sum(r['customer_id']==uid for r in delivered)!=(cohort or {}).get('planned_contacts',1) for uid in users),
            'status':'descriptive_only' if users and matured==len(users) else 'preliminary'})
    decision=await db.get(DecisionBinding,cid)
    artifacts=[]
    for row in (await db.scalars(select(Artifact).where(Artifact.campaign_id==cid))).all():
        binding=await db.get(ArtifactBinding,row.id)
        artifacts.append({'id':row.id,'kind':row.kind,'row_count':row.row_count,'sha256':row.sha256,'binding':binding.data if binding else None})
    arms=await cohorts(db,cid)
    return {'campaign_id':cid,'scenario':'membership_benefits','data_scope':'synthetic_customer_ids','decision':decision.data if decision else None,
        'groups':groups,'breakdown':breakdown,'reviews':reviews,'observations':observations,'cohorts':arms,'artifacts':artifacts,
        'limitations':['人工观察未经外部系统验真，演示数据不作为真实效果证据','分母为已记录送达人数，不是导出名单人数','耗时差值来自成对人工填写，不代表已证明节省工时','不提供因果效果、统计显著性或ROI结论']+(['历史产物缺少版本绑定，不能录入新观察；请创建新计划体验完整闭环'] if any(not a['binding'] for a in artifacts) else [])}
