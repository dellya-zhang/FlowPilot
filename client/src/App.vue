<script setup lang="ts">
import {computed, onMounted,onBeforeUnmount,ref,reactive} from 'vue'
import {client,type Json} from './api'
const token=ref(''),api=client(()=>token.value),view=ref('campaigns'),error=ref(''),busy=ref(false),runtime=ref<Json>({})
const campaigns=ref<Json[]>([]),current=ref<Json|null>(null),candidates=ref<Json[]>([]),events=ref<Json[]>([]),artifacts=ref<Json[]>([]),evaluation=ref<Json|null>(null),experiment=ref<Json|null>(null),questions=ref<string[]>([]),selected=ref<string[]>([])
const search=ref(''),status=ref(''),reason=ref(''),settlement=ref('浏览器演示：提前结束合成反馈观察窗口'),feedbackCustomer=reactive<Record<string,number>>({}),edit=reactive({id:'',title:'',message:'',reason:''})
const form=reactive({brief:'',audience:'dormant',budget:5000,known_benefits:'',min_days_since_purchase:30,min_lifetime_spend_cny:500,channel:'sms',unit_cost_cny:0.1,pilot_ratio:0.1,window_days:7,comparison_axis:'message',reference_message:'',contacts_per_user:1})
const members=ref<Json[]>([]),member=ref(1),sid=ref(''),messages=ref<Json[]>([]),audits=ref<Json[]>([]),tickets=ref<Json[]>([]),pending=ref(false),question=ref(''),useModel=ref(false),lastMode=ref(''),chatBusy=ref(false)
const labels:Record<string,string>={CREATED:'待生成',NEEDS_CLARIFICATION:'待补充',GENERATING:'生成中',WAITING_APPROVAL:'待审批',PILOT_READY:'待交付',PILOT_EXPORTED:'试点完成',COMPLETED:'已交付',FAILED:'失败',RUNNING:'试点观察',FINALIZED:'已结算',PROMOTED:'已晋级'}
const tabs=[['campaigns','活动管理'],['plan','活动详情'],['members','会员画像'],['support','客服工作台'],['consumer','消费者端'],['settings','运行设置']]
const visible=computed(()=>campaigns.value.filter(c=>(!status.value||c.status===status.value)&&c.brief.toLowerCase().includes(search.value.toLowerCase())))
const currentId=computed(()=>current.value?.id||'')
const live=computed(()=>runtime.value.provider!=='mock'&&runtime.value.configured)
let timer:ReturnType<typeof setInterval>|undefined,stream:EventSource|null=null,epoch=0
function route(tab:string){view.value=tab;location.hash=tab}
function routeHash(){const next=location.hash.slice(1);if(tabs.some(x=>x[0]===next))view.value=next}
async function guard(fn:()=>Promise<void>){if(busy.value)return;busy.value=true;error.value='';try{await fn()}catch(e){error.value=e instanceof Error?e.message:String(e)}finally{busy.value=false}}
async function load(){const [c,m,r]=await Promise.all([api.request<Json[]>('/campaigns'),api.request<Json[]>('/retail/members'),api.request('/retail/runtime')]);campaigns.value=c;members.value=m;runtime.value=r;useModel.value=!!live.value}
function planInput(){const {brief,audience,budget,...p}=form;return {...p,available_channels:p.comparison_axis==='channel'?['sms','in_app']:[p.channel],channel_costs:{sms:p.unit_cost_cny,in_app:0.05,manual:p.unit_cost_cny}}}
async function refresh(id=currentId.value){
  if(!id)return
  const n=++epoch,root='/campaigns/'+id
  const [c,x,es,as,p,ev]=await Promise.all([api.request(root),api.request<Json[]>(root+'/candidates'),api.request<Json[]>(root+'/events'),api.request<Json[]>(root+'/artifacts'),api.request(root+'/planning'),api.request(root+'/evaluation')])
  if(n!==epoch||id!==currentId.value)return
  current.value=c;candidates.value=x;events.value=es;artifacts.value=as;questions.value=p.questions||[];evaluation.value=ev
  if(p.experiment_id){const exp=await api.request('/experiments/'+p.experiment_id);if(n===epoch&&id===currentId.value)experiment.value=exp}
  else experiment.value=null
  campaigns.value=campaigns.value.map(t=>t.id===id?c:t)
}
async function open(c:Json){
  stream?.close();current.value=c;candidates.value=[];events.value=[];artifacts.value=[];experiment.value=null;evaluation.value=null;selected.value=[];edit.id='';route('plan')
  const p=await api.request('/campaigns/'+c.id+'/planning');if(p.input)Object.assign(form,p.input,{brief:c.brief,audience:c.audience,budget:c.budget})
  await refresh(c.id)
  if(!token.value){stream=new EventSource('/api/v1/campaigns/'+c.id+'/stream');for(const name of ['candidate.ready','candidate.failed','generation.finished','generation.failed','campaign.completed','experiment.finalized'])stream.addEventListener(name,()=>{if(!busy.value)refresh(c.id).catch(e=>error.value=e.message)})}
}
function newPlan(){stream?.close();epoch++;current.value=null;candidates.value=[];events.value=[];experiment.value=null;questions.value=[];route('plan')}
function example(){Object.assign(form,{brief:'面向近30天未消费且累计消费超过500元的会员介绍积分查询与专属客服，促使会员主动了解权益。',known_benefits:'积分查询、专属客服；不提供额外优惠',audience:'dormant',min_days_since_purchase:30,min_lifetime_spend_cny:500,budget:5000});newPlan()}
async function create(){const c=await api.request('/campaigns','POST',{brief:form.brief,audience:form.audience,budget:form.budget,planning:planInput()});campaigns.value=[c,...campaigns.value];await open(c);if(c.status==='CREATED'){await api.request('/campaigns/'+c.id+'/generate','POST');await refresh()}}
async function resume(){const root='/campaigns/'+currentId.value;await api.request(root+'/planning','PUT',planInput());await api.request(root+'/generate','POST');await refresh()}
async function approve(x:Json){await api.request('/campaigns/'+currentId.value+'/approve','POST',{candidate_id:x.id,expected_revision:x.plan_data?.revision||1,decision_reason:reason.value||null});await refresh()}
function editCandidate(x:Json){Object.assign(edit,{id:x.id,title:x.title,message:x.message,reason:''})}
async function saveEdit(x:Json){await api.request('/campaigns/'+currentId.value+'/candidates/'+x.id+'/revisions','POST',{expected_revision:x.plan_data?.revision||1,title:edit.title,message:edit.message,reason:edit.reason});edit.id='';await refresh()}
async function startExperiment(){await api.request('/experiments','POST',{campaign_id:currentId.value,candidate_ids:selected.value,expected_revisions:Object.fromEntries(candidates.value.filter(x=>selected.value.includes(x.id)).map(x=>[x.id,x.plan_data?.revision||1]))});await refresh()}
async function recordFeedback(arm:Json){const id=Number(feedbackCustomer[arm.candidate_id]||arm.users[0]);await api.request('/experiments/'+experiment.value?.id+'/feedback','POST',{event_id:experiment.value?.id+'-'+id,customer_id:id,candidate_id:arm.candidate_id});await refresh()}
async function settle(){await api.request('/experiments/'+experiment.value?.id+'/finalize','POST',{simulation_reason:settlement.value||null});await refresh()}
async function promote(arm:Json){await api.request('/experiments/'+experiment.value?.id+'/promote?require_finalized=true&candidate_id='+arm.candidate_id,'POST',{decision_reason:reason.value||null});await refresh()}
async function full(){await api.request('/campaigns/'+currentId.value+'/full','POST');await refresh()}
async function startChat(){sid.value=(await api.request('/support/sessions?member_id='+member.value,'POST')).session_id;await chatRefresh()}
async function chatRefresh(){const root='/support/sessions/'+sid.value;const [m,a,s,t]=await Promise.all([api.request<Json[]>(root+'/messages'),api.request<Json[]>(root+'/audit'),api.request(root),api.request<Json[]>('/retail/sessions/'+sid.value+'/tickets')]);messages.value=m;audits.value=a;pending.value=s.needs_confirmation;tickets.value=t;lastMode.value=a.at(-1)?.decision?.mode||''}
async function chat(fn:()=>Promise<void>){if(chatBusy.value)return;chatBusy.value=true;error.value='';try{await fn()}catch(e){error.value=e instanceof Error?e.message:String(e)}finally{chatBusy.value=false}}
async function send(text=question.value){if(!text.trim())return;if(!sid.value)await startChat();await api.request('/support/sessions/'+sid.value+'/messages','POST',{content:text,use_model:useModel.value});question.value='';await chatRefresh();runtime.value=await api.request('/retail/runtime')}
async function confirm(yes:boolean){await api.request('/support/sessions/'+sid.value+'/confirm?approved='+yes,'POST');await chatRefresh()}
function pretty(x:unknown){return JSON.stringify(x,null,2)}
onMounted(()=>{routeHash();window.addEventListener('hashchange',routeHash);guard(load);timer=setInterval(()=>{if(currentId.value&&!busy.value&&view.value==='plan')refresh().catch(e=>error.value=e.message)},2500)})
onBeforeUnmount(()=>{window.removeEventListener('hashchange',routeHash);if(timer)clearInterval(timer);stream?.close()})
</script>

