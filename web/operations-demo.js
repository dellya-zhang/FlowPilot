/* Browser simulation of operator workflow. No business service or model is called. */
(() => {
  const copy=x=>JSON.parse(JSON.stringify(x));
  const axisNames={message:'文案表达',frequency:'触达频次',channel:'触达渠道'},channelNames={manual:'人工触达',sms:'短信',in_app:'站内消息'};
  function normalize(p){return p?{scenario:'membership_benefits',comparison_axis:'message',reference_message:'',success_definition:'用户主动查询权益或联系专属客服',contacts_per_user:1,available_channels:[],channel_costs:{},...p}:null;}
  function questions(p){const q=[];if(!p?.known_benefits?.trim())q.push('请填写已确认的权益或服务。');if(p?.comparison_axis!=='message'&&!p?.reference_message?.trim())q.push('频次/渠道实验需要一份运营确认的统一文案。');if(p?.comparison_axis==='channel'&&new Set(p.available_channels).size<2)q.push('至少确认两个可用渠道。');return q;}
  window.enrichOperationsDemo=(base,c,variant)=>{
    if(!base)return null;
    const p=normalize(c.planning),axis=p.comparison_axis;let channel=p.channel,contacts=p.contacts_per_user;
    if(axis==='frequency')contacts=variant;
    if(axis==='channel')channel=[...new Set(p.available_channels.length?p.available_channels:[p.channel])][variant-1];
    const unit=p.channel_costs[channel]??p.unit_cost_cny,total=Math.round(base.segment.treatment_size*unit*contacts*10000)/10000;
    const value=axis==='message'?['权益说明','使用场景','参与指引'][variant-1]:axis==='frequency'?contacts+'次触达':channelNames[channel];
    base.content_source=axis==='message'?'browser_template':'operator_reference';base.schema_version=2;base.planning_input=p;base.scenario={id:'membership_benefits',name:'会员权益触达',success_definition:p.success_definition};
    Object.assign(base.strategy,{channel,contacts_per_user:contacts,comparison_axis:axis,message_focus:axis==='message'?value:'运营确认的统一文案',rationale:'只改变'+axisNames[axis]+'，固定其他约束，先试点再人工决策。'});
    base.strategy.contact_schedule=Array.from({length:contacts},(_,i)=>({contact:i+1,day_offset:Math.round(i*(p.window_days-1)/Math.max(contacts-1,1)),channel}));
    Object.assign(base.cost_estimate,{unit_cost_cny:unit,contacts_per_user:contacts,total_cny:unit?total:null,within_budget:unit?total<=c.budget:null});
    base.comparison={axis,axis_label:axisNames[axis],value,controlled_variables:['权益','人群','成功定义','观察天数'].concat(axis!=='channel'?['渠道']:[],axis!=='frequency'?['频次']:[],axis!=='message'?['同一确认文案']:[]),hypothesis:value+'可能影响权益咨询率，尚无效果证据。',risks:[contacts>1?'重复触达可能增加打扰与退订风险':'单次触达可能被忽略'],recommendation:'先试点，再由人工决定；没有自动胜出结论'};
    base.decision_basis=[{evidence:p.known_benefits,implication:'权益来自运营输入，尚未外部核验'},{evidence:'每次单价 '+(unit||'未知'),implication:'费用还需乘以计划次数和可触达人群'}];
    return base;
  };
  function snapshot(x){return copy({candidate_id:x.id,revision:x.plan_data?.revision||1,title:x.title,message:x.message,reasoning:x.reasoning,score:x.score,plan_data:x.plan_data||null});}
  function storeVersion(ctx,x,reason){const s=snapshot(x);(ctx.db.revisions[x.id]||=[]).push({revision:s.revision,reason,snapshot:s,created_at:new Date().toISOString()});return s;}
  function comparison(snaps,error){
    if(!snaps.every(s=>s.plan_data?.comparison))return {validated:false,axis:'legacy',note:'历史方案'};
    const plans=snaps.map(s=>s.plan_data),axis=plans[0].comparison.axis;
    if(plans.some(p=>p.comparison.axis!==axis))error(422,'请选择相同比较变量的方案');
    const signatures=snaps.map((s,i)=>{const p=plans[i];return JSON.stringify([p.segment.dsl,p.strategy.holdout_ratio||0,p.offer_strategy,p.scenario,p.strategy.ab_experiment.test_ratio,p.strategy.ab_experiment.window_days,axis!=='channel'?p.strategy.channel:null,axis!=='frequency'?p.strategy.contacts_per_user:null,axis!=='message'?s.message:null]);});
    if(new Set(signatures).size!==1)error(422,'存在混杂变量，请保持非比较变量一致');
    const values=snaps.map((s,i)=>axis==='message'?s.message:axis==='channel'?plans[i].strategy.channel:plans[i].strategy.contacts_per_user);
    if(new Set(values).size!==values.length)error(422,'比较变量没有差异');
    return {validated:true,axis,axis_label:axisNames[axis],controls:plans[0].comparison.controlled_variables,values};
  }
  function getCohorts(db,cid){
    const result=Object.values(db.artifacts).filter(a=>a.campaign_id===cid&&a.binding).map(a=>({available_since:a.binding.exported_at||a.binding.bound_at,planned_contacts:a.binding.snapshot.plan_data?.strategy.contacts_per_user||1,phase:a.kind,candidate_id:a.binding.candidate_id,revision:a.binding.revision,customer_ids:a.users,window_days:a.binding.snapshot.plan_data?.strategy.ab_experiment.window_days||7}));
    const exp=Object.values(db.experiments).find(e=>e.campaign_id===cid);
    if(exp?.binding)for(const [candidate_id,customer_ids] of Object.entries(exp.arms)){const s=exp.binding.snapshots[candidate_id];result.push({available_since:exp.binding.bound_at,planned_contacts:s.plan_data?.strategy.contacts_per_user||1,phase:'pilot',candidate_id,revision:s.revision,customer_ids,window_days:s.plan_data?.strategy.ab_experiment.window_days||7});}
    return result;
  }
  window.dispatchOperationsDemo=async(ctx,url,method,data)=>{
    const {db,save,id,error,response,event,pool,rawPool,demoPlan,hash}=ctx;
    db.revisions||={};db.reviews||={};db.observations||={};
    const parts=url.pathname.split('/').filter(Boolean).slice(2),[resource,cid,action,candidateId,subaction]=parts;
    let c=db.campaigns.find(x=>x.id===cid);
    const findX=xid=>{const x=(db.candidates[c?.id]||[]).find(x=>x.id===xid);if(!x)error(404,'当前任务没有此候选');return x;};
    const checkRevision=(x,expected)=>{if(expected!=null&&expected!==(x.plan_data?.revision||1))error(409,'方案版本已变化，请刷新');};
    const bind=async(s,source)=>({bound_at:new Date().toISOString(),candidate_id:s.candidate_id,revision:s.revision,snapshot:s,snapshot_sha256:await hash(JSON.stringify(s)),source});
    if(resource==='campaigns'&&!cid&&method==='POST'){
      if(!data.brief||data.brief.trim().length<5||data.brief.length>2000||data.budget<100||data.budget>100000)error(422,'请检查目标长度与预算');
      const p=normalize(data.planning);c={id:id(),brief:data.brief.trim(),audience:data.audience,budget:data.budget,status:p&&questions(p).length?'NEEDS_CLARIFICATION':'CREATED',selected_candidate_id:null,planning:p};db.campaigns.push(c);save();return response(c,201);
    }
    if(resource==='campaigns'&&c&&action==='planning'){
      if(method==='GET')return response({input:c.planning||null,questions:c.planning?questions(normalize(c.planning)):[],editable:['CREATED','NEEDS_CLARIFICATION'].includes(c.status),experiment_id:Object.values(db.experiments).find(e=>e.campaign_id===c.id)?.id||null});
      if(method==='PUT'){
        if(!['CREATED','NEEDS_CLARIFICATION'].includes(c.status))error(409,'输入已冻结');const p=normalize(data);
        if(!Number.isFinite(p.pilot_ratio)||p.pilot_ratio<.05||p.pilot_ratio>.3||p.unit_cost_cny<0||p.window_days<3||p.window_days>14)error(422,'策略参数超出范围');
        c.planning=p;c.status=questions(p).length?'NEEDS_CLARIFICATION':'CREATED';save();return response(c);
      }
    }
    if(resource==='campaigns'&&c&&action==='generate'&&method==='POST'){
      if(c.status!=='CREATED')error(409,'当前状态不能生成');
      const rows=pool(c);if(!rows.length)error(422,'人群为空；未调用模型');
      const proposed=c.planning?.comparison_axis==='channel'?[...new Set(c.planning.available_channels)].map((_,i)=>i+1):[1,2,3];if(c.planning&&proposed.every(v=>demoPlan(c,v)?.cost_estimate.within_budget===false))error(422,'预算不足；未调用模型');
      c.audience_snapshot={eligible:copy(rows),holdout:copy(rawPool(c).filter(p=>!rows.some(x=>x.id===p.id)))};
      const p=normalize(c.planning),variants=p?.comparison_axis==='channel'?[...new Set(p.available_channels)].map((_,i)=>i+1):[1,2,3];
      c.status='GENERATING';event(c.id,'generation.queued');save();db.candidates[c.id]=[];
      variants.forEach((variant,i)=>setTimeout(()=>{
        const plan=demoPlan(c,variant);
        if(plan?.cost_estimate.within_budget===false)event(c.id,'candidate.failed',{error:'费用超过预算'});
        else {const mode=plan?.comparison?.value||['权益说明','使用场景','参与指引'][variant-1];const message=p&&p.comparison_axis!=='message'?p.reference_message:(p?.known_benefits||c.brief)+'。从'+mode+'角度了解服务，自愿参与，不增加额外优惠。';
          const x={id:id(),variant,title:mode+' · '+c.audience+' 客群',message,reasoning:'浏览器模拟，仅验证流程。',score:plan?.cost_estimate.total_cny?100:80,segment:c.audience,plan_data:plan};if(plan)plan.content_preview_variants=[{title:x.title,copy:message,finalized:false}];db.candidates[c.id].push(x);storeVersion(ctx,x,'首次生成');event(c.id,'candidate.ready',{variant,candidate_id:x.id});}
        if(i===variants.length-1){c.status=db.candidates[c.id].length?'WAITING_APPROVAL':'FAILED';event(c.id,'generation.finished',{status:c.status});}save();
      },350+i*350));return response({status:c.status},202);
    }
    if(resource==='campaigns'&&c&&action==='candidates'&&candidateId&&subaction==='revisions'){
      const x=findX(candidateId);if(!db.revisions[x.id]?.length)storeVersion(ctx,x,'历史基线');
      if(method==='GET')return response(db.revisions[x.id]);
      if(method==='POST'){
        if(c.status!=='WAITING_APPROVAL'||Object.values(db.experiments).some(e=>e.campaign_id===c.id))error(409,'审批或创建实验后版本已冻结');checkRevision(x,data.expected_revision);
        if(!data.reason||data.reason.trim().length<3)error(422,'请说明修改原因');
        const next=copy(x);for(const k of ['title','message'])if(data[k]!=null)next[k]=data[k].trim();
        if(!next.title||!next.message||next.title.length>150||next.message.length>400||/保证收益|百分百中奖|无需同意|无法生成|不能协助/.test(next.title+next.message))error(422,'修订未通过演示规则');
        if(!next.plan_data)error(409,'历史候选没有策略快照');
        const strategy=next.plan_data.strategy,p=normalize(next.plan_data.planning_input||c.planning);
        if(data.channel){if(!(p.available_channels.length?p.available_channels:[strategy.channel]).includes(data.channel))error(422,'渠道未确认可用');strategy.channel=data.channel;}
        if(data.contacts_per_user!=null){if(!Number.isInteger(data.contacts_per_user)||data.contacts_per_user<1||data.contacts_per_user>3)error(422,'次数范围为1–3');strategy.contacts_per_user=data.contacts_per_user;}
        const unit=p.channel_costs[strategy.channel]??p.unit_cost_cny,total=Math.round(unit*strategy.contacts_per_user*next.plan_data.segment.treatment_size*10000)/10000;if(unit&&total>c.budget)error(422,'修订后费用超过预算');
        Object.assign(next.plan_data.cost_estimate,{unit_cost_cny:unit,contacts_per_user:strategy.contacts_per_user,total_cny:unit?total:null});
        if(JSON.stringify(next)===JSON.stringify(x))error(422,'请实际修改后再保存');
        next.plan_data.content_source='operator_revision';next.score=unit?100:80;strategy.contact_schedule=Array.from({length:strategy.contacts_per_user},(_,i)=>({contact:i+1,day_offset:Math.round(i*(p.window_days-1)/Math.max(strategy.contacts_per_user-1,1)),channel:strategy.channel}));next.plan_data.revision++;next.plan_data.content_preview_variants=[{title:next.title,copy:next.message,finalized:false}];Object.assign(x,next);storeVersion(ctx,x,data.reason);event(c.id,'candidate.revised',{candidate_id:x.id,revision:x.plan_data.revision});save();return response(snapshot(x),201);
      }
    }
    if(resource==='campaigns'&&c&&action==='approve'&&method==='POST'){
      if(c.status!=='WAITING_APPROVAL'||Object.values(db.experiments).some(e=>e.campaign_id===c.id))error(409,'当前阶段不能普通审批');const x=findX(data.candidate_id);checkRevision(x,data.expected_revision);c.status='PILOT_READY';c.selected_candidate_id=x.id;c.decision=await bind(snapshot(x),'approval');c.decision.decision_reason=data.decision_reason||null;c.decision.content_status='operator_approved_preview';event(c.id,'campaign.approved',{candidate_id:x.id,revision:c.decision.revision});save();return response(c);
    }
    if(resource==='experiments'&&!cid&&method==='POST'){
      c=db.campaigns.find(c=>c.id===data.campaign_id);if(!c||c.status!=='WAITING_APPROVAL'||Object.values(db.experiments).some(e=>e.campaign_id===c.id))error(409,'当前任务不能创建实验');
      const ids=data.candidate_ids;if(new Set(ids).size!==2)error(422,'请选择两个候选');const snaps=ids.map(xid=>{const x=findX(xid);checkRevision(x,data.expected_revisions?.[xid]);return snapshot(x);});const contract=comparison(snaps,error);
      const users=pool(c).map(p=>p.id),perArm=data.per_arm||(c.planning?Math.max(1,Math.ceil(users.length*c.planning.pilot_ratio/2)):10);if(users.length<2*perArm)error(422,'人群不足');
      const exp={id:id(),campaign_id:c.id,status:'RUNNING',winner:null,arms:{[ids[0]]:users.slice(0,perArm),[ids[1]]:users.slice(perArm,2*perArm)},feedback:{},binding:{bound_at:new Date().toISOString(),snapshots:Object.fromEntries(snaps.map(s=>[s.candidate_id,s])),comparison:contract}};db.experiments[exp.id]=exp;save();return response(exp,201);
    }
    if(resource==='experiments'&&cid&&action==='promote'&&method==='POST'){
      const e=db.experiments[cid];if(!e)error(404,'实验不存在');const winner=url.searchParams.get('candidate_id');if(!e.arms[winner])error(422,'候选不在实验中');if(e.status!=='RUNNING'&&e.winner!==winner)error(409,'已选定其他胜出方案');c=db.campaigns.find(c=>c.id===e.campaign_id);e.status='PROMOTED';e.winner=winner;c.status='PILOT_EXPORTED';c.selected_candidate_id=winner;const s=e.binding?.snapshots[winner]||snapshot(findX(winner));c.decision=await bind(s,'experiment:'+e.id);c.decision.decision_reason=data?.decision_reason||null;c.decision.content_status='operator_approved_preview';event(c.id,'experiment.promoted',{winner,revision:s.revision});save();return response({winner,status:e.status});
    }
    if(resource==='campaigns'&&c&&action==='reviews'&&method==='POST'){
      if(!['WAITING_APPROVAL','PILOT_READY','PILOT_EXPORTED','COMPLETED'].includes(c.status))error(409,'生成完成后才能评价');
      if(!['demo','manual_observation'].includes(data.source)||data.source!=='demo')error(422,'在线预览只记录演示数据');
      const x=findX(data.candidate_id),versions=db.revisions[x.id]||[];if(!versions.some(r=>r.revision===data.revision)&&data.revision!==(x.plan_data?.revision||1))error(422,'版本不存在');
      if(!['adopted','edited','rejected'].includes(data.decision)||!data.reason||data.reason.trim().length<3||!Number.isFinite(data.minutes_spent)||data.minutes_spent<0||data.minutes_spent>1440||data.baseline_minutes!==null&&(!Number.isFinite(data.baseline_minutes)||data.baseline_minutes<0||data.baseline_minutes>1440))error(422,'请检查评价内容和耗时');
      const old=db.reviews[data.event_id];if(old){if(old.cid!==c.id||JSON.stringify(old.data)!==JSON.stringify(data))error(409,'记录ID冲突');return response({accepted:true,duplicate:true});}
      db.reviews[data.event_id]={cid:c.id,data:copy(data)};event(c.id,'operator.reviewed',{decision:data.decision,source:data.source});save();return response({accepted:true,duplicate:false});
    }
    if(resource==='campaigns'&&c&&action==='observations'&&candidateId==='batch'&&method==='POST'){
      if(!Array.isArray(data.rows)||data.rows.length<1||data.rows.length>100)error(422,'每次需要1–100条记录');
      const results=[];const target=new URL(url);target.pathname=target.pathname.replace(/\/batch$/,'');
      for(const row of data.rows){try{const r=await window.dispatchOperationsDemo(ctx,target,'POST',row);results.push({event_id:row.event_id,status:200,...await r.json()});}catch(e){results.push({event_id:row.event_id,status:e.status||500,accepted:false,detail:e.message});}}
      return response({results,accepted:results.filter(r=>r.accepted).length,rejected:results.filter(r=>!r.accepted).length});
    }
    if(resource==='campaigns'&&c&&action==='observations'&&method==='POST'){
      if(data.source!=='demo')error(422,'在线预览只记录演示数据');
      const cohort=getCohorts(db,c.id).find(a=>a.phase===data.phase&&a.candidate_id===data.candidate_id&&a.revision===data.revision&&a.customer_ids.includes(data.customer_id));if(!cohort)error(422,'用户不属于此版本的名单');
      const time=Date.parse(data.occurred_at);if(cohort.available_since&&time<Date.parse(cohort.available_since)-60000)error(422,'观察不能早于交付或实验');if(!Number.isFinite(time)||time>Date.now()+60000)error(422,'观察时间不合法');
      const old=db.observations[data.event_id];if(old){const previous=copy(old.data);delete previous.within_window;if(old.cid!==c.id||JSON.stringify(previous)!==JSON.stringify(data))error(409,'回流ID冲突');return response({accepted:true,duplicate:true});}
      const rows=Object.values(db.observations).filter(r=>r.cid===c.id).map(r=>r.data);
      const previous=rows.find(r=>r.candidate_id===data.candidate_id&&r.revision===data.revision&&r.customer_id===data.customer_id&&r.outcome===data.outcome&&(r.contact_number||0)===(data.contact_number||0));
      if(previous){if(['phase','source','actual_cost_cny','contact_number'].some(k=>previous[k]!==data[k]))error(409,'已有观察不能覆盖来源或费用');return response({accepted:true,duplicate:true});}
      if(data.outcome==='delivered'&&(!Number.isInteger(data.contact_number)||data.contact_number<1||data.contact_number>10))error(422,'触达序号范围为1–10');
      if(data.outcome==='converted'&&data.contact_number!==null)error(422,'转化不填写次数');
      const deliveries=rows.filter(r=>r.candidate_id===data.candidate_id&&r.revision===data.revision&&r.customer_id===data.customer_id&&r.outcome==='delivered');
      if(deliveries.some(r=>r.source!==data.source))error(422,'同一用户必须保持相同记录来源');
      let within=true;
      if(data.outcome==='converted'){
        if(!deliveries.length)error(422,'请先记录送达');const first=Math.min(...deliveries.map(r=>Date.parse(r.occurred_at)));
        if(time<first)error(422,'转化早于首次送达');within=time<=first+cohort.window_days*86400000;
      }else if(data.outcome==='delivered'){
        for(let n=1;n<data.contact_number;n++)if(!deliveries.some(r=>r.contact_number===n))error(422,'请从第1次起逐次记录，不能跳号');
        if(deliveries.length&&time<Math.max(...deliveries.map(r=>Date.parse(r.occurred_at))))error(422,'后续触达不能早于已记录触达');
      }else error(422,'观察类型不合法');
      if(data.actual_cost_cny!==null&&(!Number.isFinite(data.actual_cost_cny)||data.actual_cost_cny<0||data.outcome!=='delivered'))error(422,'实际费用应为送达记录中的非负数字');
      db.observations[data.event_id]={cid:c.id,data:{...copy(data),within_window:within}};event(c.id,'execution.observed',{outcome:data.outcome,source:data.source});save();return response({accepted:true,duplicate:false,within_window:within});
    }
    if(resource==='campaigns'&&c&&action==='evaluation'&&method==='GET'){
      const reviews=Object.values(db.reviews).filter(r=>r.cid===c.id).map(r=>r.data),observations=Object.values(db.observations).filter(r=>r.cid===c.id).map(r=>r.data),cohorts=getCohorts(db,c.id);
      const groups=['demo','manual_observation'].map(source=>{const rs=reviews.filter(r=>r.source===source),paired=rs.filter(r=>r.baseline_minutes!==null),os=observations.filter(r=>r.source===source),ds=os.filter(r=>r.outcome==='delivered'),cs=os.filter(r=>r.outcome==='converted'&&r.within_window);return {source,review_count:rs.length,decisions:Object.fromEntries(['adopted','edited','rejected'].map(d=>[d,rs.filter(r=>r.decision===d).length])),paired_time_samples:paired.length,reported_minutes_difference:paired.length?paired.reduce((n,r)=>n+r.baseline_minutes-r.minutes_spent,0):null,observed_delivered:new Set(ds.map(r=>r.candidate_id+':'+r.revision+':'+r.customer_id)).size,observed_contacts:ds.length,within_window_converted:cs.length,late_conversions:os.filter(r=>r.outcome==='converted'&&!r.within_window).length,observed_conversion_rate:ds.length?cs.length/new Set(ds.map(r=>r.candidate_id+':'+r.revision+':'+r.customer_id)).size:null,reported_contact_cost_cny:ds.length&&ds.every(r=>r.actual_cost_cny!==null)?ds.reduce((n,r)=>n+r.actual_cost_cny,0):null};});
      const breakdown=cohorts.map(a=>{
        const ds=observations.filter(r=>r.source==='demo'&&r.phase===a.phase&&r.candidate_id===a.candidate_id&&r.revision===a.revision&&r.outcome==='delivered');
        const cs=observations.filter(r=>r.source==='demo'&&r.phase===a.phase&&r.candidate_id===a.candidate_id&&r.revision===a.revision&&r.outcome==='converted'&&r.within_window);
        const users=[...new Set(ds.map(r=>r.customer_id))];const matured=users.filter(uid=>Math.min(...ds.filter(r=>r.customer_id===uid).map(r=>Date.parse(r.occurred_at)))+a.window_days*86400000<=Date.now()).length;
        return {source:'demo',candidate_id:a.candidate_id,revision:a.revision,phase:a.phase,planned_users:a.customer_ids.length,observed_delivered:users.length,observed_contacts:ds.length,within_window_converted:cs.length,observed_conversion_rate:users.length?cs.length/users.length:null,frequency_deviations:users.filter(uid=>ds.filter(r=>r.customer_id===uid).length!==a.planned_contacts).length,matured_deliveries:matured,followup_complete:users.length>0&&matured===users.length};
      }).filter(r=>r.observed_delivered);
      return response({campaign_id:c.id,data_scope:'synthetic_customer_ids',decision:c.decision||null,groups,breakdown,reviews,observations,cohorts,artifacts:Object.values(db.artifacts).filter(a=>a.campaign_id===c.id).map(({csv,...a})=>a),limitations:['人工观察未验真；演示记录不作为真实效果证据','分母为已记录送达人数，不是名单人数','没有成对耗时不推断效率提升；不提供ROI或因果效果结论'].concat(Object.values(db.artifacts).some(a=>a.campaign_id===c.id&&!a.binding)?['历史产物缺少版本绑定，请创建新计划录入新观察']:[])});
    }
    return null;
  };
})();
