const $=id=>document.getElementById(id);let cid=null,sid=sessionStorage.getItem('sid'),xs=[],stream=null,exp=null;let refreshSequence=0;let currentCampaign=null;let token='';sessionStorage.removeItem('token');$('token').value=token;
const statuses={CREATED:'待生成',GENERATING:'生成中',WAITING_APPROVAL:'等待人工审批',PILOT_READY:'可交付试点',PILOT_EXPORTED:'试点已完成',COMPLETED:'全量已交付',FAILED:'生成失败'};
async function api(path,method='GET',body,extraHeaders={}){const r=await fetch('/api/v1'+path,{method,headers:{'Content-Type':'application/json',...extraHeaders,...(token?{'Authorization':'Bearer '+token}:{})},body:body===undefined?undefined:JSON.stringify(body)});const data=await r.json();if(!r.ok){const error=Error(typeof data.detail==='string'?data.detail:JSON.stringify(data));error.status=r.status;throw error}return data}
function el(tag,text,parent){const n=document.createElement(tag);n.textContent=text;parent.append(n);return n}function button(text,parent,fn){const b=el('button',text,parent);b.className='secondary';b.onclick=()=>guard(fn,b);return b}
async function guard(fn,b){$('notice').textContent='';const wasDisabled=b?.disabled;if(b)b.disabled=true;try{await fn()}catch(e){$('notice').textContent=e.message||'连接失败，请检查本地服务'}finally{if(b)b.disabled=wasDisabled||false;if(currentCampaign)syncControls(currentCampaign)}}
async function tasks(){const rows=await api('/campaigns');$('taskCount').textContent=rows.length;$('tasks').replaceChildren();if(!rows.length)el('p','还没有计划，创建你的第一项任务。',$('tasks')).className='empty-small';for(const c of rows){const b=button('', $('tasks'),()=>select(c.id));b.className='task'+(c.id===cid?' active':'');el('span',c.brief,b).className='task-title';el('small',statuses[c.status]||c.status,b)}}

