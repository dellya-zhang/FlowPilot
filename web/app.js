const $=id=>document.getElementById(id);let cid=null,sid=sessionStorage.getItem('sid'),xs=[],stream=null,exp=null;let refreshSequence=0;let currentCampaign=null;let token='';sessionStorage.removeItem('token');$('token').value=token;
let currentPlanning=null,streamRefreshTimer=null;
function scheduleStreamRefresh(){clearTimeout(streamRefreshTimer);streamRefreshTimer=setTimeout(()=>guard(refresh),150)}
const statuses={NEEDS_CLARIFICATION:'等待补充策略信息',CREATED:'待生成',GENERATING:'生成中',WAITING_APPROVAL:'等待人工审批',PILOT_READY:'可交付试点',PILOT_EXPORTED:'试点已完成',COMPLETED:'全量已交付',FAILED:'生成失败'};
async function api(path,method='GET',body,extraHeaders={}){const r=await fetch('/api/v1'+path,{method,headers:{'Content-Type':'application/json',...extraHeaders,...(token?{'Authorization':'Bearer '+token}:{})},body:body===undefined?undefined:JSON.stringify(body)});const data=await r.json();if(!r.ok){const error=Error(typeof data.detail==='string'?data.detail:JSON.stringify(data));error.status=r.status;throw error}return data}
function el(tag,text,parent){const n=document.createElement(tag);n.textContent=text;parent.append(n);return n}function button(text,parent,fn){const b=el('button',text,parent);b.className='secondary';b.onclick=()=>guard(fn,b);return b}
async function guard(fn,b){$('notice').textContent='';if(b?.dataset.busy)return;if(b)b.dataset.busy='1';const wasDisabled=b?.disabled;if(b)b.disabled=true;try{await fn()}catch(e){$('notice').textContent=e.message||'连接失败，请检查本地服务'}finally{if(b){delete b.dataset.busy;b.disabled=wasDisabled||false;}if(currentCampaign)syncControls(currentCampaign)}}
let campaignRows=[];
async function tasks(){campaignRows=await api('/campaigns');renderTasks()}
function renderTasks(){const rows=campaignRows.filter(c=>c.brief.toLowerCase().includes($('campaignSearch').value.trim().toLowerCase()));$('campaignSummary').textContent='待审批 '+campaignRows.filter(c=>c.status==='WAITING_APPROVAL').length+' · 已交付 '+campaignRows.filter(c=>c.status==='COMPLETED').length;$('taskCount').textContent=rows.length;$('tasks').replaceChildren();if(!rows.length)el('p','还没有计划，创建你的第一项任务。',$('tasks')).className='empty-small';for(const c of rows){const b=button('', $('tasks'),()=>select(c.id));b.className='task'+(c.id===cid?' active':'');el('span',c.brief,b).className='task-title';el('small',statuses[c.status]||c.status,b)}}

