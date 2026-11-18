from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from app.agents.provider import draft
from app.core.config import get_settings
from app.services.planning import plan_document, audience_rules, PlanNeedsClarification
from app.services.rules import check_marketing


class MarketingState(TypedDict, total=False):
    brief: str
    audience: str
    budget: int
    variant: int
    goal: dict
    segment: dict
    planning: dict | None
    user_layers: list[dict]
    audience_count: int
    holdout_count: int
    content_source: str
    outcome: str
    title: str
    message: str
    reasoning: str
    score: int
    plan_data: dict
    trace: list[str]
    compliance_retry: int
    violations: list[str]


async def understand_goal(state):
    return {'goal':{'description':state['brief'], 'budget_cny':state['budget'], 'intent':(state.get('planning') or {}).get('intent','education')}, 'trace':['goal_understanding']}


async def select_segment(state):
    return {'segment':{'dsl':{'all':audience_rules(state['audience'], state.get('planning'))}, 'treatment_size':state.get('audience_count',0)}, 'trace':state['trace']+['segment_selection']}


async def plan_strategy(state):
    return {'plan_data':plan_document(state), 'trace':state['trace']+['strategy_planning']}


async def planning_compliance(state):
    plan = state['plan_data']
    if plan['cost_estimate']['within_budget'] is False:
        raise ValueError('Estimated contact cost exceeds budget')
    if state.get('planning') and plan['segment']['treatment_size'] == 0:
        raise ValueError('No consenting audience matches the plan')
    return {'trace':state['trace']+['planning_compliance']}


async def executor(state):
    plan = state['plan_data']
    if plan['strategy']['comparison_axis'] != 'message':
        return {'content_source':'operator_reference','outcome':'draft','title':plan['comparison']['axis_label']+' · '+plan['comparison']['value'],
                'message':plan['planning_input']['reference_message'], 'reasoning':'固定运营确认文案，仅比较'+plan['comparison']['axis_label'],
                'trace':state['trace']+['content_preview']}
    brief=state['brief']
    if state.get('violations'):
        brief += "\n上次文案合规未通过，请移除违规内容；不要增加任何未确认权益。检查类别：" + ",".join(state['violations'])
    output = await draft(brief, state['audience'], state['variant'],
                         budget=state['budget'], plan=state['plan_data'])
    return {**output, 'content_source':get_settings().ai_provider, 'trace':state['trace']+['content_preview']}


async def content_compliance(state):
    # Conservative refusal classification. Does not claim general semantic validation.
    if state.get('outcome') == 'clarification':
        raise PlanNeedsClarification('补充权益适用条件、渠道限制和目标后重试')
    if state.get('outcome', 'draft') != 'draft':
        raise ValueError('Only draft output can become a candidate')
    if any(word in state['message'] for word in ('无法生成', '不能协助', '无法提供', '需要补充合法信息')):
        raise ValueError('Refusal is not an executable marketing plan')
    issues=check_marketing(state['title']+'\n'+state['message'],state['budget'])
    if issues:
        retries=state.get('compliance_retry',0)
        if state.get('content_source')=='operator_reference' or retries>=2:
            raise ValueError('Content blocked by demo policy')
        return {'violations':issues,'compliance_retry':retries+1,'trace':state['trace']+['compliance_retry']}
    return {'violations':[],'trace':state['trace']+['content_compliance']}


async def critic(state):
    if not state['message'].strip():
        raise ValueError('Empty campaign content')
    plan = state['plan_data']
    plan['content_preview_variants'] = [{'title':state['title'], 'copy':state['message'], 'finalized':False}]
    plan['compliance'] = {'decision':'PASS', 'checks':['budget_ceiling','estimated_contact_cost','consent_audience','prohibited_claims','limited_refusal_patterns'], 'scope':'演示规则通过；不代表法律合规或业务事实审核'}
    plan['content_source']=state.get('content_source','configured_provider')
    plan['trace'] = state['trace']+['plan_output']
    # Completeness rubric, never interpreted as confidence or conversion probability.
    return {'score':sum([20,20,20,20,20 if plan['cost_estimate']['source']=='operator_input' else 0]), 'plan_data':plan}


def build_graph():
    builder = StateGraph(MarketingState)
    nodes = [('goal_understanding',understand_goal), ('segment_selection',select_segment),
             ('strategy_planning',plan_strategy), ('planning_compliance',planning_compliance),
             ('content_preview',executor), ('content_compliance',content_compliance), ('plan_output',critic)]
    previous = START
    for name, fn in nodes:
        builder.add_node(name, fn)
        if previous != 'content_compliance':builder.add_edge(previous,name)
        previous=name
    builder.add_conditional_edges('content_compliance', lambda s: 'content_preview' if s.get('violations') else 'plan_output', ['content_preview','plan_output'])
    builder.add_edge(previous,END)
    return builder.compile()


marketing_graph = build_graph()
