/* Structured plan forms and presentation. All external content uses textContent. */
function readPlanningInput(){
  const value=id=>document.getElementById(id).value;
  return {intent:value('planIntent'),known_benefits:value('knownBenefits').trim(),channel:value('planChannel'),region:value('planRegion'),unit_cost_cny:Number(value('unitCost')),pilot_ratio:Number(value('pilotRatio'))/100,window_days:Number(value('windowDays')),enable_holdout:document.getElementById('enableHoldout').checked,holdout_ratio:0.1};
}
function fillPlanningInput(data){
  for(const [id,key] of Object.entries({planIntent:'intent',knownBenefits:'known_benefits',planChannel:'channel',planRegion:'region',unitCost:'unit_cost_cny',windowDays:'window_days'}))document.getElementById(id).value=data[key];
  document.getElementById('pilotRatio').value=data.pilot_ratio*100;
  document.getElementById('enableHoldout').checked=data.enable_holdout;
}
function renderPlanningState(c,meta){
  const editable=['CREATED','NEEDS_CLARIFICATION'].includes(c.status);
  document.getElementById('clarifyPlan').hidden=!editable;
  document.getElementById('resumePlan').hidden=c.status!=='CREATED';
  const box=document.getElementById('planningQuestions');box.hidden=!meta.questions.length;box.textContent=meta.questions.join(' ');
}
function renderStrategy(plan,parent){
  const add=(tag,text,root)=>{const n=document.createElement(tag);n.textContent=text;root.append(n);return n;};
  const wrapper=add('section','',parent);wrapper.className='strategy-preview';
  add('h4','结构化策略 · v'+plan.revision,wrapper);
  add('p',plan.strategy.message_focus+' · 可触达 '+plan.segment.treatment_size+' 人 · 对照 '+plan.segment.holdout_size+' 人',wrapper);
  const fields=add('details','',wrapper);add('summary','查看策略字段、权益依据与预算测算',fields);
  const names={education:'权益认知',acquisition:'新客获取',retention:'用户留存',reactivation:'沉睡唤醒',manual:'人工触达',sms:'短信',in_app:'站内消息',consent:'同意触达',segment:'客群',region:'地区',new:'新用户',active:'活跃用户',dormant:'沉睡用户',north:'北区',south:'南区',east:'东区',west:'西区'};
  const row=(title,text)=>{const line=add('p','',fields);add('strong',title+'：',line);add('span',text,line);};
  row('业务目标',names[plan.goal.intent]||plan.goal.intent);
  row('策略假设',plan.strategy.message_focus+' · '+plan.strategy.rationale);
  row('人群条件',plan.segment.dsl.all.map(r=>(names[r.field]||r.field)+' = '+(names[r.value]||(r.value===true?'是':String(r.value)))).join('，'));
  row('样本规模','符合条件 '+plan.segment.estimated_size+' 人 / 可触达 '+plan.segment.treatment_size+' 人 / 不触达对照 '+plan.segment.holdout_size+' 人');
  row('权益依据',plan.offer_strategy.verified_benefits+'（运营输入，待人工核实）');
  row('触达建议',(names[plan.strategy.channel]||plan.strategy.channel)+' · '+plan.strategy.pilot_schedule.map(x=>x.label+' '+Math.round(x.ratio*100)+'%').join(' → '));
  const cost=plan.cost_estimate;
  row('预算测算',cost.total_cny===null?'单价未知，不输出费用预测；预算上限 ¥'+plan.goal.budget_cny:'¥'+cost.unit_cost_cny+' × '+plan.segment.treatment_size+' 人 = ¥'+cost.total_cny+' / 上限 ¥'+plan.goal.budget_cny);
  row('验证计划','观察建议 '+plan.strategy.ab_experiment.window_days+' 天；人工记录转化并晋级，不自动计时或判断显著性');
  row('规则检查',plan.compliance?.decision==='PASS'?'演示规则通过，仍需人工审核':'待完成');
  const details=add('details','',fields);add('summary','执行阶段与实现边界',details);
  const phases={goal_understanding:'目标理解',segment_selection:'人群圈选',strategy_planning:'策略规划',planning_compliance:'预算与人群检查',content_preview:'示例文案',content_compliance:'内容规则检查',plan_output:'计划输出'};
  add('p',(plan.trace||[]).map(x=>phases[x]||x).join(' → '),details);
  for(const text of plan.limitations||[])add('p',text,details);
  add('p','规划来源：规则引擎；文案来源：当前 Provider。完整性评分不是转化概率或策略置信度。',details);
}
addEventListener('DOMContentLoaded',()=>{
  document.getElementById('clarifyPlan').onclick=()=>guard(async()=>{await api('/campaigns/'+cid+'/planning','PUT',readPlanningInput());await refresh();},document.getElementById('clarifyPlan'));
  document.getElementById('resumePlan').onclick=()=>guard(async()=>{await api('/campaigns/'+cid+'/generate','POST');await select(cid);},document.getElementById('resumePlan'));
});
