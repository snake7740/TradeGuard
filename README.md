# TradeGuard · 交易风控中枢

信用卡/支付交易反欺诈与自动化处置多 Agent 系统。

**English README**: [`README.en.md`](./README.en.md) · 许可证：[Apache-2.0](./LICENSE)

> **解决什么问题**：交易欺诈已从「单笔攻击」演变为「团伙化、工业化」——测卡攻击、账户盗用、
> 跑分洗钱靠单交易视角的规则引擎无法发现跨账户关联；多源信号（流水/征信/舆情/投诉）分散、
> 告警疲劳、处置留痕缺失。本系统以多 Agent 协同实现「信号聚合 → 根因定位 → 处置执行 →
> 核验审计 → 知识沉淀」五阶段闭环：低风险自动处置、高风险证据化调查、全部处置合规留痕。

- 方案文档：[`docs/`](./docs/00-总则.md)（4A 架构 + BDD/TDD + 敏捷拆分 + OpenAPI 契约 + 数据字典 + 社区对标）
- 机读契约：[`docs/openapi/tradeguard-openapi.yaml`](./docs/openapi/tradeguard-openapi.yaml)
- 方案总览（HTML，含业务逻辑与操作流程）：[`docs/reports/tradeguard-overview.html`](./docs/reports/tradeguard-overview.html)

## 技术栈一览

| 层 | 组件 | 作用 |
| --- | --- | --- |
| 多 Agent 协同 | AgentTeams（Manager + 4 Worker） | 任务拆解 / 上下文传递 / 协同执行 |
| 后端 | FastAPI（web-api） | 12 态状态机 + 5 Skill 内核，22 REST 路径 |
| 业务库 MCP | mcp-core（12 工具） | 处置执行唯一通道（审批把关 + 幂等） |
| 外部源 MCP | mcp-external-mock（3 工具） | 征信 / 舆情 / 投诉（确定性模拟） |
| 前端 | Vue 3 + Element Plus | 5 页面 × 4 角色人机操作面 |
| 存储 | PostgreSQL（pgvector） | 业务 / 向量 / 审计一体 |
| 事件 | RocketMQ | 事件驱动闭环（尽力而为） |
| 配置 / 治理 | Nacos + Higress | 阈值热更新 + AI 网关统一入口 |
| 可观测 | AgentScope Studio | OTLP Trace 可视化 |
| LLM | DashScope（Qwen） | 语义 RAG + 根因假设排序（可选，无 Key 降级） |

详细选型与替换成本见 [`docs/04 §2`](./docs/04-技术架构TA.md)。

## 项目地图（先读什么）

| 你要找 | 去哪 |
| --- | --- |
| 完整方案（4A 架构） | [`docs/00-总则.md`](./docs/00-总则.md)（索引）→ 01~09 |
| 业务场景 / 痛点 / 演示 | [`docs/reports/tradeguard-overview.html`](./docs/reports/tradeguard-overview.html)（浏览器打开） |
| 后端代码 | `services/web-api/app/`（入口 `main.py`） |
| 处置执行 | `services/mcp-core/server.py` |
| 前端页面 | `web-portal/src/views/` |
| 数据库结构 | `db/init/01-schema.sql` |
| 一键启动 | `scripts/start_all.py` |
| 演示场景 | `scripts/demo_playbook.py` |
| 各模块设计 | `services/*/README.md`、`db/README.md`、`skills/README.md` |

## 快速开始（下载即可用）

克隆或下载解压后，按下面步骤即可完整启动；核心链路**不依赖 LLM**，无需任何 Key 也能跑通。

### 0. 前置条件

