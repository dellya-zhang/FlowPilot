/* Task-oriented navigation shared by static preview and the Python workspace. */
(() => {
  const byId = id => document.getElementById(id);
  const views = ['marketing', 'support', 'knowledge', 'settings'];
  function showView(view) {
    if (!views.includes(view)) view = 'marketing';
    document.body.dataset.view = view;
    const headings = {marketing:['从运营目标，到可追踪的交付。','填写目标 → 比较候选 → 人工决策 → 试点验证 → 排重交付。'],support:['让咨询与操作，各有清晰的流程。','查看政策、提出申请，再由你确认是否提交演示审核。'],knowledge:['回答有依据，政策可追溯。','检索本地政策，阅读原文引用；此入口不发起业务操作。'],settings:['连接模型，查看调用预算。','本地后端负责模型连接；在线演示使用浏览器模板。']};
    document.querySelector('.page-heading h1').textContent = headings[view][0];
    document.querySelector('.page-heading p').textContent = headings[view][1];
    document.querySelectorAll('[data-view]').forEach(a => {
      a.classList.toggle('nav-active', a.dataset.view === view);
      if (a.dataset.view === view) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });
    const panels = {'.left-column': view === 'marketing', '.main-column': view === 'marketing', '.support-panel': view === 'support', '.knowledge-panel': view === 'knowledge', '.model-panel': view === 'settings', '.privacy-note': view === 'settings'};
    for (const [selector, visible] of Object.entries(panels)) document.querySelector(selector).hidden = !visible;
    document.querySelector('.right-column').hidden = view === 'marketing';
    byId('journeyIntro').hidden = view !== 'marketing';
  }
  window.addEventListener('hashchange', () => showView(location.hash.slice(1)));
  showView(location.hash.slice(1));
  const scenarios = {
    member: ['为新会员介绍积分查询与专属客服权益，不提供额外折扣，强调自愿参与', 'new', '单方案路线：生成后审批一套候选，再导出试点与全量名单。'],
    experiment: ['向沉睡用户介绍积分查询与专属客服服务，比较权益说明与服务场景两种表达，不承诺优惠', 'dormant', 'A/B 路线：生成后不要普通审批，点击“创建双臂试点”，记录反馈后人工选出胜出方案。']
  };
  document.querySelectorAll('[data-scenario]').forEach(b => b.addEventListener('click', () => {
    if (b.dataset.scenario === 'support') { location.hash = 'support'; byId('message').focus(); return; }
    const [brief, audience, hint] = scenarios[b.dataset.scenario];
    byId('brief').value = brief; byId('audience').value = audience;
    byId('brief').dispatchEvent(new Event('input'));
    byId('scenarioHint').textContent = hint;
    document.querySelectorAll('[data-scenario]').forEach(x => x.classList.toggle('chosen', x === b));
    byId('brief').focus();
  }));
  window.addEventListener('campaign-view', ({detail:c}) => {
    const experiment = !!sessionStorage.getItem('exp-' + c.id);
    const steps = {
      CREATED: ['准备生成', '点击创建并生成方案，启动候选生成。'],
      GENERATING: ['正在生成候选', '候选会陆续出现。等待生成完成后，再选择审批或 A/B 路线。'],
      WAITING_APPROVAL: experiment ? ['下一步 · 观察双臂反馈并人工晋级', '在下方实验区记录模拟转化，再选择胜出方案。普通审批已关闭。'] : ['下一步 · 选择决策路线', '直接审批一套候选，或在下方创建 A/B 双臂试点。选择后不能切换路线。'],
      PILOT_READY: ['下一步 · 导出试点名单', '方案已确认。先导出小范围试点，再进行全量排重交付。'],
      PILOT_EXPORTED: ['下一步 · 全量排重交付', experiment ? '胜出方案已确认。全量交付将排除两臂全部试点用户。' : '试点名单已就绪。全量交付将排除已参加试点的用户。'],
      COMPLETED: ['交付完成 · 下载 CSV', '下方产物区可下载名单。可回看执行记录，或在左侧创建新任务。'],
      FAILED: ['生成失败', '展开执行记录查看原因，调整目标后创建新任务。']
    };
    const [title, detail] = steps[c.status] || ['查看当前任务', '请查看任务状态与执行记录。'];
    byId('nextTitle').textContent = title; byId('nextDetail').textContent = detail;
    byId('routeChoice').hidden = c.status !== 'WAITING_APPROVAL' || experiment;
  });
})();
