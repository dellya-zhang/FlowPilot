"""Explicit experimental designs, with operator inputs as the sole cost evidence."""
from copy import deepcopy

AXES = {'message':'文案表达', 'frequency':'触达频次', 'channel':'触达渠道'}
CHANNELS = {'manual':'人工触达', 'sms':'短信', 'in_app':'站内消息'}


def variants(data):
    if data and data.get('comparison_axis') == 'channel':
        return list(range(1, len(list(dict.fromkeys(data.get('available_channels') or [data['channel']])))+1))
    return [1, 2, 3]


def enrich_strategy(original, data, variant):
    plan = deepcopy(original)
    axis = data.get('comparison_axis', 'message')
    channel = data['channel']
    contacts = data.get('contacts_per_user', 1)
    if axis == 'frequency':
        contacts = variant
    elif axis == 'channel':
        channel = list(dict.fromkeys(data.get('available_channels') or [channel]))[variant-1]
    unit = data.get('channel_costs', {}).get(channel, data['unit_cost_cny'])
    size = plan['segment']['treatment_size']
    cost = round(size*contacts*unit, 4)
    descriptions = {
        'message': ['权益说明','使用场景','参与指引'][variant-1],
        'frequency': f'{contacts}次触达',
        'channel': CHANNELS[channel],
    }
    value = descriptions[axis]
    plan['schema_version'] = 2
    plan['planning_input'] = deepcopy(data)
    plan['scenario'] = {'id':'membership_benefits', 'name':'会员权益触达', 'success_definition':data.get('success_definition','用户主动查询权益或联系专属客服')}
    plan['strategy'].update(channel=channel, contacts_per_user=contacts, comparison_axis=axis,
        message_focus=value if axis=='message' else '运营确认的统一文案',
        rationale=f'只改变{AXES[axis]}，固定权益、人群条件与观察口径；检验差异，不预设胜出。')
    plan['strategy']['contact_schedule']=[{'contact':i+1,'day_offset':round(i*(data['window_days']-1)/max(contacts-1,1)),'channel':channel} for i in range(contacts)]
    plan['cost_estimate'].update(unit_cost_cny=unit, contacts_per_user=contacts, total_cny=cost if unit else None,
        source='operator_input' if unit else 'not_provided', within_budget=cost<=plan['goal']['budget_cny'] if unit else None,
        scope='每次触达单价 × 计划次数 × 可触达人群；不含未知权益及履约成本')
    plan['confidence_signals']['cost_known']=unit>0
    plan['comparison'] = {'axis':axis,'axis_label':AXES[axis],'value':value,
        'controlled_variables':['权益来源','人群条件','对照比例','成功定义','观察天数']+(['渠道'] if axis!='channel' else [])+(['频次'] if axis!='frequency' else [])+(['同一运营确认文案'] if axis!='message' else []),
        'hypothesis':f'{value}可能影响权益咨询率；需要同一口径的实际记录验证，当前没有效果预测。',
        'risks':(['重复触达可能增加打扰及退订风险；执行前核实渠道许可与频控'] if contacts>1 else ['单次触达可能被忽略；不能据此推断低成本方案效果更好'])+(['不同渠道的送达能力与可达用户可能不同，当前数据无法核验'] if axis=='channel' else []),
        'recommendation':'没有历史效果证据，暂不推荐自动胜出；先做受控试点'}
    plan['decision_basis'] = [
        {'input':'known_benefits','evidence':data['known_benefits'],'implication':'只允许使用运营确认的权益；仍需人工核实事实'},
        {'input':'audience_snapshot','evidence':f'{size}名可触达合成用户','implication':'基于固定人群，后续撤回同意者仍会被排除'},
        {'input':'unit_cost_cny','evidence':str(unit) if unit else '未知','implication':'有单价时测算费用；没有单价时不输出收益或成本承诺'},
        {'input':'comparison_axis','evidence':AXES[axis],'implication':'只改变一个主要变量，避免混合解释'},
    ]
    plan['limitations'] = ['权益与单价来自运营填写，未经业务系统核验','渠道及频次是交接要求，不发送真实消息','人群为合成数据，不能证明真实渠道可达性','观察窗口用于人工回流口径，不自动做统计推断']
    return plan