$('campaignSearch').addEventListener('input',renderTasks);
async function select(id){cid=id;exp=null;$('experimentView').replaceChildren();if(stream)stream.close();clearTimeout(streamRefreshTimer);await refresh();if(currentPlanning?.input)fillPlanningInput(currentPlanning.input);await renderExperiment();if(!token&&!window.FlowPilotDemo){stream=new EventSource('/api/v1/campaigns/'+id+'/stream');for(const name of ['candidate.ready','candidate.revised','candidate.failed','generation.finished','generation.failed','campaign.approved','pilot.exported','campaign.completed','operator.reviewed','execution.observed'])stream.addEventListener(name,scheduleStreamRefresh)}}
async function refresh(){
  if(!cid)return;
  const viewId=cid,sequence=++refreshSequence;
  const [c,cs,events,arts,planning,evaluation]=await Promise.all([
    api('/campaigns/'+viewId),api('/campaigns/'+viewId+'/candidates'),api('/campaigns/'+viewId+'/events'),
    api('/campaigns/'+viewId+'/artifacts'),api('/campaigns/'+viewId+'/planning'),api('/campaigns/'+viewId+'/evaluation')]);
  if(viewId!==cid||sequence!==refreshSequence)return;
  currentCampaign=c;currentPlanning=planning;
  if(planning.experiment_id)sessionStorage.setItem('exp-'+viewId,planning.experiment_id);
  xs=cs;renderPlanningState(c,planning);renderCandidateList(c,cs);syncControls(c);
  window.dispatchEvent(new CustomEvent('campaign-view',{detail:c}));
  $('status').textContent=(statuses[c.status]||c.status)+' · '+cid;
  $('events').textContent=events.map(e=>e.id+' '+e.name+' '+JSON.stringify(e.payload)).join('\n');
  $('artifacts').replaceChildren();
  for(const a of arts){
    const box=el('div','',$('artifacts'));box.className='artifact-item';
    if(a.binding)el('small','交付版本 v'+a.binding.revision+' · '+a.binding.snapshot.title,box);
    button(a.kind+' · '+a.row_count+' 人 · 下载 CSV',box,async()=>{
      const r=await fetch('/api/v1/artifacts/'+a.id+'/download',{headers:token?{'Authorization':'Bearer '+token}:{}});
      if(!r.ok)throw Error('下载失败');const url=URL.createObjectURL(await r.blob());const link=document.createElement('a');link.href=url;link.download=a.kind+'.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    });
  }
  renderEvaluation(evaluation);await tasks();
}
async function experiment(){const ids=selectedExperimentIds();if(ids.length!==2)throw Error('请选择两个候选加入 A/B 对照');exp=await api('/experiments','POST',{campaign_id:cid,candidate_ids:ids,expected_revisions:Object.fromEntries(xs.filter(x=>ids.includes(x.id)).map(x=>[x.id,x.plan_data?.revision||1]))});sessionStorage.setItem('exp-'+cid,exp.id);await renderExperiment();await refresh()}
async function renderExperiment(){
  const id=exp?.id||sessionStorage.getItem('exp-'+cid);if(!id)return;
  exp=await api('/experiments/'+id);const box=$('experimentView');box.replaceChildren();
  if(exp.binding?.comparison)el('p','比较变量：'+(exp.binding.comparison.axis_label||'历史方案')+'；方案版本已冻结。',box).className='hint';
  if(exp.status==='RUNNING'){
    const reason=labeled(box,'模拟提前结算原因（观察窗口到期后可留空）','textarea','',{maxLength:1000});
    button('结算实验',box,async()=>{await api('/experiments/'+id+'/finalize','POST',{simulation_reason:reason.value.trim()||null});await renderExperiment()});
  }
  const result=exp.binding?.finalize_result;
  if(result)el('p',result.recommended_candidate_id?'推荐：'+(xs.find(x=>x.id===result.recommended_candidate_id)?.title||result.recommended_candidate_id):'两组转化率相同，暂无推荐。',box).className='hint';
  for(const arm of exp.arms){
    const armBox=el('div','',box);armBox.className='experiment-arm';const matched=xs.find(x=>x.id===arm.candidate_id);
    el('h3',matched?.title||('方案 '+arm.candidate_id.slice(0,8)),armBox);
    el('p',arm.users.length+' 位试点用户 · '+arm.conversions+' 位模拟回流 · 比率 '+(100*arm.rate).toFixed(1)+'%',armBox);
    if(exp.status==='RUNNING'){
      button('记录一名模拟转化',armBox,async()=>{await api('/experiments/'+id+'/feedback','POST',{event_id:id+'-'+arm.users[0],candidate_id:arm.candidate_id,customer_id:arm.users[0]});await renderExperiment()});
    }
    if(exp.status==='FINALIZED'){
      const reason=labeled(armBox,'人工晋级依据（可选；未完成观察时说明原因）','textarea','',{maxLength:1000});
      button('人工选为胜出方案',armBox,async()=>{await api('/experiments/'+id+'/promote?candidate_id='+arm.candidate_id+'&require_finalized=true','POST',{decision_reason:reason.value||null});await renderExperiment();await refresh()});
    }
  }
  el('small','上方模拟回流按钮仅演示事件去重；下方复盘报告使用先送达、后转化的独立观察记录，不混用两种分母。',box);
  el('small',exp.note,box);
}
async function chat(){if(!sid){sid=(await api('/support/sessions','POST')).session_id;sessionStorage.setItem('sid',sid)}const result=await api('/support/sessions/'+sid+'/messages','POST',{content:$('message').value});await history();$('confirmBox').hidden=!result.needs_confirmation;$('supportAudit').hidden=!result.decision;$('supportTrace').textContent=result.decision?JSON.stringify(result.decision,null,2):''}
$('newSupport').onclick=()=>guard(async()=>{sid=(await api('/support/sessions','POST')).session_id;sessionStorage.setItem('sid',sid);$('confirmBox').hidden=true;$('supportAudit').hidden=true;await history()});
async function history(){if(!sid)return;const rows=await api('/support/sessions/'+sid+'/messages');$('chatlog').replaceChildren();for(const r of rows)el('p',r.content,$('chatlog')).className=r.role;$('chatlog').scrollTop=$('chatlog').scrollHeight}
$('create').onclick=()=>guard(async()=>{const c=await api('/campaigns','POST',{brief:$('brief').value,audience:$('audience').value,budget:Number($('budget').value),planning:readPlanningInput()});try{if(c.status==='CREATED')await api('/campaigns/'+c.id+'/generate','POST')}finally{await select(c.id)}},$('create'));
for(const [id,fn] of Object.entries({pilot:async()=>{await api('/campaigns/'+cid+'/pilot','POST');await refresh()},finalize:async()=>{await api('/campaigns/'+cid+'/full','POST');await refresh()},experiment,send:chat,confirm:()=>confirm(true),cancel:()=>confirm(false)}))$(id).onclick=()=>guard(fn,$(id));
async function confirm(ok){await api('/support/sessions/'+sid+'/confirm?approved='+ok,'POST');$('confirmBox').hidden=true;await history()}
$('saveToken').onclick=()=>{token=$('token').value;guard(async()=>{await tasks();await loadModelStatus()})};
setInterval(()=>{if(cid&&(token||window.FlowPilotDemo))guard(refresh)},2000);guard(async()=>{await tasks();await loadModelStatus();if(sid){try{await history()}catch(e){if(e.status===404){sid=null;sessionStorage.removeItem('sid')}else throw e}}});

function syncControls(c){
  const isExperiment=!!sessionStorage.getItem('exp-'+c.id);
  $('pilot').disabled=isExperiment||!['PILOT_READY','PILOT_EXPORTED','COMPLETED'].includes(c.status);
  $('finalize').disabled=!['PILOT_READY','PILOT_EXPORTED','COMPLETED'].includes(c.status);
  $('experiment').disabled=isExperiment||c.status!=='WAITING_APPROVAL'||selectedExperimentIds().length!==2;
  $('phaseBadge').textContent=statuses[c.status]||c.status;
  const phases={NEEDS_CLARIFICATION:-1,CREATED:-1,GENERATING:0,WAITING_APPROVAL:1,PILOT_READY:1,PILOT_EXPORTED:2,COMPLETED:3,FAILED:-1};
  for(const step of document.querySelectorAll('[data-step]'))step.classList.toggle('active',Number(step.dataset.step)<=phases[c.status]);
}
function countBrief(){$('briefCount').textContent=$('brief').value.length+' / 2000'}
$('brief').addEventListener('input',countBrief);countBrief();
for(const quick of document.querySelectorAll('[data-prompt]'))quick.onclick=()=>{$('message').value=quick.dataset.prompt;guard(chat,$('send'))};
$('message').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.isComposing){e.preventDefault();guard(chat,$('send'))}});
async function loadModelStatus(){
  if(window.FlowPilotDemo){
    $('environmentBadge').lastChild.textContent=' 在线交互预览';
    $('modelState').textContent='模拟环境';$('modelName').textContent='浏览器模板';$('modelMode').textContent='在线模拟 · 不访问本机';
    $('modelDescription').textContent='想使用真实模型？请在本机启动完整后端。此页面不会扫描或访问你的本地服务。';
    $('testModel').disabled=true;$('refreshModel').hidden=true;
    document.querySelector('.privacy-note details').hidden=true;
    return;
  }
  try{
    const m=await api('/local-model');
    $('modelName').textContent=m.model;$('modelState').textContent=m.configured?'已配置':'未配置';
    $('modelMode').textContent=({mock:'本机模拟 · 无模型费用',deepseek:'本机中转 → DeepSeek 云端',openai:'本机中转 → OpenAI 云端',local:'本机推理 · 回环地址'})[m.provider]||'未知配置';
    $('modelDescription').textContent=m.mode==='cloud-api'?'模型 Key 仅存于本机 .env。连接测试使用固定的虚构需求，仍会发往所选云服务。':m.mode==='local-inference'?'仅连接本机推理服务。测试提示词不经过云模型，环境代理不会用于本机推理。':'当前使用模板生成。可以先验证操作流程，再在本机 .env 切换模型。';
    $('modelBudget').hidden=false;$('modelBudget').textContent=m.audit_available?'调用 '+m.calls_used+' / '+m.calls_limit+' · 保守预算占用 ¥'+m.budget_upper_cny.toFixed(4)+' / ¥'+m.budget_limit_cny:'预算记录不可用，请检查本地文件后重试';
    $('testModel').disabled=!m.configured||!m.audit_available;
  }catch(e){$('modelState').textContent='不可用';$('modelName').textContent='未连接本地后端';$('modelMode').textContent='请从 localhost 页面访问';$('modelDescription').textContent='模型诊断仅允许回环地址与同源页面访问。请按接入指南在本机启动服务。';$('testModel').disabled=true;}
}
$('refreshModel').onclick=()=>guard(loadModelStatus,$('refreshModel'));
$('testModel').onclick=()=>guard(async()=>{
  $('modelResult').hidden=false;$('modelResult').textContent='正在验证结构化输出，请稍候…';
  try{const r=await api('/local-model/test','POST',undefined,{'X-Local-Model-Test':'1'});$('modelResult').textContent='连接成功 · '+r.elapsed_ms+' ms\n'+r.sample.title+'\n'+r.sample.message;}
  catch(e){$('modelResult').textContent=e.message;throw e;}
  finally{await loadModelStatus();}
},$('testModel'));

// A separate read-only knowledge entry never touches pending support actions.
if(window.FlowPilotDemo){
  $('knowledgeAsk').disabled=true;$('knowledgeModel').disabled=true;
  $('knowledgeResult').textContent='知识库检索需本地 Python 后端；在线预览不模拟 RAG 结果。';
}else{
  $('knowledgeAsk').onclick=()=>guard(async()=>{
    const r=await api('/knowledge/answer','POST',{question:$('knowledgeQuestion').value,use_model:$('knowledgeModel').checked});
    $('knowledgeResult').replaceChildren();el('p',r.answer,$('knowledgeResult'));
    for(const [i,c] of r.citations.entries())el('small','['+(i+1)+'] '+c.source+' · '+c.heading+' · '+c.chunk_id,$('knowledgeResult'));
    el('p',(r.abstained?'未命中依据':'已返回原文依据')+' · '+r.mode,$('knowledgeResult')).className='fine-print';
  },$('knowledgeAsk'));
}
