/* Browser-only interactive preview. It never sends requests to a model or server. */
(() => {
  if (!new URLSearchParams(location.search).has('demo') && !location.hostname.endsWith('.github.io')) return;
  window.FlowPilotDemo = true;
  const key = 'flowpilot-public-demo-v1';
  let db;
  try { db = JSON.parse(localStorage.getItem(key)); } catch (_) { /* reset invalid preview state */ }
  if (!db || db.version !== 1) db = {version:1,campaigns:[],candidates:{},events:{},artifacts:{},experiments:{},sessions:{},next:1};
  const save = () => localStorage.setItem(key, JSON.stringify(db));
  const id = () => crypto.randomUUID().replaceAll('-','');
  const error = (status,message) => { throw {status,message}; };
  const event = (cid,name,payload={}) => (db.events[cid] ||= []).push({id:db.next++,name,payload});
  const people = audience => Array.from({length:500},(_,i)=>({id:i+1,segment:['new','active','dormant'][i%3],region:['north','south','east','west'][i%4]})).filter(p=>p.id%9!==0&&(audience==='all'||p.segment===audience));
  const hash = async text => [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text)))].map(n=>n.toString(16).padStart(2,'0')).join('');
  function rawPool(c){return people(c.audience).filter(p=>!c.planning||c.planning.region==='all'||p.region===c.planning.region);}
  function pool(c){const rows=rawPool(c);return c.planning?.enable_holdout?rows.slice(Math.floor(rows.length*c.planning.holdout_ratio)):rows;}
  function demoPlan(c,variant){
    if(!c.planning)return null;
    const p=c.planning,count=pool(c).length,focus=['权益说明','使用场景','参与指引'][variant-1];
    const rules=[{field:'consent',op:'eq',value:true}];if(c.audience!=='all')rules.push({field:'segment',op:'eq',value:c.audience});if(p.region!=='all')rules.push({field:'region',op:'eq',value:p.region});
    return {schema_version:1,revision:1,planning_source:'browser_simulation',goal:{intent:p.intent,description:c.brief,budget_cny:c.budget},segment:{dsl:{all:rules},estimated_size:rawPool(c).length,treatment_size:count,holdout_size:rawPool(c).length-count},strategy:{channel:p.channel,message_focus:focus,rationale:'比较表达角度，不预测转化效果。',execution_mode:'handoff_only',pilot_schedule:[{round:1,ratio:p.pilot_ratio,label:'试点'},{round:2,ratio:1-p.pilot_ratio,label:'剩余全量'}],ab_experiment:{test_ratio:p.pilot_ratio,window_days:p.window_days,window_enforced:false}},offer_strategy:{verified_benefits:p.known_benefits,source:'operator_input',additional_incentives:false},cost_estimate:{unit_cost_cny:p.unit_cost_cny,source:p.unit_cost_cny?'operator_input':'not_provided',total_cny:p.unit_cost_cny?Math.round(count*p.unit_cost_cny*10000)/10000:null},compliance:{decision:'PASS'},trace:['goal_understanding','segment_selection','strategy_planning','planning_compliance','content_preview','content_compliance','plan_output'],limitations:['浏览器模板模拟，不运行真实 LangGraph 或模型','渠道、观察窗口与节奏只作交接参数，不执行投放']};
  }
  const originalFetch = window.fetch.bind(window);
  const headers = {'Content-Type':'application/json'};
  const response = (data,status=200) => new Response(JSON.stringify(data),{status,headers});
  async function dispatch(url, method, data) {
    const parts=url.pathname.split('/').filter(Boolean).slice(2), [resource,cid,action]=parts;
    if(resource==='health')return response({status:'browser-demo'});
    if(resource==='campaigns') {
      if(!cid&&method==='GET')return response([...db.campaigns].reverse());
      if(!cid&&method==='POST') {
        if(!data.brief?.trim()||data.brief.trim().length<5||data.brief.length>2000||data.budget<100||data.budget>100000)error(422,'请输入5–2000字活动目标，预算100–100000元');
        const c={id:id(),brief:data.brief.trim(),audience:data.audience,budget:data.budget,status:data.planning&&!data.planning.known_benefits.trim()?'NEEDS_CLARIFICATION':'CREATED',selected_candidate_id:null,planning:data.planning||null};
        db.campaigns.push(c);save();return response(c,201);
      }
      const c=db.campaigns.find(c=>c.id===cid);if(!c)error(404,'任务不存在，请重新选择');
      if(!action&&method==='GET')return response(c);
      if(action==='planning'&&method==='GET')return response({input:c.planning||null,questions:c.status==='NEEDS_CLARIFICATION'?['请填写已经确认的权益或服务；无优惠时请明确说明。']:[],editable:['CREATED','NEEDS_CLARIFICATION'].includes(c.status),experiment_id:Object.values(db.experiments).find(e=>e.campaign_id===cid)?.id||null});
      if(action==='planning'&&method==='PUT'){
        if(!['CREATED','NEEDS_CLARIFICATION'].includes(c.status))error(409,'开始生成后策略输入已冻结');
        if(!Number.isFinite(data.pilot_ratio)||data.pilot_ratio<.05||data.pilot_ratio>.3||!Number.isFinite(data.unit_cost_cny)||data.unit_cost_cny<0||data.window_days<3||data.window_days>14)error(422,'请检查试点比例、单价和观察天数');
        c.planning=data;c.status=data.known_benefits.trim()?'CREATED':'NEEDS_CLARIFICATION';save();return response(c);
      }
      if(action==='generate'&&method==='POST') {
        if(c.status!=='CREATED')error(409,'任务已开始生成');
        if(c.planning&&(!pool(c).length||pool(c).length*c.planning.unit_cost_cny>c.budget))error(422,'预算不足或符合条件的人群为空；未调用模型');
        c.status='GENERATING';event(cid,'generation.queued');save();
        if(c.planning&&(!pool(c).length||pool(c).length*c.planning.unit_cost_cny>c.budget)){c.status='FAILED';event(cid,'generation.failed',{error:'预算不足或人群为空'});save();return response({status:c.status},202);}
        [1,2,3].forEach((variant,i)=>setTimeout(()=>{
          const mode=['权益说明','服务场景','自愿参与'][i];
          const x={id:id(),variant,title:mode+' · '+c.audience+' 客群',message:(c.planning?.known_benefits||c.brief)+'。本方案从'+mode+'角度介绍已知信息，不添加未声明的优惠。',reasoning:'浏览器模板模拟；完整后端可接入真实模型。',score:c.planning?.unit_cost_cny?100:80,segment:c.audience,plan_data:demoPlan(c,variant)};
          (db.candidates[cid] ||= []).push(x);event(cid,'candidate.ready',{variant,candidate_id:x.id});
          if(variant===3){c.status='WAITING_APPROVAL';event(cid,'generation.finished',{successes:3});}save();
        },350+i*350));
        return response({status:c.status},202);
      }
      if(action==='candidates')return response(db.candidates[cid]||[]);
      if(action==='events')return response((db.events[cid]||[]).filter(e=>e.id>Number(url.searchParams.get('after')||0)));
      if(action==='artifacts')return response(Object.values(db.artifacts).filter(a=>a.campaign_id===cid).map(({csv,...a})=>a));
      const exp=Object.values(db.experiments).find(e=>e.campaign_id===cid);
      if(action==='approve'&&method==='POST') {
        if(exp||c.status!=='WAITING_APPROVAL')error(409,'当前阶段不能普通审批；实验任务请人工晋级');
        if(!(db.candidates[cid]||[]).some(x=>x.id===data.candidate_id))error(422,'候选不属于当前任务');
        c.selected_candidate_id=data.candidate_id;c.status='PILOT_READY';event(cid,'campaign.approved');save();return response(c);
      }
      if(['pilot','finalize'].includes(action)&&method==='POST') {
        const kind=action==='pilot'?'pilot':'full';
        if(action==='pilot'&&exp)error(409,'实验客群已定义试点，请晋级后全量交付');
        if(action==='pilot'&&!['PILOT_READY','PILOT_EXPORTED','COMPLETED'].includes(c.status))error(409,'请先审批方案');
        if(action==='finalize'&&!['PILOT_EXPORTED','COMPLETED'].includes(c.status))error(409,'请先完成试点或晋级');
        let a=Object.values(db.artifacts).find(a=>a.campaign_id===cid&&a.kind===kind);
        if(!a) {
          let rows=pool(c),pilot=Object.values(db.artifacts).find(a=>a.campaign_id===cid&&a.kind==='pilot');
          if(kind==='pilot')rows=rows.slice(0,c.planning?Math.max(1,Math.ceil(rows.length*c.planning.pilot_ratio)):25);
          else {const exposed=new Set(exp?Object.values(exp.arms).flat():pilot?.users||[]);rows=rows.filter(p=>!exposed.has(p.id));}
          const csv='\ufeffcustomer_id,segment,region\r\n'+rows.map(p=>[p.id,p.segment,p.region].join(',')).join('\r\n');
          a={id:id(),campaign_id:cid,kind,row_count:rows.length,sha256:await hash(csv),csv,users:rows.map(p=>p.id)};db.artifacts[a.id]=a;
        }
        c.status=kind==='pilot'?'PILOT_EXPORTED':'COMPLETED';event(cid,kind==='pilot'?'pilot.exported':'campaign.completed',{rows:a.row_count});save();
        const {csv,...view}=a;return response(view);
      }
    }
    if(resource==='artifacts'&&action==='download') {
      const a=db.artifacts[cid];if(!a)error(404,'文件不存在');
      return new Response(a.csv,{headers:{'Content-Type':'text/csv;charset=utf-8','X-Content-SHA256':a.sha256}});
    }
    if(resource==='experiments') {
      if(!cid&&method==='POST') {
        const c=db.campaigns.find(c=>c.id===data.campaign_id);
        if(!c||c.status!=='WAITING_APPROVAL'||Object.values(db.experiments).some(e=>e.campaign_id===c.id))error(409,'该任务不能新建实验');
        const ids=data.candidate_ids,valid=new Set((db.candidates[c.id]||[]).map(x=>x.id));
        if(new Set(ids).size!==2||ids.some(x=>!valid.has(x)))error(422,'请选择当前任务的两个不同候选');
        const cohort=pool(c),perArm=data.per_arm||(c.planning?Math.max(1,Math.ceil(cohort.length*c.planning.pilot_ratio/2)):10);if(cohort.length<2*perArm)error(422,'符合条件的人群不足');const users=cohort.slice(0,2*perArm).map(p=>p.id),eid=id();
        db.experiments[eid]={id:eid,campaign_id:c.id,status:'RUNNING',winner:null,arms:{[ids[0]]:users.slice(0,perArm),[ids[1]]:users.slice(perArm)},feedback:{}};
        save();return response(db.experiments[eid],201);
      }
      const e=db.experiments[cid];if(!e)error(404,'实验不存在');
      if(!action&&method==='GET')return response({...e,arms:Object.entries(e.arms).map(([candidate_id,users])=>{
        const conversions=Object.values(e.feedback).filter(f=>f.candidate_id===candidate_id).length;
        return {candidate_id,users,conversions,rate:conversions/users.length};}),note:'模拟回流和描述性转化率，不代表真实业务效果或统计显著性。'});
      if(action==='feedback'&&method==='POST') {
        if(e.status!=='RUNNING')error(409,'实验已结束');
        if(!e.arms[data.candidate_id]?.includes(data.customer_id))error(422,'用户不属于该实验臂');
        const old=e.feedback[data.event_id];if(old&&(old.customer_id!==data.customer_id||old.candidate_id!==data.candidate_id))error(409,'重复事件载荷冲突');
        const duplicate=!!old||Object.values(e.feedback).some(f=>f.customer_id===data.customer_id);
        if(!duplicate)e.feedback[data.event_id]=data;save();return response({accepted:true,duplicate});
      }
      if(action==='promote'&&method==='POST') {
        const winner=url.searchParams.get('candidate_id');if(!e.arms[winner])error(422,'候选不在实验中');
        if(e.status!=='RUNNING'&&e.winner!==winner)error(409,'实验已晋级');
        e.status='PROMOTED';e.winner=winner;const c=db.campaigns.find(c=>c.id===e.campaign_id);
        c.selected_candidate_id=winner;c.status='PILOT_EXPORTED';event(c.id,'experiment.promoted',{winner});save();return response({winner,status:e.status});
      }
    }
    if(resource==='support'&&cid==='sessions') {
      const sid=parts[2],verb=parts[3];
      if(!sid&&method==='POST'){const sid=id();db.sessions[sid]={session_id:sid,status:'OPEN',pending:false,messages:[]};save();return response(db.sessions[sid],201);}
      const s=db.sessions[sid];if(!s)error(404,'会话不存在，重置演示后请刷新页面');
      if(verb==='messages'&&method==='GET')return response(s.messages);
      const finish=approved=>{s.pending=false;s.status=approved?'ESCALATED':'OPEN';return approved?'已记录人工审核申请（在线演示，不创建真实工单）。':'已取消申请。';};
      let reply,intent='faq';
      if(verb==='confirm'&&method==='POST'){if(!s.pending)error(409,'没有待确认申请');reply=finish(url.searchParams.get('approved')==='true');intent='refund';}
      else if(verb==='messages'&&method==='POST') {
        const text=(data.content||'').trim();if(!text)error(422,'请输入问题');s.messages.push({role:'user',content:text});
        if(s.pending&&['确认','好的','取消'].includes(text))reply=finish(text!=='取消');
        else if(s.pending)reply='已有待确认申请，请先确认或取消。';
        else if(text.includes('退款')&&!/政策|规则|条件|是什么/.test(text)){s.pending=true;s.status='WAITING_CONFIRMATION';intent='refund';reply='是否确认提交退款人工审核申请？在线演示不执行退款。';}
        else if(text.includes('退款'))reply='退款需提交订单信息并由人工审核，本演示不执行资金操作。';
        else if(/订单|物流/.test(text))reply='演示未连接真实订单和物流系统，不能读取个人订单。';
        else reply='我可以演示会员政策、订单工具路由和退款审核确认。';
      } else error(404,'未支持的客服操作');
      s.messages.push({role:'assistant',content:reply});save();return response({reply,intent,session_status:s.status,needs_confirmation:s.pending,facts:[]});
    }
    error(404,'在线演示未实现该接口，请运行完整后端');
  }
  window.fetch=async(input,options={})=>{
    const url=new URL(typeof input==='string'?input:input.url,location.href);
    if(url.origin!==location.origin||!url.pathname.startsWith('/api/v1/'))return originalFetch(input,options);
    try{return await dispatch(url,options.method||'GET',options.body?JSON.parse(options.body):{});}
    catch(e){return response({detail:e.message||'演示数据异常，请重置演示'},e.status||500);}
  };
  addEventListener('DOMContentLoaded',()=>{
    const banner=document.createElement('section');banner.className='demo-banner';
    const text=document.createElement('span');text.textContent='在线交互预览 · 浏览器模拟后端，数据仅保存在本机，不调用模型、不发送真实营销或退款。';
    const reset=document.createElement('button');reset.textContent='重置演示';reset.onclick=()=>{for(const c of db.campaigns)sessionStorage.removeItem('exp-'+c.id);localStorage.removeItem(key);sessionStorage.removeItem('sid');location.reload();};
    const guide=document.createElement('a');guide.href='https://github.com/dellya-zhang/FlowPilot#使用指南';guide.textContent='完整项目与使用指南 ↗';
    banner.append(text,reset,guide);document.querySelector('header').after(banner);
    const api=document.getElementById('apiLink');api.href='https://github.com/dellya-zhang/FlowPilot#快速启动完整后端';api.textContent='启动完整后端 ↗';
  });
})();