<template>
<div class="shell" :class="{consumer: view==='consumer'}">
  <aside class="side"><a class="brand" href="#campaigns">FlowPilot<span>零售会员运营</span></a><nav><button v-for="t in tabs" :key="t[0]" :class="{active:view===t[0]}" @click="route(t[0])">{{t[1]}}</button></nav><div class="side-foot">本地工作区<br>合成会员与订单</div></aside>
  <main>
    <header class="top"><a v-if="view==='consumer'" href="#support">返回客服工作台</a><div><small>工作台 / {{tabs.find(t=>t[0]===view)?.[1]}}</small><h1>{{tabs.find(t=>t[0]===view)?.[1]}}</h1></div><span class="mode" :class="{live}">{{runtime.provider==='mock'?'离线模板':live?'模型已配置 · '+runtime.model:'模型未配置 / 未连接'}}</span></header>
    <div v-if="error" role="alert" class="error">{{error}}</div>
    <section v-if="view==='campaigns'">
      <div class="stats"><div><small>全部活动</small><strong>{{campaigns.length}}</strong></div><div><small>待审批</small><strong>{{campaigns.filter(c=>c.status==='WAITING_APPROVAL').length}}</strong></div><div><small>已交付</small><strong>{{campaigns.filter(c=>c.status==='COMPLETED').length}}</strong></div></div>
      <div class="panel"><div class="heading"><h2>活动列表</h2><div><button @click="example">填入示例需求</button> <button class="primary" @click="newPlan">新建活动</button></div></div>
      <div class="filters"><input v-model="search" placeholder="搜索活动目标"><select v-model="status"><option value="">全部状态</option><option v-for="(v,k) in labels" :value="k" :key="k">{{v}}</option></select><button @click="guard(load)">刷新</button></div>
      <div class="scroll"><table><thead><tr><th>活动目标</th><th>客群</th><th>预算</th><th>状态</th><th></th></tr></thead><tbody><tr v-for="c in visible" :key="c.id"><td>{{c.brief}}</td><td>{{c.audience}}</td><td>¥{{c.budget}}</td><td><span class="pill">{{labels[c.status]||c.status}}</span></td><td><button @click="guard(()=>open(c))">详情</button></td></tr><tr v-if="!visible.length"><td colspan="5">暂无活动</td></tr></tbody></table></div></div>
    </section>
    <section v-else-if="view==='plan'">
      <div class="panel" v-if="!current||['CREATED','NEEDS_CLARIFICATION'].includes(current.status)">
        <div class="heading"><h2>{{current?'补充营销条件':'创建营销活动'}}</h2><button @click="example">填入召回示例</button></div><p v-for="q in questions" :key="q" class="error">{{q}}</p>
        <form @submit.prevent="guard(current?resume:create)">
          <label>活动目标<textarea v-model="form.brief" required minlength="5" maxlength="2000" :disabled="!!current"/></label>
          <label>已确认权益<textarea v-model="form.known_benefits" required maxlength="1000"/></label>
          <div class="form-grid">
            <label>目标客群<select v-model="form.audience" :disabled="!!current"><option value="all">全部</option><option value="new">新会员</option><option value="active">活跃会员</option><option value="dormant">沉睡会员</option></select></label>
            <label>活动预算（元）<input type="number" v-model.number="form.budget" :disabled="!!current" min="100" max="100000" required></label>
            <label>距最近消费至少（天）<input type="number" v-model.number="form.min_days_since_purchase" min="0" max="3650"></label>
            <label>累计消费至少（元）<input type="number" v-model.number="form.min_lifetime_spend_cny" min="0"></label>
            <label>渠道<select v-model="form.channel"><option value="sms">短信</option><option value="in_app">站内消息</option><option value="manual">人工</option></select></label>
            <label>每次触达单价（元）<input type="number" v-model.number="form.unit_cost_cny" min="0" step="0.01"></label>
            <label>比较变量<select v-model="form.comparison_axis"><option value="message">文案</option><option value="frequency">频次</option><option value="channel">渠道</option></select></label>
            <label>每人触达次数<input type="number" v-model.number="form.contacts_per_user" min="1" max="3"></label>
            <label>试点比例<input type="number" v-model.number="form.pilot_ratio" min=".05" max=".3" step=".05"></label>
            <label>观察天数<input type="number" v-model.number="form.window_days" min="3" max="14"></label>
          </div><label v-if="form.comparison_axis!=='message'">运营确认的统一文案<textarea v-model="form.reference_message" maxlength="400" required/></label>
          <button class="primary" :disabled="busy">{{current?'补充并生成':'创建并生成候选'}}</button>
        </form>
      </div>
      <template v-if="current">
      <div class="panel"><div class="heading"><div><h2>{{current.brief}}</h2><small>{{current.id}} · ¥{{current.budget}}</small></div><span class="pill">{{labels[current.status]}}</span></div><p v-if="current.status==='GENERATING'">模型与规划执行中，候选将逐步出现。</p><p v-if="current.status==='FAILED'" class="error">生成失败，请查看执行事件与本地模型配置；没有使用模板替代真实模型输出。</p></div>
      <div class="panel"><div class="heading"><h2>候选方案</h2><span>{{candidates.length}} 个</span></div>
        <label v-if="current.status==='WAITING_APPROVAL'">审批 / 晋级依据<input v-model="reason" placeholder="记录运营决策依据（可选）"></label>
        <div class="candidate-grid"><article v-for="x in candidates" :key="x.id" class="candidate">
          <div class="heading"><small>候选 {{x.variant}} · v{{x.plan_data?.revision||1}}</small><span class="pill">{{x.plan_data?.content_source||'未知来源'}}</span></div>
          <p class="hint" v-if="x.plan_data?.generation_receipt">模型 {{x.plan_data.generation_receipt.model}} · Token {{x.plan_data.generation_receipt.usage?.total_tokens??"未知"}} · {{x.plan_data.generation_receipt.elapsed_ms}} ms</p><h3>{{x.title}}</h3><p class="copy">{{x.message}}</p><p>{{x.reasoning}}</p>
          <dl><dt>目标人数</dt><dd>{{x.plan_data?.segment?.treatment_size??'—'}}</dd><dt>预计费用</dt><dd>{{x.plan_data?.cost_estimate?.total_cny??'待确认'}}</dd><dt>比较条件</dt><dd>{{x.plan_data?.comparison?.value||'—'}}</dd></dl>
          <details><summary>结构化方案与执行轨迹</summary><pre>{{pretty(x.plan_data)}}</pre></details>
          <div v-if="current.status==='WAITING_APPROVAL'&&!experiment" class="actions"><label class="check"><input type="checkbox" :value="x.id" v-model="selected" :disabled="selected.length>=2&&!selected.includes(x.id)"> A/B 候选</label><button @click="editCandidate(x)">修订</button><button class="primary" :disabled="busy" @click="guard(()=>approve(x))">审批</button></div>
          <div v-if="edit.id===x.id"><label>标题<input v-model="edit.title"></label><label>文案<textarea v-model="edit.message"></textarea></label><label>修改原因<input v-model="edit.reason"></label><button :disabled="busy" @click="guard(()=>saveEdit(x))">保存新版</button></div>
        </article></div><button v-if="current.status==='WAITING_APPROVAL'&&!experiment" class="primary" :disabled="busy||selected.length!==2" @click="guard(startExperiment)">创建 A/B 试点</button>
      </div>
      <div v-if="experiment" class="panel"><div class="heading"><h2>A/B 实验</h2><span class="pill">{{labels[experiment.status]}}</span></div><div class="candidate-grid">
      <article class="candidate" v-for="arm in experiment.arms" :key="arm.candidate_id"><h3>{{candidates.find(x=>x.id===arm.candidate_id)?.title}}</h3><p>{{arm.users.length}} 人 · {{arm.conversions}} 转化 · {{(arm.rate*100).toFixed(1)}}%</p><button v-if="experiment.binding?.pilot_artifacts?.[arm.candidate_id]" @click="guard(()=>api.download('/experiments/'+experiment?.id+'/arms/'+arm.candidate_id+'/download','pilot-'+arm.candidate_id+'.csv'))">下载试点 CSV</button>
      <template v-if="experiment.status==='RUNNING'"><label>合成反馈会员<select v-model.number="feedbackCustomer[arm.candidate_id]"><option v-for="u in arm.users" :key="u" :value="u">{{u}}</option></select></label><button :disabled="busy" @click="guard(()=>recordFeedback(arm))">记录模拟转化</button></template>
      <button v-if="experiment.status==='FINALIZED'" :disabled="busy" @click="guard(()=>promote(arm))">人工选择胜出</button></article></div>
      <template v-if="experiment.status==='RUNNING'"><label>提前结算原因（模拟）<input v-model="settlement"></label><button :disabled="busy" @click="guard(settle)">结算实验</button></template><details v-if="experiment.binding?.finalize_result"><summary>结算结果</summary><pre>{{pretty(experiment.binding.finalize_result)}}</pre></details></div>
      <div class="panel"><div class="heading"><h2>名单交付</h2><button class="primary" :disabled="busy||!['PILOT_READY','PILOT_EXPORTED','COMPLETED'].includes(current.status)" @click="guard(full)">全量交付</button></div><div v-for="a in artifacts" :key="a.id" class="artifact"><div><strong>{{a.kind}} · {{a.row_count}} 人</strong><small>{{a.sha256}}</small></div><button @click="guard(()=>api.download('/artifacts/'+a.id+'/download',a.kind+'.csv'))">下载 CSV</button></div></div>
      <div class="panel"><h2>执行记录</h2><div class="event" v-for="event in events" :key="event.id"><small>#{{event.id}}</small><strong>{{event.name}}</strong><code>{{pretty(event.payload)}}</code></div><details><summary>复盘记录（完整录入可使用旧版工作台）</summary><pre>{{pretty(evaluation)}}</pre></details></div>
      </template>
    </section>
    <section v-else-if="view==='members'" class="panel"><div class="heading"><h2>合成会员数据库</h2><small>显示前 50 条，非真实个人</small></div><div class="scroll"><table><thead><tr><th>ID</th><th>等级</th><th>积分</th><th>最近消费间隔</th><th>累计消费</th></tr></thead><tbody><tr v-for="m in members" :key="m.id"><td>{{m.id}}</td><td>{{m.tier}}</td><td>{{m.points}}</td><td>{{m.days_since_purchase}} 天</td><td>¥{{m.lifetime_spend_cny}}</td></tr></tbody></table></div></section>
    <section v-else-if="view==='support'||view==='consumer'" class="chat-grid"><div class="panel"><div class="heading"><h2>消费者会话</h2><button :disabled="chatBusy" @click="chat(startChat)">新会话</button></div><label>合成会员<select v-model.number="member" :disabled="chatBusy" @change="sid='';messages=[];audits=[];tickets=[];pending=false"><option v-for="m in members" :key="m.id" :value="m.id">会员 {{m.id}} · {{m.tier}}</option></select></label>
    <label class="check"><input type="checkbox" v-model="useModel" :disabled="!live||chatBusy"> 使用已配置模型（会发送合成业务上下文）</label><p class="hint" v-if="lastMode">本轮来源：{{lastMode}}</p>
    <div class="chat"><div v-if="!messages.length" class="bubble">您好，请问需要查询订单、积分或退款政策吗？</div><div v-for="(m,i) in messages" :key="i" :class="['bubble',m.role]">{{m.content}}</div></div>
    <div v-if="pending" class="confirm"><strong>待确认审核操作</strong><button :disabled="chatBusy" @click="chat(()=>confirm(true))">确认</button><button :disabled="chatBusy" @click="chat(()=>confirm(false))">取消</button></div>
    <div class="quick"><button v-for="q in ['我的订单','我的积分','退款政策是什么','查看物流']" :key="q" :disabled="chatBusy" @click="chat(()=>send(q))">{{q}}</button></div><form class="send" @submit.prevent="chat(()=>send())"><input v-model="question" placeholder="请输入问题" maxlength="2000" required><button class="primary" :disabled="chatBusy">{{chatBusy?'处理中':'发送'}}</button></form></div>
    <div v-if="view==='support'" class="panel"><h2>工具调用记录</h2><p class="hint">仅显示实际执行记录；模型不能授权业务写操作。</p><details v-for="a in audits.slice().reverse()" :key="a.id"><summary>#{{a.id}} · {{a.intent}} · {{a.decision?.mode||'确认'}}</summary><pre>{{pretty(a.decision)}}</pre><pre v-if="a.citations?.length">{{pretty(a.citations)}}</pre></details><h2>本地审核工单</h2><div class="artifact" v-for="t in tickets" :key="t.id"><div>{{t.order_id}}<small>{{t.id}}</small></div><span class="pill">{{t.status}}</span></div></div></section>
    <section v-else class="panel"><h2>模型与访问设置</h2><p>API Key 只在本地后端 .env 文件中配置，网页不接收密钥。真实模型是否成功调用，以执行记录、供应商响应 ID、Usage 日志为准。离线模板不产生付费调用。</p><label>可选共享工作区令牌<input v-model="token" type="password" autocomplete="off" placeholder="ACCESS_TOKEN（非模型 API Key）"></label><button @click="guard(load)">刷新连接配置</button><pre>{{pretty(runtime)}}</pre><p><a href="/docs" target="_blank" rel="noopener">API 文档</a> · <a href="/" target="_blank" rel="noopener">旧版完整工作台</a></p></section>
  </main>
</div>
</template>