- Windows 10/11 + [Docker Desktop](https://www.docker.com/products/docker-desktop/)（Docker Compose v2，引擎在运行）
- Git（或直接下载 ZIP 解压）
- 首次构建需拉取镜像，建议网络可达镜像仓库

> **操作系统说明**：本仓库按 Windows 优先开发验证（Docker Desktop + PowerShell，命令用
> `.venv\Scripts\python`、`copy`）。macOS / Linux 将命令替换为 `.venv/bin/python`、`cp` 即可；
> Windows 专属注意——宿主端口由 `com.docker.backend.exe` 引擎进程监听，**绝不能 taskkill 清端口**
> （会杀掉整个 Docker 引擎），详见 [`CLAUDE.md`](./CLAUDE.md)。

### 1. 获取代码

```powershell
git clone <仓库地址> TradeGuard
cd TradeGuard
```

### 2.（可选但推荐）配置 LLM Key —— 语义 RAG / LLM 调查 / AgentTeams 协同需要

> 核心链路（立案 → 聚合 → 自动处置）确定性运行，**不依赖 LLM**；跳过本步仍可一键起栈。
> 需要 **语义 RAG（知识库语义检索）、LLM 根因假设排序、AgentTeams 5 Agent 协同** 时才需配置。
> 无 Key 时自动降级：语义 RAG 回落字符哈希、LLM 调查回落规则假设（功能可用，检索/推理质量降级）。

1. 复制模板：

```powershell
copy secrets\dashscope.env.example secrets\dashscope.env
```

1. 编辑 `secrets\dashscope.env`（位置：**仓库根目录 `secrets/dashscope.env`**），填入真实值（端点取自**租户 MaaS 控制台**，勿用公共 DashScope 端点）：

```bash
DASHSCOPE_API_KEY=sk-你的真实Key              # 必填：DashScope/通义 API Key
# 两个端点语义不同，勿混用（详见 secrets/dashscope.env.example 注释）：
DASHSCOPE_BASE_URL=https://<租户id>.cn-beijing.maas.aliyuncs.com/api/v1             # 百炼原生（AgentTeams 原生调用）
AGENTTEAMS_OPENAI_BASE_URL=https://<租户id>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1  # OpenAI 兼容（web-api 语义 RAG / LLM 调查用）
AGENTTEAMS_DEFAULT_MODEL=qwen3.8-max
```

> `secrets/dashscope.env` 已被 `.gitignore` 排除（仅 `*.example` 模板入库），真实 Key 绝不提交、不泄露。
>
> **安全提示（运行时暴露面）**：本地以 compose `env_file` 将 Key 注入 web-api 容器，Key 会出现在
> 容器环境变量（`docker inspect` / `docker-compose config` 可见）——这是本地开发折中；
> **生产形态应走 Higress 凭据透传（docs/04 §5），Agent 侧零密钥**。切勿在日志/终端回显这些配置。

### 3. 一键启动（可重入，任意初始状态）

```powershell
.venv\Scripts\python scripts\start_all.py
```

脚本自动完成：`.env` 凭据自举（缺失时生成随机强凭据，R-37）→ 拉起全栈 →
逐服务真实探活 → 数据就位（空库优先从 `db/export/` 恢复，缺失才回退 data-generator）→
Higress 路由重建 → AgentTeams 体检 → C1~C9 端到端取证，**全绿 exit 0**。

### 4. 访问

| 入口 | 地址 | 说明 |
| --- | --- | --- |
| 风控门户 | <http://localhost:8300> | 顶栏切换 4 角色，完整演示五阶段闭环 |
| OpenAPI 文档 | <http://localhost:8200/docs> | 24 个 REST 路径契约（含 /api/skills 技能注册表） |
| 方案总览 | `docs/reports/tradeguard-overview.html` | 浏览器打开，含业务逻辑 + 操作流程 + 演示场景 |

### 5. 操作指引（启动后怎么用）

1. 打开门户 <http://localhost:8300，顶栏切换角色（风控值班员> / 风控审批官 / 合规审计员 / 风控策略管理员）。
2. 在「案件工作台」新建演示案件（选 low / medium / high 严重度）→ 观察案件状态沿五阶段闭环自动流转。
3. 三个演示场景（详见 HTML「端到端演示」章节，或 `scripts/demo_playbook.py` 追溯）：
   - **D1 低风险自动放行**：立案后零人工，EventWorker 自动推进至 DISPOSED。
   - **D2 调查后冻结 + 人工审批**：高风险 → 调查 → 人工批准 → 执行 → 核验 → 归档。
   - **D3 误报申诉回滚**：执行后故障注入 → 核验不一致 → 反向处置 → 人工复核申诉成立。

## 安全基线（R-37）

- `.env` / `secrets/` 不入库（gitignore），仓库只含 CHANGE_ME 模板；
- 全部宿主端口仅绑定 `127.0.0.1`（局域网不可达）；
- `/api` 全量需 `Authorization: Bearer <TG_API_TOKEN>`（仅 `/api/health` 豁免；门户 nginx 自动注入令牌，浏览器无感）；
- CORS 走 `TG_CORS_ORIGINS` 白名单。

## 目录结构（对应 docs/04 §3 部署拓扑）

```
├── docker-compose.yml          # 中间件编排（healthcheck 依赖顺序）
├── db/
│   ├── init/                   # 01-schema 12 表 + 02-roles 权限矩阵 + 03 图退化 + 04 不变量 + 05~07 迁移
│   ├── export/                 # 命名卷数据导出件（克隆即完整启动，密钥扫描闸门，R-37）
│   └── backup.sh               # 备份/恢复
├── config/rocketmq/broker.conf
├── services/
│   ├── web-api/                # FastAPI 后端（12 态状态机 + 5 Skill 内核）
│   ├── mcp-core/               # AA-MCP-01 业务库 MCP（12 工具，处置执行唯一通道）
│   ├── mcp-external-mock/      # AA-MCP-02 外部数据源模拟（3 工具）
│   └── data-generator/         # PaySim 式合成数据
├── web-portal/                 # Vue 3 + Element Plus 前端（5 页面 × 4 角色）
├── skills/                     # AA-SK-01~05 官方技能定义
├── scripts/                    # start_all / demo_playbook / kpi_report / offline_eval 等
└── docs/                       # 方案文档集（00~12 + openapi + reports）
```

## 本地等价与替换声明

- PolarDB-PG → `pgvector/pgvector:pg16`（可平滑迁移，docs/04 §2 替换成本列）；
- UnifiedModel 语义运行时 → SQL 视图退化路径（`fn_related_graph`），接入正式运行时仅换 mcp-core 查询后端；
- LLM 凭据生产走 Higress 透传；本地凭据仅经 `.env` / `secrets/`（均 gitignore）流转，
  仓库只含 CHANGE_ME 格式模板（`.env.example` / `secrets/dashscope.env.example`），
  克隆后新建对应文件填入真实值即可，Git 提交不携带任何密钥（R-37）。