async function select(id){cid=id;exp=null;$('experimentView').replaceChildren();if(stream)stream.close();await refresh();await renderExperiment();if(!token&&!window.FlowPilotDemo){stream=new EventSource('/api/v1/campaigns/'+id+'/stream');for(const name of ['candidate.ready','candidate.failed','generation.finished','generation.failed','campaign.approved','pilot.exported','campaign.completed'])stream.addEventListener(name,()=>guard(refresh))}}
async function refresh(){if(!cid)return;const viewId=cid,sequence=++refreshSequence;const [c,cs,events,arts]=await Promise.all([api('/campaigns/'+viewId),api('/campaigns/'+viewId+'/candidates'),api('/campaigns/'+viewId+'/events'),api('/campaigns/'+viewId+'/artifacts')]);if(viewId!==cid||sequence!==refreshSequence)return;currentCampaign=c;xs=cs;syncControls(c);$('status').textContent=(statuses[c.status]||c.status)+' · '+cid;$('candidates').replaceChildren();for(const x of cs){const box=el('div','',$('candidates'));box.className='candidate'+(c.selected_candidate_id===x.id?' selected':'');const head=el('div','',box);head.className='candidate-head';el('span','候选方案 / 0'+x.variant,head).className='badge';el('span','规则评分 '+x.score,head).className='score';el('h3',x.title,box);el('p',x.message,box);el('small',x.reasoning,box);const b=button(c.selected_candidate_id===x.id?'已选方案':'审批此方案',box,async()=>{await api('/campaigns/'+cid+'/approve','POST',{candidate_id:x.id});await refresh()});b.className=c.selected_candidate_id===x.id?'primary':'secondary';b.disabled=c.status!=='WAITING_APPROVAL'}$('pilot').disabled=!!sessionStorage.getItem('exp-'+cid)||!['PILOT_READY','PILOT_EXPORTED','COMPLETED'].includes(c.status);$('finalize').disabled=!['PILOT_EXPORTED','COMPLETED'].includes(c.status);$('experiment').disabled=c.status!=='WAITING_APPROVAL'||cs.length<2;$('events').textContent=events.map(e=>e.id+' '+e.name+' '+JSON.stringify(e.payload)).join('\n');$('artifacts').replaceChildren();for(const a of arts){button(a.kind+' · '+a.row_count+' 人 · 下载 CSV',$('artifacts'),async()=>{const r=await fetch('/api/v1/artifacts/'+a.id+'/download',{headers:token?{'Authorization':'Bearer '+token}:{}});if(!r.ok)throw Error('下载失败');const url=URL.createObjectURL(await r.blob());const link=document.createElement('a');link.href=url;link.download=a.kind+'.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)})}await tasks()}
async function experiment(){exp=await api('/experiments','POST',{campaign_id:cid,candidate_ids:xs.slice(0,2).map(x=>x.id)});sessionStorage.setItem('exp-'+cid,exp.id);await renderExperiment()}
async function renderExperiment(){const id=exp?.id||sessionStorage.getItem('exp-'+cid);if(!id)return;exp=await api('/experiments/'+id);const box=$('experimentView');box.replaceChildren();for(const arm of exp.arms){const armBox=el('div','',box);armBox.className='experiment-arm';const matched=xs.find(x=>x.id===arm.candidate_id);el('h3',matched?.title||('方案 '+arm.candidate_id.slice(0,8)),armBox);el('p',arm.users.length+' 位试点用户 · '+arm.conversions+' 位转化 · 转化率 '+(100*arm.rate).toFixed(1)+'%',armBox);if(exp.status==='RUNNING'){button('记录一名模拟转化',armBox,async()=>{await api('/experiments/'+id+'/feedback','POST',{event_id:id+'-'+arm.users[0],candidate_id:arm.candidate_id,customer_id:arm.users[0]});await renderExperiment()});button('人工选为胜出方案',armBox,async()=>{await api('/experiments/'+id+'/promote?candidate_id='+arm.candidate_id,'POST');await renderExperiment();await refresh()})}}el('small',exp.note,box)}
async function chat(){if(!sid){sid=(await api('/support/sessions','POST')).session_id;sessionStorage.setItem('sid',sid)}const result=await api('/support/sessions/'+sid+'/messages','POST',{content:$('message').value});await history();$('confirmBox').hidden=!result.needs_confirmation}
async function history(){if(!sid)return;const rows=await api('/support/sessions/'+sid+'/messages');$('chatlog').replaceChildren();for(const r of rows)el('p',r.content,$('chatlog')).className=r.role;$('chatlog').scrollTop=$('chatlog').scrollHeight}
$('create').onclick=()=>guard(async()=>{const c=await api('/campaigns','POST',{brief:$('brief').value,audience:$('audience').value,budget:Number($('budget').value)});await api('/campaigns/'+c.id+'/generate','POST');await select(c.id)},$('create'));
for(const [id,fn] of Object.entries({pilot:async()=>{await api('/campaigns/'+cid+'/pilot','POST');await refresh()},finalize:async()=>{await api('/campaigns/'+cid+'/finalize','POST');await refresh()},experiment,send:chat,confirm:()=>confirm(true),cancel:()=>confirm(false)}))$(id).onclick=()=>guard(fn,$(id));
async function confirm(ok){await api('/support/sessions/'+sid+'/confirm?approved='+ok,'POST');$('confirmBox').hidden=true;await history()}
$('saveToken').onclick=()=>{token=$('token').value;guard(async()=>{await tasks();await loadModelStatus()})};
setInterval(()=>{if(cid&&(token||window.FlowPilotDemo))guard(refresh)},2000);guard(async()=>{await tasks();await loadModelStatus();if(sid){try{await history()}catch(e){if(e.status===404){sid=null;sessionStorage.removeItem('sid')}else throw e}}});

function syncControls(c){
  const isExperiment=!!sessionStorage.getItem('exp-'+c.id);
  $('pilot').disabled=isExperiment||!['PILOT_READY','PILOT_EXPORTED','COMPLETED'].includes(c.status);
  $('finalize').disabled=!['PILOT_EXPORTED','COMPLETED'].includes(c.status);
  $('experiment').disabled=isExperiment||c.status!=='WAITING_APPROVAL'||xs.length<2;
  $('phaseBadge').textContent=statuses[c.status]||c.status;
  const phases={CREATED:-1,GENERATING:0,WAITING_APPROVAL:1,PILOT_READY:1,PILOT_EXPORTED:2,COMPLETED:3,FAILED:-1};
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
