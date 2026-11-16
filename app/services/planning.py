"""Rule-based planning and shared, executable audience constraints.

No invented business estimates: counts use the local database, costs use an
operator-supplied unit price. Channels and schedules are handoff metadata.
"""
import hashlib
import math
import random
from sqlalchemy import select
from app.models.entities import Customer, PlanningContext
from app.schemas.contracts import PlanningInput
from app.services.rules import evaluate


class PlanNeedsClarification(ValueError):
    pass


async def planning_input(db, campaign_id):
    context = await db.get(PlanningContext, campaign_id)
    return context.data if context else None


def missing_inputs(data):
    if data.get('_questions'):
        return data['_questions']
    return ['请填写已经确认的权益或服务；无优惠时请明确写“不提供额外优惠”。'] if not data['known_benefits'].strip() else []


def audience_rules(audience, data=None):
    rules = [{'field': 'consent', 'op': 'eq', 'value': True}]
    if audience != 'all':
        rules.append({'field': 'segment', 'op': 'eq', 'value': audience})
    if data and data.get('region', 'all') != 'all':
        rules.append({'field': 'region', 'op': 'eq', 'value': data['region']})
    return rules


async def audience_pool(db, campaign):
    data = await planning_input(db, campaign.id)
    rules = audience_rules(campaign.audience, data)
    rows = list((await db.scalars(select(Customer).order_by(Customer.id))).all())
    eligible = [c for c in rows if all(evaluate(r, {'consent':c.consent, 'segment':c.segment, 'region':c.region}) for r in rules)]
    holdout = []
    if data and data['enable_holdout']:
        shuffled = list(eligible)
        random.Random(int(hashlib.sha256((campaign.id+':holdout').encode()).hexdigest(),16)).shuffle(shuffled)
        holdout = shuffled[:math.floor(len(shuffled)*data['holdout_ratio'])]
        excluded = {c.id for c in holdout}
        eligible = [c for c in eligible if c.id not in excluded]
    return eligible, holdout


def plan_document(state):
    data = state.get('planning') or PlanningInput(known_benefits=state['brief']).model_dump()
    count = state.get('audience_count', 0)
    holdout_count = state.get('holdout_count', 0)
    unit = data['unit_cost_cny']
    cost = round(count*unit, 4)
    focus = ['权益说明', '使用场景', '参与指引'][state['variant']-1]
    return {
        'schema_version': 1, 'revision': 1, 'planning_source': 'deterministic_rules',
        'goal': {**state.get('goal', {}), 'intent': data['intent'], 'description': state['brief'], 'budget_cny': state['budget'], 'success_metric': '人工记录的转化人数 / 试点人数；仅描述性指标'},
        'segment': {'dsl': state.get('segment', {}).get('dsl', {'all': audience_rules(state['audience'], data)}), 'estimated_size': count+holdout_count, 'treatment_size': count, 'holdout_size': holdout_count, 'source': 'local_synthetic_customer_database'},
        'user_layers': state.get('user_layers', []),
        'strategy': {'channel': data['channel'], 'execution_mode': 'handoff_only', 'message_focus': focus,
            'rationale': f'以{focus}形成可比较的表达假设；不据此预测转化提升。',
            'pilot_schedule': [{'round':1, 'ratio':data['pilot_ratio'], 'label':'试点', 'window_hours':data['window_days']*24}, {'round':2, 'ratio':round(1-data['pilot_ratio'],6), 'label':'剩余全量', 'window_hours':data['window_days']*24}],
            'ab_experiment': {'test_ratio':data['pilot_ratio'], 'window_days':data['window_days'], 'window_enforced':False, 'recommended_test_ratio':0.3 if count<100 else 0.2 if count<1000 else 0.1, 'recommended_window_days':{'acquisition':7,'retention':14,'reactivation':7,'education':3}[data['intent']]},
            'holdout_ratio': data['holdout_ratio'] if data['enable_holdout'] else 0},
        'offer_strategy': {'verified_benefits': data['known_benefits'], 'source': 'operator_input', 'additional_incentives': False},
        'cost_estimate': {'unit_cost_cny':unit, 'source':'operator_input' if unit else 'not_provided', 'total_cny':cost if unit else None, 'within_budget':cost <= state['budget'] if unit else None, 'scope':'触达单价 × 可触达人群；不含未知权益成本'},
        'event_contract': {'event':'conversion', 'required_fields':['event_id','customer_id','candidate_id'], 'deduplication':'event_id + experiment/customer', 'attribution':'manual_demo_feedback'},
        'confidence_signals': {'verified_business_facts':False, 'operator_facts_present':bool(data['known_benefits'].strip()), 'audience_count_from_database':True, 'cost_known':unit>0, 'measured_conversion_evidence':False},
        'limitations':['权益来自运营输入，未经外部系统核验', '渠道与投放节奏不执行真实触达', '观察天数为建议参数，当前不自动计时或归因'],
    }
