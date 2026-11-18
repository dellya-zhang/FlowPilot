/* Read-only case presentation. Never creates tasks, approves, or calls a model. */
(() => {
  const host = document.getElementById('projectOverview');
  host.innerHTML = `
    <div class="overview-lead"><div><h2>会员营销与客服流程</h2><p>FlowPilot 面向营销运营与客服：前者把活动目标推进到名单交付，后者把政策咨询与需要确认的操作分开处理。</p><div class="overview-actions"><a class="primary overview-link" href="#marketing">进入工作台，亲自运行 ↗</a><a class="secondary overview-link" href="https://github.com/dellya-zhang/FlowPilot#核心实现与源码" target="_blank" rel="noopener noreferrer">查看后端实现</a></div></div><aside><strong>这个项目交付了什么？</strong><p>一个前后端工作台<br>一套可恢复的 Agent 编排后端<br>带版本的名单与可追溯的执行记录</p><span class="subtle-tag">个人独立演示工程 · 合成用户</span></aside></div>
    <div class="case-heading"><div><h2>业务案例</h2></div><div class="case-tabs" role="group" aria-label="案例选择"><button id="caseMarketing" class="primary" aria-pressed="true">营销案例</button><button id="caseSupport" class="secondary" aria-pressed="false">客服案例</button></div></div>
    <p class="case-source">以下是实际运行浏览器模板后保存的案例快照，不调用模型，也不是业务成效数据。切换步骤只阅读快照，不执行审批。</p>
    <div id="caseSteps" class="case-steps" role="group" aria-label="案例步骤"></div>
    <section class="case-stage" aria-live="polite"><div id="caseNarrative"></div><div id="caseEvidence" class="case-evidence"></div></section>
    <details class="implementation-map"><summary>从页面行为，找到我实现的后端机制与源码</summary><div class="implementation-grid">
      <article><h3>方案逐个出现</h3><p>LangGraph 工作流、有界并发与 SSE 事件，把生成进度呈现给运营。</p><a href="https://github.com/dellya-zhang/FlowPilot/blob/main/app/services/planning.py" target="_blank" rel="noopener noreferrer">规划工作流 ↗</a> · <a href="https://github.com/dellya-zhang/FlowPilot/blob/main/app/services/campaigns.py" target="_blank" rel="noopener noreferrer">任务编排 ↗</a></article>
      <article><h3>审批后按版本交付</h3><p>审批快照、唯一约束与状态条件更新，保留交付版本，避免重复推进。</p><a href="https://github.com/dellya-zhang/FlowPilot/blob/main/app/services/versions.py" target="_blank" rel="noopener noreferrer">版本与交付逻辑 ↗</a></article>
      <article><h3>进程退出后接管</h3><p>SQL 租约、心跳与 owner 校验；这项能力需要运行真实后端验证。</p><a href="https://github.com/dellya-zhang/FlowPilot/blob/main/app/services/leases.py" target="_blank" rel="noopener noreferrer">租约实现 ↗</a> · <a href="https://github.com/dellya-zhang/FlowPilot/blob/main/docs/多进程验证报告.md" target="_blank" rel="noopener noreferrer">验证记录 ↗</a></article>
      <article><h3>政策回答能追溯</h3><p>本地知识库 BM25 检索与可选模型证据筛选；客服状态机单独处理操作确认。</p><a href="https://github.com/dellya-zhang/FlowPilot/blob/main/app/services/knowledge.py" target="_blank" rel="noopener noreferrer">检索实现 ↗</a> · <a href="https://github.com/dellya-zhang/FlowPilot/blob/main/app/services/support.py" target="_blank" rel="noopener noreferrer">客服状态机 ↗</a></article>
    </div><p class="case-source">在线页面只模拟交互。完整后端提供数据库、LangGraph、SSE、知识检索和可选真实模型接入；在线快照不能证明这些后端机制已被执行。</p></details>`;
  let data, mode = 'marketing', stage = 0;
  const steps = {marketing:['提出需求','比较方案','人工确认','检查交付'],support:['咨询政策','提出申请','确认结果']};
  const narrative = document.getElementById('caseNarrative'), evidence = document.getElementById('caseEvidence');
  function node(tag, text, parent, className) {
    const el = document.createElement(tag); el.textContent = text;
    if (className) el.className = className;
    parent.append(el); return el;
  }
  function paragraph(text) { node('p', text, narrative); }
  function card(title, text) {
    const box = node('article', '', evidence, 'evidence-card');
    node('h3', title, box); node('p', text, box); return box;
  }
  function download(label, path, parent) {
    const a = node('a', label, parent, 'text-link'); a.href = path; a.download = ''; return a;
  }
  function render() {
    if (!data) return;
    const nav = document.getElementById('caseSteps'); nav.replaceChildren();
    steps[mode].forEach((text, i) => {
      const b = node('button', `${String(i+1).padStart(2,'0')} / ${text}`, nav, i===stage?'selected':'');
      b.setAttribute('aria-pressed', String(i===stage)); b.onclick = () => {stage=i;render();};
    });
    narrative.replaceChildren(); evidence.replaceChildren();
    node('span', `${mode==='marketing'?'营销':'客服'}案例 · ${stage+1} / ${steps[mode].length}`, narrative, 'eyebrow');
    const c = data.campaign;
    if (mode === 'marketing') {
      const selected = data.candidates.find(x=>x.id===c.selected_candidate_id);
      if (stage===0) {
        node('h2','活动需求',narrative);
        paragraph('场景：新会员还不了解已有权益。运营希望得到几套可比较的表达，再小范围试点，最后交付剩余名单。');
        card('原始需求', c.brief);
        card('明确约束', `人群：新用户；预算：¥${c.budget}；已确认权益：${c.planning.known_benefits}。不允许补造折扣。`);
        card('最终需要的产物','不是只得到一段文案，而是可审核的候选、试点名单、全量排重名单和执行记录。');
      } else if (stage===1) {
        node('h2',`同一份需求，得到 ${data.candidates.length} 套候选。`,narrative);
        paragraph('这次只比较文案角度，其他约束保持一致。运营能检查表达差异、已知权益与潜在风险，再决定采用哪套。');
        for(const candidate of data.candidates) {
          const box = card(candidate.title,candidate.message);
          node('small', '来源：浏览器模板；完整后端可切换模型生成。', box);
        }
      } else if (stage===2) {
        node('h2','审批与版本绑定',narrative);
        paragraph('本案例选择单方案路线：人工选定第一套候选，并将审批绑定到当时的方案版本；之后先交付试点，再交付剩余名单。');
        card('采用的方案',selected.title+'：'+selected.message);
        card('版本绑定',`交付绑定 v${data.artifacts[0].binding.revision}，CSV包含 candidate_id、plan_revision、channel 和 message 等字段。`);
        card('另一条路线','工作台也支持 A/B 双臂：客群互斥、记录模拟反馈，再由人工晋级。本快照没有执行这条路线。');
      } else {
        const pilot=data.artifacts.find(a=>a.kind==='pilot'),full=data.artifacts.find(a=>a.kind==='full');
        const overlap=pilot.users.filter(x=>full.users.includes(x)).length;
        node('h2','名单交付结果',narrative);
        paragraph('下面的数量与文件来自这次案例运行。名单导出不等于已触达，更不等于转化提升；真实渠道尚未接入。');
        let box=card(`${pilot.row_count} 人 / 试点名单`,'先给小范围合成用户，保留所采用的文案与版本。');download('下载本次 pilot.csv ↓','./examples/pilot.csv',box);
        box=card(`${full.row_count} 人 / 剩余全量`,'排除已进入试点的用户，交付剩余名单。');download('下载本次 full.csv ↓','./examples/full.csv',box);
        box=card(`${overlap} 人 / 两份名单交集`,`${pilot.row_count+full.row_count} 位合成用户完成分配。可以下载CSV自行核对 customer_id。`);download('完整案例 JSON ↓','./examples/case.json',box);
        const details=node('details','',evidence,'case-events');node('summary','查看这次运行的阶段事件',details);
        node('pre',data.events.map(e=>e.name+' '+JSON.stringify(e.payload)).join('\n'),details);
      }
    } else {
      const states=[data.support.policy,data.support.pending,data.support.confirmed];
      const titles=['先回答政策，不发起审核。','明确提出退款申请，才进入待确认。','用户确认后，留下演示审核记录。'];
      const descriptions=['用户问“退款政策是什么”。这是咨询，应该得到政策说明，而不是被自动发起退款。','用户说“我要退款”。系统先展示确认与取消选择，等待用户决定。','用户选择确认。当前只记录演示审核状态，没有真实订单接口、工单创建或支付退款。'];
      node('h2',titles[stage],narrative);paragraph(descriptions[stage]);
      const chat=node('div','',evidence,'case-transcript');
      for(const row of states[stage]) {
        const box=node('div','',chat,row.role==='user'?'transcript-user':'transcript-assistant');
        node('strong',row.role==='user'?'用户':'客服',box);node('p',row.content,box);
      }
      if(stage===0) card('规则客服与政策检索各自负责什么？','这里的对话来自规则客服。本地“政策知识库”另提供BM25检索、原文引用与可选模型证据筛选；在线快照没有运行RAG。');
      if(stage===1) card('当前停在哪里？','待用户确认。支持确认或取消；本页面展示的只是案例快照，不会代替你确认新申请。');
    }
    const controls=node('div','',narrative,'case-controls');
    if(stage>0){const back=node('button','← 上一步',controls,'secondary');back.onclick=()=>{stage--;render();};}
    if(stage<steps[mode].length-1){const next=node('button','下一步：'+steps[mode][stage+1]+' →',controls,'primary');next.onclick=()=>{stage++;render();};}
    else {const a=node('a','去工作台亲自体验 ↗',controls,'primary overview-link');a.href=mode==='marketing'?'#marketing':'#support';}
  }
  for(const [id,value] of [['caseMarketing','marketing'],['caseSupport','support']]) {
    document.getElementById(id).onclick=()=>{
      mode=value;stage=0;
      for(const [other,key] of [['caseMarketing','marketing'],['caseSupport','support']]) {
        const active=key===mode,b=document.getElementById(other);
        b.className=active?'primary':'secondary';b.setAttribute('aria-pressed',String(active));
      }
      render();
    };
  }
  fetch('./examples/case.json').then(r=>{if(!r.ok)throw Error('HTTP '+r.status);return r.json();}).then(d=>{data=d;render();}).catch(()=>{
    node('p','案例快照暂时无法加载。仍可进入工作台运行完整流程，或在 GitHub 查看 examples/case.json。',narrative);
  });
})();
