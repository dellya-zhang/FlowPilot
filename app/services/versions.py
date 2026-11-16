"""Immutable candidate history and exact decision/experiment version bindings."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from fastapi import HTTPException
from sqlalchemy import select
from app.models.entities import CandidatePlan, PlanRevision, DecisionBinding, ExperimentBinding
from app.services.rules import check_marketing
from app.services.strategy_design import CHANNELS


def digest(snapshot):
    return hashlib.sha256(json.dumps(snapshot,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


async def current_snapshot(db, candidate):
    plan = await db.get(CandidatePlan, candidate.id)
    return {'candidate_id':candidate.id, 'revision':plan.data.get('revision',1) if plan else 1,
            'title':candidate.title,'message':candidate.message,'reasoning':candidate.reasoning,
            'score':candidate.score,'plan_data':deepcopy(plan.data) if plan else None}


async def save_revision(db, snapshot, reason):
    key=(snapshot['candidate_id'],snapshot['revision'])
    if not await db.get(PlanRevision,key):
        db.add(PlanRevision(candidate_id=key[0], revision=key[1],snapshot=deepcopy(snapshot),reason=reason))
        await db.flush()


async def checked_snapshot(db, candidate, expected_revision=None):
    snapshot = await current_snapshot(db,candidate)
    if expected_revision is not None and snapshot['revision'] != expected_revision:
        raise HTTPException(409, '方案版本已变化，请刷新后重新确认')
    await save_revision(db,snapshot,'生成方案 / 旧方案版本基线')
    return snapshot


def comparison_contract(snapshots):
    plans = [s.get('plan_data') for s in snapshots]
    if not all(p and p.get('comparison') for p in plans):
        return {'validated':False,'axis':'legacy','note':'历史方案缺少受控比较字段'}
    axes = {p['comparison']['axis'] for p in plans}
    if len(axes)!=1:
        raise HTTPException(422,'A/B方案必须比较同一变量')
    axis=axes.pop()
    def controls(s,p):
        return [p['segment']['dsl'],p['strategy'].get('holdout_ratio',0),p['offer_strategy'],p.get('scenario'),
                p['strategy']['ab_experiment']['test_ratio'],p['strategy']['ab_experiment']['window_days'],
                p['strategy']['channel'] if axis!='channel' else None,
                p['strategy'].get('contacts_per_user',1) if axis!='frequency' else None,
                s['message'] if axis!='message' else None]
    signatures=[json.dumps(controls(s,p),sort_keys=True,ensure_ascii=False) for s,p in zip(snapshots,plans)]
    if len(set(signatures))!=1:
        raise HTTPException(422,'存在混杂变量：请保持权益、人群、观察口径及非比较变量一致')
    values=[s['message'] if axis=='message' else p['strategy']['channel'] if axis=='channel' else p['strategy'].get('contacts_per_user',1) for s,p in zip(snapshots,plans)]
    if len(set(values))!=len(values):
        raise HTTPException(422,'所选方案在比较变量上没有差异')
    return {'validated':True,'axis':axis,'axis_label':plans[0]['comparison']['axis_label'],
            'controls':plans[0]['comparison']['controlled_variables'],'values':values,
            'note':'检查的是方案参数一致性，不代表实际随机试验或真实渠道可达性已经验证'}


async def bind_decision(db, campaign, snapshot, source, reason=None):
    data={'decision_reason':reason,'content_status':'operator_approved_preview','bound_at':datetime.now(timezone.utc).isoformat(),'candidate_id':snapshot['candidate_id'],'revision':snapshot['revision'],'snapshot_sha256':digest(snapshot),'snapshot':deepcopy(snapshot),'source':source}
    old=await db.get(DecisionBinding,campaign.id)
    if old:
        if old.data['snapshot_sha256']!=data['snapshot_sha256']:
            raise HTTPException(409,'已审批版本不能覆盖')
        return old.data
    db.add(DecisionBinding(campaign_id=campaign.id,data=data))
    await db.flush()
    return data


def revised_snapshot(snapshot, edit, budget):
    updated=deepcopy(snapshot); plan=updated.get('plan_data')
    if not plan or not plan.get('comparison'):
        raise HTTPException(409,'历史候选没有结构化策略，请创建新计划后修改')
    for field in ('title','message'):
        if getattr(edit,field) is not None:updated[field]=getattr(edit,field)
    if check_marketing(updated['title']+'\n'+updated['message'],budget) or any(w in updated['message'] for w in ('无法生成','不能协助','无法提供','需要补充合法信息')):
        raise HTTPException(422,'修订内容未通过演示规则检查')
    planning=plan.get('planning_input',{})
    strategy=plan['strategy']
    if edit.channel is not None:
        allowed=planning.get('available_channels') or [strategy['channel']]
        if edit.channel not in allowed:
            raise HTTPException(422,'只能选择计划中确认可用的渠道')
        strategy['channel']=edit.channel
    if edit.contacts_per_user is not None:strategy['contacts_per_user']=edit.contacts_per_user
    unit=planning.get('channel_costs',{}).get(strategy['channel'],planning.get('unit_cost_cny',plan['cost_estimate']['unit_cost_cny']))
    total=round(unit*strategy.get('contacts_per_user',1)*plan['segment']['treatment_size'],4)
    if unit and total>budget:raise HTTPException(422,'修订后的预计触达费用超过预算')
    plan['cost_estimate'].update(unit_cost_cny=unit,contacts_per_user=strategy.get('contacts_per_user',1),total_cny=total if unit else None,within_budget=total<=budget if unit else None)
    if updated==snapshot:raise HTTPException(422,'请实际修改文案、标题、渠道或频次后再保存')
    updated['revision']+=1;plan['revision']=updated['revision']
    plan['content_preview_variants']=[{'title':updated['title'],'copy':updated['message'],'finalized':False}]
    plan['content_source']='operator_revision'
    updated['score']=100 if unit else 80
    plan['strategy']['contact_schedule']=[{'contact':i+1,'day_offset':round(i*(planning.get('window_days',7)-1)/max(strategy.get('contacts_per_user',1)-1,1)),'channel':strategy['channel']} for i in range(strategy.get('contacts_per_user',1))]
    plan['comparison']['value']=CHANNELS[strategy['channel']] if strategy['comparison_axis']=='channel' else str(strategy.get('contacts_per_user',1))+'次触达' if strategy['comparison_axis']=='frequency' else strategy['message_focus']
    return updated
