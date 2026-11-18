# FlowPilot

面向会员运营和客服的工作流系统。运营人员录入活动目标、客群和预算，系统生成候选方案，人工选定版本后导出试点与剩余人群名单。客服侧提供政策查询和退款审核申请确认。

这是个人开发的演示工程，使用合成客户数据，不连接真实营销渠道、订单或支付系统。

[在线工作台](https://dellya-zhang.github.io/FlowPilot/) · [操作指南](docs/用户操作指南.md) · [架构说明](docs/设计映射与架构.md)

## 业务场景

**会员权益推广。** 运营希望向新会员介绍积分查询和专属客服，不新增优惠。系统生成三套候选，运营审批其中一套后直接导出全量名单。旧版可选单方案试点仍兼容。仓库保存的一次浏览器模拟案例产出 17 人试点、150 人剩余名单，交集为零。

[试点 CSV](web/examples/pilot.csv) · [剩余 CSV](web/examples/full.csv) · [案例记录](web/examples/case.json)

![名单交付示例](docs/案例交付桌面.png)

**A/B 试点。** 固定其他条件，仅比较文案、频次或渠道之一。系统冻结两个方案版本，划分互斥客群，记录去重后的模拟反馈，结算后保存描述性结果和推荐，由运营选择胜出方案。全量交付排除两臂所有试点用户。当前不提供统计显著性检验。

**客服。** 政策咨询直接回答；订单查询明确告知未接入订单系统；退款申请进入待确认状态，用户确认或取消后记录结果。政策知识库独立提供 BM25 检索及原文引用。

## 实现

| 模块 | 实现与职责 |
| --- | --- |
| 营销编排 | [planning.py](app/services/planning.py)、[campaigns.py](app/services/campaigns.py)：LangGraph 组织目标、人群、策略、文案与检查节点；有界并发生成候选，SSE 推送事件 |
| 业务状态 | [versions.py](app/services/versions.py)、[entities.py](app/models/entities.py)：SQL 保存任务、候选、修订和审批快照；条件更新限制状态迁移 |
| 名单与实验 | [routes.py](app/api/routes.py)、[experiments.py](app/api/experiments.py)：客群过滤、固定种子抽样、互斥分臂和排重；CSV 与 SHA256 |
| 任务恢复 | [leases.py](app/services/leases.py)：租约、心跳、过期接管及 owner 校验；恢复时重跑生成任务，不是节点级 checkpoint |
| 客服与检索 | [support.py](app/services/support.py)、[knowledge.py](app/services/knowledge.py)：规则路由、确认状态、BM25 及引用；模型不授权业务写操作 |
| 模型费用 | [budget.py](app/services/budget.py)、[limiter.py](app/services/limiter.py)：预算预留与结算、限流、同机跨进程保护 |

前端使用 HTML/CSS/JavaScript，后端使用 FastAPI、SQLAlchemy、LangGraph。默认 SQLite 和本地文件；可选 PostgreSQL、Redis、MinIO 配置尚未完成组合验证。

```mermaid
flowchart LR
    UI[运营工作台] --> API[FastAPI]
    API --> G[LangGraph 候选生成]
    G --> M[Mock / 云模型]
    API --> DB[(任务 / 版本 / 事件)]
    G --> DB
    API --> C[审批 / 实验 / 排重]
    C --> DB
    C --> CSV[CSV 名单]
    DB --> SSE[SSE 事件]
    SSE --> UI
    API --> S[客服状态机 / 政策检索]
```

## 在线演示与本地运行

GitHub Pages 只运行浏览器模拟适配器，使用模板和浏览器存储，不执行 Python、SQL、LangGraph 或 BM25。完整后端需要本地启动；默认 Mock 模型，不需要 API Key。上面的案例数字来自模拟快照，不是真实营销效果。

## 使用指南

在工作台创建营销计划，生成候选后选择普通审批或 A/B 试点，普通审批直接导出全量；A/B 先记录反馈、结算（提前结算须注明模拟原因）、人工晋级，再下载 CSV。客服入口可输入“退款政策是什么”或“我要退款”，观察咨询与申请的不同处理方式。详细步骤见[操作指南](docs/用户操作指南.md)。

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

## API 与测试

本地 Swagger 位于 `/docs`。主要接口：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | /api/v1/campaigns | 创建任务 |
| POST | /api/v1/campaigns/{id}/generate | 生成候选 |
| GET | /api/v1/campaigns/{id}/stream | SSE 事件 |
| POST | /api/v1/campaigns/{id}/approve | 审批 |
| POST | /api/v1/campaigns/{id}/pilot | 试点导出 |
| POST | /api/v1/campaigns/{id}/full | 全量交付（普通审批或实验晋级） |
| POST | /api/v1/experiments/{id}/finalize | 固化实验结论 |
| POST | /api/v1/experiments | A/B 实验 |
| POST | /api/v1/support/sessions/{id}/messages | 客服消息 |
| POST | /api/v1/support/sessions/{id}/confirm | 确认或取消 |

```bash
python -m pytest -q
python scripts/extreme_http.py
python scripts/multiprocess_check.py
```

历史验证记录：[真实模型联调](docs/DeepSeek联调报告.md)、[HTTP 故障与突发测试](docs/极限工况测试报告.md)、[多进程验证](docs/多进程验证报告.md)。这些报告记录特定环境下的测试，不代表生产容量。付费模型测试不在 CI 自动执行。

## 目录

```text
app/agents/    模型适配与编排
app/api/       REST 接口
app/services/  业务逻辑、规则与基础服务
app/models/    SQL 数据模型
web/           工作台与浏览器模拟适配器
tests/         自动化回归
scripts/       联调、浏览器与多进程检查
docs/          设计及验证记录
```

## 边界

尚未实现生产级账户和租户隔离、真实渠道投放、订单或支付退款、ClickHouse 查询、跨主机全局预算以及图级 checkpoint。详见[设计差异](docs/项目逻辑审计与设计差异.md)与[变更记录](CHANGELOG.md)。

[LICENSE](LICENSE)
