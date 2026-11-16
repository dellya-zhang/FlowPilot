/* Versioned operator workbench. Text from users/models is never HTML. */
const comparisonSelection=new Set();
let renderedCampaignId=null,evaluationData=null,comparisonSelectionTouched=false;
function ui(tag,text,parent,attrs={}){const n=document.createElement(tag);n.textContent=text;Object.assign(n,attrs);parent.append(n);return n;}
function labeled(parent,title,tag,value,attrs={}){const label=ui('label',title,parent);return ui(tag,'',label,{value,...attrs});}
function choice(parent,title,options,value){const select=labeled(parent,title,'select',value);for(const [v,label] of options)ui('option',label,select,{value:v});select.value=value;return select;}
function action(parent,title,fn){const b=ui('button',title,parent,{className:'secondary',type:'button'});b.onclick=()=>guard(fn,b);return b;}
function selectedExperimentIds(){return [...comparisonSelection].filter(id=>xs.some(x=>x.id===id));}
function renderCandidateList(c,cs){
  if(renderedCampaignId===c.id&&document.querySelector('.candidate-interaction[open]'))return;
  if(renderedCampaignId!==c.id){comparisonSelection.clear();comparisonSelectionTouched=false;renderedCampaignId=c.id;}
  if(!comparisonSelectionTouched){comparisonSelection.clear();cs.slice(0,2).forEach(x=>comparisonSelection.add(x.id));}
  const root=$('candidates');root.replaceChildren();
  if(cs.length){
    const comparison=ui('div','',root,{className:'candidate-comparison'});ui('h3','先比较决策差异，再查看方案细节',comparison);
    const table=ui('table','',comparison);const head=ui('tr','',ui('thead','',table));for(const title of ['方案 / 版本','比较变量','渠道 / 次数','预计触达总费用'])ui('th',title,head);
    const body=ui('tbody','',table);for(const x of cs){const p=x.plan_data;const row=ui('tr','',body);for(const text of [x.title+' · v'+(p?.revision||1),p?.comparison?.value||p?.strategy.message_focus||'历史候选',(p?.strategy.channel||'未知')+' / '+(p?.strategy.contacts_per_user||1)+'次',p?.cost_estimate.total_cny==null?'单价未知':'¥'+p.cost_estimate.total_cny])ui('td',text,row);}
    ui('p','没有历史效果证据，不自动推荐胜出者。频次与渠道实验固定同一份确认文案。',comparison,{className:'fine-print'});
  }
  for(const x of cs){
    const box=ui('section','',root,{className:'candidate'+(c.selected_candidate_id===x.id?' selected':'')});
    const head=ui('div','',box,{className:'candidate-head'});
    ui('span','候选 / 0'+x.variant+' · v'+(x.plan_data?.revision||1),head,{className:'badge'});ui('span','完整性评分 '+x.score,head,{className:'score'});
    ui('h3',x.title,box);ui('p',x.message,box);ui('small',x.reasoning,box);
    if(x.plan_data)renderStrategy(x.plan_data,box);
    const frozen=c.status!=='WAITING_APPROVAL'||!!sessionStorage.getItem('exp-'+c.id);
    const decisionDisclosure=ui('details','',box,{className:'candidate-interaction'});ui('summary','填写本次选择理由（可选）',decisionDisclosure);const decisionReason=labeled(decisionDisclosure,'为何采用此方案？依据和未解决的风险是什么？','textarea','',{maxLength:1000,disabled:frozen});
    const approve=action(box,c.selected_candidate_id===x.id?'已选方案':'审批此方案',async()=>{
      await api('/campaigns/'+c.id+'/approve','POST',{candidate_id:x.id,expected_revision:x.plan_data?.revision||1,decision_reason:decisionReason.value||null});await refresh();
    });approve.dataset.action='approve';approve.className=c.selected_candidate_id===x.id?'primary':'secondary';approve.disabled=frozen;
    const label=ui('label','',box,{className:'comparison-choice'});const select=ui('input','',label,{type:'checkbox',checked:comparisonSelection.has(x.id),disabled:frozen});ui('span','加入 A/B 对照（请选择两个）',label);
    select.onchange=()=>{comparisonSelectionTouched=true;if(select.checked)comparisonSelection.add(x.id);else comparisonSelection.delete(x.id);syncControls(c);};
    if(x.plan_data){
      const editFrozen=frozen||!x.plan_data.comparison;
      const editor=ui('details','',box,{className:'candidate-interaction revision-editor'});ui('summary','修改方案并保存新版本',editor);
      const fields=ui('div','',editor,{className:'form-grid'});
      const title=labeled(fields,'方案标题','input',x.title,{maxLength:150,disabled:editFrozen});
      const message=labeled(fields,'文案（仍需人工核实权益）','textarea',x.message,{maxLength:400,disabled:editFrozen});
      const allowed=x.plan_data.planning_input?.available_channels?.length?x.plan_data.planning_input.available_channels:[x.plan_data.strategy.channel];
      const channel=choice(fields,'计划渠道',allowed.map(v=>[v,{manual:'人工',sms:'短信',in_app:'站内'}[v]]),x.plan_data.strategy.channel);channel.disabled=editFrozen;
      const contacts=labeled(fields,'每人计划次数','input',x.plan_data.strategy.contacts_per_user||1,{type:'number',min:1,max:3,disabled:editFrozen});
      const reason=labeled(fields,'本次修改原因','textarea','',{maxLength:1000,disabled:editFrozen});
      const save=action(editor,'保存为 v'+((x.plan_data.revision||1)+1),async()=>{
        await api('/campaigns/'+c.id+'/candidates/'+x.id+'/revisions','POST',{expected_revision:x.plan_data.revision||1,title:title.value,message:message.value,channel:channel.value,contacts_per_user:Number(contacts.value),reason:reason.value});
        editor.open=false;await refresh();
      });save.disabled=editFrozen;
      ui('p',editFrozen?'此方案已冻结或来自旧版策略结构。历史可查看；下一轮修改请创建新计划。':'保留原版本。创建实验或审批后不可再修改；A/B会检查非比较变量一致。',editor,{className:'fine-print'});
    }
    const history=ui('details','',box,{className:'candidate-interaction revision-history'});ui('summary','查看版本历史',history);const historyBody=ui('div','',history);
    history.addEventListener('toggle',()=>{if(history.open&&!history.dataset.loaded)guard(async()=>{
      const rows=await api('/campaigns/'+c.id+'/candidates/'+x.id+'/revisions');historyBody.replaceChildren();
      for(const r of rows){const item=ui('div','',historyBody,{className:'revision-entry'});ui('strong','v'+r.revision+' · '+r.reason,item);ui('p',r.snapshot.message,item);ui('small',r.created_at||'历史基线',item);}
      history.dataset.loaded='1';
    });});
    const review=ui('details','',box,{className:'candidate-interaction review-editor'});ui('summary','记录采纳情况与修改耗时',review);
    const fields=ui('div','',review,{className:'form-grid'});
    const revision=labeled(fields,'评价版本','input',x.plan_data?.revision||1,{type:'number',min:1,max:x.plan_data?.revision||1});
    const decision=choice(fields,'使用结果',[['adopted','可直接采纳'],['edited','修改后可用'],['rejected','无法使用']], 'adopted');
    const source=choice(fields,'记录来源',[['demo','演示数据'],['manual_observation','人工观察（未验真）']], 'demo');if(window.FlowPilotDemo)source.options[1].disabled=true;
    const minutes=labeled(fields,'本次实际处理分钟','input','',{type:'number',min:0,max:1440,step:.1});
    const baseline=labeled(fields,'可比任务的原耗时（可留空）','input','',{type:'number',min:0,max:1440,step:.1});
    const reason=labeled(fields,'采纳、重写或拒绝的原因','textarea','',{maxLength:1000});
    action(review,'保存方案评价',async()=>{
      if(minutes.value==='')throw Error('请填写本次实际处理分钟');
      await api('/campaigns/'+c.id+'/reviews','POST',{event_id:crypto.randomUUID(),candidate_id:x.id,revision:Number(revision.value),decision:decision.value,source:source.value,minutes_spent:Number(minutes.value),baseline_minutes:baseline.value===''?null:Number(baseline.value),reason:reason.value});review.open=false;await refresh();
    });
  }
}
function renderEvaluation(data){
  evaluationData=data;$('downloadEvaluation').disabled=false;
  if($('observationEditor').open)return;
  const root=$('evaluationSummary');root.replaceChildren();
  if(data.decision)ui('p','已绑定：'+data.decision.snapshot.title+' · v'+data.decision.revision+'；后续产物使用此快照。选择理由：'+(data.decision.decision_reason||'未填写'),root,{className:'binding-summary'});
  const groups=ui('div','',root,{className:'evaluation-groups'});
  for(const group of data.groups){
    const box=ui('div','',groups,{className:'evaluation-group'});ui('h3',group.source==='demo'?'演示数据':'人工观察 · 未验真',box);
    ui('p','评价 '+group.review_count+' 条：直接采纳 '+group.decisions.adopted+' / 修改可用 '+group.decisions.edited+' / 拒绝 '+group.decisions.rejected,box);
    ui('p','记录送达 '+group.observed_delivered+' 人 / '+(group.observed_contacts||0)+' 次触达 · 窗口内转化 '+group.within_window_converted+' 人 · 迟到 '+group.late_conversions+' 人',box);
    ui('p',group.observed_conversion_rate===null?'没有送达观察，不计算转化率':'已记录样本转化率 '+(group.observed_conversion_rate*100).toFixed(1)+'%（描述性）',box);
    ui('p',group.reported_minutes_difference===null?'缺少成对耗时，不推断节省时间':'成对耗时差 '+group.reported_minutes_difference+' 分钟（原方式 − 本次）· '+group.paired_time_samples+' 条人工样本',box);
    ui('p',group.reported_contact_cost_cny===null?'实际费用记录不完整':'已填写的实际触达费用 ¥'+group.reported_contact_cost_cny,box);
  }
  for(const row of data.breakdown||[]){const matched=xs.find(x=>x.id===row.candidate_id);ui('p',(matched?.title||row.candidate_id.slice(0,8))+' · v'+row.revision+' · '+row.phase+' · '+(row.source==='demo'?'演示':'人工观察')+'：送达 '+row.observed_delivered+' / 名单 '+row.planned_users+' 人；频次偏离 '+(row.frequency_deviations||0)+' 人；'+(row.followup_complete?'观察窗口已满，仍仅作描述':'观察窗口未全部结束，结果为初步记录'),root,{className:'fine-print'});}
  for(const limitation of data.limitations)ui('p',limitation,root,{className:'fine-print'});
  const cohorts=$('observationCohort');cohorts.replaceChildren();
  for(const [i,cohort] of data.cohorts.entries()){const x=xs.find(x=>x.id===cohort.candidate_id);ui('option',(x?.title||cohort.candidate_id.slice(0,8))+' · v'+cohort.revision+' · '+cohort.phase,cohorts,{value:String(i)});}
  updateObservationUsers();$('saveObservation').disabled=!data.cohorts.some(c=>c.customer_ids.length);
  if(window.FlowPilotDemo)$('observationSource').querySelector('[value="manual_observation"]').disabled=true;
}
function updateObservationUsers(){
  const select=$('observationUser');select.replaceChildren();
  const cohort=evaluationData?.cohorts[Number($('observationCohort').value)];

  for(const id of cohort?.customer_ids||[])ui('option','用户 '+id,select,{value:String(id)});
  updateContactNumber();
}
function updateContactNumber(){
  const cohort=evaluationData?.cohorts[Number($('observationCohort').value)];
  const receipts=(evaluationData?.observations||[]).filter(r=>cohort&&r.candidate_id===cohort.candidate_id&&r.revision===cohort.revision&&r.phase===cohort.phase&&r.customer_id===Number($('observationUser').value)&&r.outcome==='delivered');
  $('observationContacts').value=Math.min(10,1+Math.max(0,...receipts.map(r=>r.contact_number||1)));
}
addEventListener('DOMContentLoaded',()=>{
  $('observationCohort').onchange=updateObservationUsers;$('observationUser').onchange=updateContactNumber;
  $('observationEditor').addEventListener('toggle',()=>{if(!$('observationEditor').open&&evaluationData)renderEvaluation(evaluationData);});
  $('saveObservation').onclick=()=>guard(async()=>{
    const cohort=evaluationData.cohorts[Number($('observationCohort').value)];
    if(!cohort)throw Error('当前没有可记录观察的已交付名单');
    const time=$('observationTime').value?new Date($('observationTime').value):new Date();
    await api('/campaigns/'+cid+'/observations','POST',{event_id:crypto.randomUUID(),candidate_id:cohort.candidate_id,revision:cohort.revision,customer_id:Number($('observationUser').value),phase:cohort.phase,outcome:$('observationOutcome').value,source:$('observationSource').value,occurred_at:time.toISOString(),contact_number:$('observationOutcome').value==='delivered'?Number($('observationContacts').value):null,actual_cost_cny:$('observationOutcome').value==='delivered'&&$('observationCost').value!==''?Number($('observationCost').value):null});
    $('observationEditor').open=false;await refresh();
  },$('saveObservation'));
  $('saveObservationBatch').onclick=()=>guard(async()=>{
    const cohort=evaluationData?.cohorts[Number($('observationCohort').value)];if(!cohort)throw Error('请先选择已交付名单');
    let input;try{input=JSON.parse($('observationBatch').value)}catch(e){throw Error('请输入合法JSON数组')}
    if(!Array.isArray(input)||input.length<1||input.length>100)throw Error('每次需要1–100条记录');
    const rows=input.map(row=>({event_id:row.event_id||crypto.randomUUID(),candidate_id:cohort.candidate_id,revision:cohort.revision,phase:cohort.phase,source:$('observationSource').value,customer_id:row.customer_id,outcome:row.outcome,occurred_at:row.occurred_at,actual_cost_cny:row.actual_cost_cny??null,contact_number:row.contact_number??null}));
    const result=await api('/campaigns/'+cid+'/observations/batch','POST',{rows});
    $('observationBatchResult').hidden=false;$('observationBatchResult').textContent='接受 '+result.accepted+' 条（含幂等重复） / 拒绝 '+result.rejected+' 条\n'+result.results.map(r=>r.event_id+' · '+(r.accepted?(r.duplicate?'重复，未新增':'已记录'):r.detail)).join('\n');
    $('observationEditor').open=false;await refresh();
  },$('saveObservationBatch'));
  $('downloadEvaluation').onclick=()=>guard(async()=>{
    const data=await api('/campaigns/'+cid+'/evaluation');const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='FlowPilot-evaluation-'+cid+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  },$('downloadEvaluation'));
});
