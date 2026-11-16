# FlowPilot · 智能运营与 Agent 工作台

[![CI](https://github.com/dellya-zhang/FlowPilot/actions/workflows/ci.yml/badge.svg)](https://github.com/dellya-zhang/FlowPilot/actions/workflows/ci.yml)
[![Interactive demo](https://img.shields.io/badge/demo-在线交互预览-94cb65)](https://dellya-zhang.github.io/FlowPilot/)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776ab)](pyproject.toml)

**面向营销运营与客服协作的本地可运行 Agent 工作台。** 输入营销目标，生成候选、人工审批、开展双臂试点，最终下载排重名单；同时提供本地政策知识库检索、引用摘录与敏感操作确认。

**[打开白色交互工作台 →](https://dellya-zhang.github.io/FlowPilot/)** · [用户操作指南](docs/用户操作指南.md) · [架构设计](docs/设计映射与架构.md) · [测试报告](#测试与验证)

> 在线演示在浏览器模拟后端，数据仅保存在访问者本机，不调用模型或真实业务服务。完整 Python 后端、数据库、LangGraph 和可选 DeepSeek 接入包含在本仓库，可按下文启动。

![FlowPilot 在线工作台](docs/在线演示桌面.png)

## 三分钟体验

1. 打开 **[在线交互演示](https://dellya-zhang.github.io/FlowPilot/)**，选择“新会员权益介绍”或“沉睡用户唤醒”。场景只填入示例，不会自动创建任务。
2. 点击 **创建并生成方案**，比较三套候选。页面会随任务阶段提示下一步。
3. 选择一条路线：**单方案审批 → 试点 → 全量交付**，或 **A/B 双臂 → 模拟反馈 → 人工晋级 → 全量交付**。两条路线互斥。
4. 在产物区下载 CSV；从顶部导航切换到 **客服协作**，体验政策咨询和操作确认。

**四个工作区**：营销计划负责候选与名单；客服协作负责咨询与审核确认；政策知识库负责原文依据；模型设置负责本地连接和预算诊断。在线预览不提供真实模型或知识库检索。

## 核心能力

- **目标驱动的方案生成**：按活动目标与人群生成候选，预算执行固定阈值校验，人工决定采用哪一套。
- **两条清晰的决策路线**：单方案试点或互斥 A/B 分臂，选定路线后按阶段推进。
- **可核验的交付**：下载试点与全量 CSV，全量排除试点用户；执行记录保留阶段事件。
- **咨询与操作分离**：政策咨询不产生退款操作；申请必须由用户确认，知识库可返回政策引用。

```mermaid
flowchart LR
    A[活动目标 / 人群 / 预算] --> B[Planner → Executor → Critic]
    B --> C[候选比较]
    C --> D[人工审批 → 试点名单]
    C --> E[A/B 双臂 → 反馈 → 人工晋级]
    D --> F[排除试点用户 → 全量 CSV]
    E --> F
    G[政策问题] --> H[检索 → 原文依据]
    I[退款申请] --> J[人工确认 / 取消 → 演示审核记录]
```

完整能力对应 Python 后端；GitHub Pages 使用浏览器模板模拟营销与客服流程。详见[本地模型接入](docs/本地模型接入.md)。

## 可以体验什么

| 用户操作 | 页面反馈 | 完整后端中的实现 |
|---|---|---|
| 填写活动目标、人群、预算 | 三套候选按完成顺序出现 | LangGraph、asyncio、有界并发、结构化输出 |
| 审批某个候选 | 状态推进至可交付试点 | SQL 条件更新、人工确认 |
| 导出试点、全量名单 | 下载 CSV，查看行数；API 提供哈希 | consent 过滤、固定种子抽样、试点排重、SHA256 |
| 创建 A/B 双臂试点 | 展示互斥客群和转化情况 | 事件去重、描述性转化率、人工晋级 |
| 记录模拟转化并晋级 | 胜出方案可全量交付 | 排除两臂所有试点用户 |
| 咨询退款政策、申请退款 | 政策回答或确认/取消按钮 | 确定性路由、事实来源、待确认状态 |
| 查看执行事件和历史任务 | 状态、候选和产物可追踪 | 数据库事件、SSE、Last-Event-ID |

这是独立演示工程，不包含企业内部源码、真实客户数据、真实投放或资金退款。客服操作采用规则路由；独立政策知识库支持 BM25 检索与可选模型证据选择。[知识库说明](docs/政策知识库.md)。

## 使用指南

### 路线 A：单方案交付

1. 在[在线工作台](https://dellya-zhang.github.io/FlowPilot/)输入：`为新会员介绍积分查询与专属客服权益，不提供额外折扣`。
2. 选择人群与预算，点击 **创建并生成方案**，等待三套候选显示。
3. 点击其中一套的 **审批此方案**，确认状态变为“可交付试点”。
4. 点击 **导出试点名单**，再在产物区下载 `pilot` CSV。
5. 点击 **全量排重交付**，下载 `full` CSV。全量名单不包含已参加试点的用户。

### 路线 B：A/B 实验

1. 创建新任务并生成候选，先不要点击普通审批。
2. 点击 **创建双臂试点**，系统取前两套候选，划分互斥用户。
3. 点击 **记录一名模拟转化**。重复点击会去重，不重复增加人数。
4. 查看描述性转化率，再点击某一臂的 **人工选为胜出方案**。
5. 点击 **全量排重交付**。这次会排除两臂试点用户，不只是胜出臂。

转化回流是演示数据；没有显著性检验、自动观察窗口或真实营销效果结论。

### 路线 C：客服确认

1. 输入 `退款政策是什么`，查看政策说明，不产生待执行操作。
2. 输入 `我要退款`，出现确认/取消按钮。
3. 点击 **确认提交审核** 或 **取消申请**。确认只记录演示审核状态，不创建真实工单、不执行支付退款。
4. 输入订单或物流问题，页面会说明当前未连接真实业务系统，不编造订单状态。

在线数据保存在当前浏览器；点击顶部 **重置演示** 可从头体验。桌面与手机均可操作。

<details>
<summary>查看手机界面</summary>

![手机界面](docs/在线演示手机.png)

</details>

## 快速启动完整后端

需要 Python 3.11+。默认 Mock 模式无需模型密钥，也不需要 Docker、Redis 或 PostgreSQL。

```bash
git clone https://github.com/dellya-zhang/FlowPilot.git
cd FlowPilot
python -m venv .venv
```

Windows PowerShell：

```powershell
.venv\Scripts\python -m pip install -e ".[dev]"
Copy-Item .env.example .env
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

macOS / Linux：

```bash
.venv/bin/python -m pip install -e '.[dev]'
cp .env.example .env
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- 工作台：<http://127.0.0.1:8000/>
- Swagger / OpenAPI：<http://127.0.0.1:8000/docs>
- 存活检查：<http://127.0.0.1:8000/api/v1/health>
- 本地静态预览：<http://127.0.0.1:8000/?demo=1>，用于体验浏览器模拟，绕过后端业务请求。

完整模式任务、候选、事件及客服历史保存在 SQL 数据库；文件位于 `artifacts/`。在线静态模式用浏览器存储，两者的数据与实现不同。

### 接入 DeepSeek

仅修改本地 `.env`，不要在网页、README 或 Git 提交中填写真实 Key：

```dotenv
AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=your_local_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
LLM_MAX_TOKENS=600
LLM_MAX_CALLS=18
LLM_BUDGET_CNY=10
```

重启服务后，新任务会使用真实模型。可选 OpenAI 模式使用 `AI_PROVIDER=openai` 和对应 Key。密钥仅由后端读取，GitHub Pages 不需要任何密钥。

费用机制是保守预算保护：请求前预留，返回 usage 后结算，未返回用量时保留预留；调用与费用名额记录在 `artifacts/llm-usage.jsonl`，跨同机进程使用文件锁。它不是供应商账单系统；删除日志会破坏历史预算连续性。

### 同机多进程与可选基础设施

```bash
uvicorn app.main:app --workers 3
```

各进程必须共享数据库、对象目录及费用日志。SQL 租约和 owner 校验保护任务执行权；心跳续期、过期接管及定期恢复已通过同机独立进程验证。模型并发上限仍按 worker 计算。

```bash
docker compose up --build -d
```

Compose 提供 PostgreSQL、Redis 和 MinIO 配置。该组合尚未在本次环境实测，不将配置存在等同于集成验证。

## 架构与技术选择

```mermaid
flowchart LR
    UI[工作台 / REST Client] --> API[FastAPI + Pydantic]
    API --> SQL[(SQLAlchemy / SQL)]
    API --> Runner[后台执行 + 租约 / owner校验]
    Runner --> Graph[LangGraph Planner → Executor → Critic]
    Graph --> Provider[Mock / DeepSeek / OpenAI]
    Provider --> Budget[同机跨进程费用账本]
    Runner --> SQL
    API --> Events[数据库事件 / SSE]
    API --> Experiment[A/B分臂 / 回流去重 / 人工晋级]
    Experiment --> Store[CSV / 本地或MinIO]
    API --> Support[客服规则 / 政策事实 / 确认状态]
```

| 层 | 技术 | 职责 |
|---|---|---|
| API | FastAPI、Pydantic | REST、校验、错误响应、OpenAPI |
| 执行 | LangGraph、asyncio | 三节点工作流、候选并发、超时与取消 |
| 数据 | SQLAlchemy、SQLite / PostgreSQL | 状态机、事务、唯一约束、租约与事件 |
| 交付 | CSV、SHA256、本地文件 / S3 | 产物复用、完整性检查与人群排重 |
| 交互 | HTML / CSS / JavaScript、SSE | 用户操作、候选、事件、历史与下载 |
| 费用 | FileLock、原子文件替换 | 同机跨进程预算预留及结算 |
| 可选 | Redis Lua、MinIO | 固定窗口限流、S3兼容存储 |

业务状态与图状态分离：Campaign 保存跨请求阶段，LangGraph 组织一次生成。当前恢复是整次任务重跑，未配置图级持久化 checkpointer，不声称节点级续跑或外部调用 exactly-once。

## API 速览

完整请求体、响应和可执行示例见本地 `/docs`。

| API | 用途 |
|---|---|
| `POST /api/v1/campaigns` | 创建营销任务 |
| `POST /api/v1/campaigns/{id}/generate` | 触发候选生成 |
| `GET /api/v1/campaigns/{id}/candidates` | 查询候选 |
| `POST /api/v1/campaigns/{id}/approve` | 普通方案审批 |
| `POST /api/v1/campaigns/{id}/pilot` | 普通试点导出 |
| `POST /api/v1/campaigns/{id}/finalize` | 排重全量交付 |
| `GET /api/v1/campaigns/{id}/stream` | SSE业务事件 |
| `POST /api/v1/experiments` | 创建双臂实验 |
| `POST /api/v1/experiments/{id}/feedback` | 转化回流，事件及用户去重 |
| `POST /api/v1/experiments/{id}/promote` | 人工晋级 |
| `POST /api/v1/support/sessions` | 创建客服会话 |
| `POST /api/v1/support/sessions/{id}/messages` | 咨询或提出申请 |
| `POST /api/v1/support/sessions/{id}/confirm` | 确认或取消待办 |

## 测试与验证

```bash
python -m pytest -q
python scripts/extreme_http.py
python scripts/multiprocess_check.py
```

上述入口使用隔离数据库、Mock 或故障传输，不消耗真实模型费用。浏览器测试另需 `playwright` 与 Edge，静态演示可执行 `python scripts/browser_static_demo.py`。

| 验证 | 结果与范围 |
|---|---|
| 自动回归 | 78项通过，含契约、并发、取消、超时、文件损坏及旧owner拒写 |
| 真实模型 | 15次 DeepSeek 调用、5088 Token；营销接口与浏览器链路通过 |
| 请求突发 | 500次本机HTTP读取全部成功；客户端排队耗时不代表服务端容量 |
| 同机多进程 | 3服务进程续期/强杀接管/幂等通过；8进程竞争2费用名额，严格2接受6拒绝 |
| 静态演示 | 普通HTTP静态服务器上完整操作通过，不访问API或模型服务器 |

[真实模型报告](docs/DeepSeek联调报告.md) · [极限工况报告](docs/极限工况测试报告.md) · [多进程报告](docs/多进程验证报告.md) · [机器可读结果](docs/multiprocess-results.json)

`live_deepseek.py` 和 `browser_deepseek_e2e.py` 是明确的付费测试入口，不在 CI 自动执行。少量样本的字段契约成功率不等于模型业务质量或真实转化提升。

## 实现范围与后续方向

- **已实现**：前后端业务闭环、真实模型适配、SQL状态与事件、人工审批、实验排重、同机租约、费用保护、故障测试与交互预览。
- **尚未实现**：生产账号/租户隔离、跨主机全局预算、真实订单/工单/退款、ClickHouse查询、统计显著性和图级Checkpoint。
- **已知改进点**：信息不足时缺少完整澄清阶段；拒绝说明可能成为候选；Critic为启发式评分；关键词护栏存在误报。
- **部署边界**：同机文件锁依赖共享路径；租约依赖同机时钟；副作用不保证精确一次；`create_all`不替代版本化迁移。

[项目逻辑审计与设计差异](docs/项目逻辑审计与设计差异.md) · [优化路线与验收标准](docs/优化路线.md) · [变更记录](CHANGELOG.md)

## 目录

```text
app/agents/       工作流与模型Provider
app/api/          营销、实验与客服API
app/services/     租约、预算、规则、存储及业务逻辑
app/models/       业务表与执行租约
web/              真实后端工作台及显式浏览器模拟适配器
scripts/          HTTP、多进程、浏览器与可选付费评测
tests/           隔离自动测试
docs/            操作指南、架构、测试结果和截图
.github/workflows/ CI与静态预览发布
```

保留仓库原有 [LICENSE](LICENSE)。欢迎基于实际实现提交问题与改进；请勿提交密钥、真实客户名单或私人资料。
