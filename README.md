# TradeGuard · 交易风控中枢

信用卡/支付交易反欺诈与自动化处置多 Agent 系统。

- 方案文档：[`docs/`](./docs/00-总则.md)（4A 架构 + BDD/TDD + 敏捷拆分 + OpenAPI 契约 + 数据字典 + 社区对标）
- 机读契约：[`docs/openapi/tradeguard-openapi.yaml`](./docs/openapi/tradeguard-openapi.yaml)
- 方案总览（HTML，含业务逻辑与操作流程）：[`docs/reports/tradeguard-overview.html`](./docs/reports/tradeguard-overview.html)

## 快速开始（下载即可用）

克隆或下载解压后，按下面步骤即可完整启动；核心链路**不依赖 LLM**，无需任何 Key 也能跑通。

### 0. 前置条件

- Windows 10/11 + [Docker Desktop](https://www.docker.com/products/docker-desktop/)（Docker Compose v2，引擎在运行）
- Git（或直接下载 ZIP 解压）
- 首次构建需拉取镜像，建议网络可达镜像仓库

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

2. 编辑 `secrets\dashscope.env`（位置：**仓库根目录 `secrets/dashscope.env`**），填入真实值（端点取自**租户 MaaS 控制台**，勿用公共 DashScope 端点）：

```bash
DASHSCOPE_API_KEY=sk-你的真实Key              # 必填：DashScope/通义 API Key
# 两个端点语义不同，勿混用（详见 secrets/dashscope.env.example 注释）：
DASHSCOPE_BASE_URL=https://<租户id>.cn-beijing.maas.aliyuncs.com/api/v1             # 百炼原生（AgentTeams 原生调用）
AGENTTEAMS_OPENAI_BASE_URL=https://<租户id>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1  # OpenAI 兼容（web-api 语义 RAG / LLM 调查用）
AGENTTEAMS_DEFAULT_MODEL=qwen3.8-max
```

> `secrets/dashscope.env` 已被 `.gitignore` 排除（仅 `*.example` 模板入库），真实 Key 绝不提交、不泄露。

### 3. 一键启动（可重入，任意初始状态）

```powershell
.venv\Scripts\python scripts\start_all.py
```

脚本自动完成：`.env` 凭据自举（缺失时生成随机强凭据，R-37）→ 拉起全栈 →
逐服务真实探活 → 数据就位（空库优先从 `db/export/` 恢复，缺失才回退 data-generator）→
Higress 路由重建 → AgentTeams 体检 → C1~C9 端到端取证，**全绿 exit 0**。

### 4. 访问

| 入口 | 地址 | 说明 |
|---|---|---|
| 风控门户 | http://localhost:8300 | 顶栏切换 4 角色，完整演示五阶段闭环 |
| OpenAPI 文档 | http://localhost:8200/docs | 22 个 REST 路径契约 |
| 方案总览 | `docs/reports/tradeguard-overview.html` | 浏览器打开，含业务逻辑 + 操作流程 + 演示剧本 |

---

## Sprint 0 一键起栈（克隆即完整启动）

```powershell
.venv\Scripts\python scripts\start_all.py
```

一条命令可重入起栈 + 端到端自证：`.env` 缺失时自动生成随机强凭据（TG_API_TOKEN /
Nacos 互信值等，R-37 凭证自举）→ `docker compose up -d` → 逐服务真实探活 →
数据就位（空库优先从 `db/export/` 提交在库的卷导出件恢复，缺失才回退
`data-generator` 合成）→ Higress 路由重建 → AgentTeams 体检 → C1~C9 数据通路硬证据。

手工分步起栈（等价路径）：

```powershell
copy .env.example .env              # 然后把其中 CHANGE_ME 换成随机强值（必填项带 ? 插值校验）
docker compose up -d --build        # 中间件 + 自研服务（web-portal 首次构建较慢）
python scripts\nacos_register.py    # 阈值/元数据播种（凭据从 .env 装载）
```

> 安全基线（R-37）：`.env`/`secrets/` 不入库（gitignore），仓库只含 CHANGE_ME 模板；
> 全部宿主端口仅绑定 `127.0.0.1`；`/api` 全量需 `Authorization: Bearer <TG_API_TOKEN>`
> （仅 `/api/health` 豁免；门户 nginx 自动注入令牌，浏览器无感）。直连 API 调试示例：
> `curl -H "Authorization: Bearer $TG_API_TOKEN" http://localhost:8200/api/cases`

| 入口 | 地址 |
|---|---|
| 风控门户（web-portal） | http://localhost:8300 |
| web-api（OpenAPI 文档） | http://localhost:8200/docs |
| mcp-core（AA-MCP-01） | http://localhost:8101/mcp |
| mcp-external-mock（AA-MCP-02） | http://localhost:8102/mcp |
| Nacos 控制台 | http://localhost:8848/nacos |
| Higress 控制台 | http://localhost:8001 |
| AgentScope Studio | http://localhost:3000 |

AgentTeams（多 Agent 协同基点）按官方脚本独立部署，见 [`scripts/install-agentteams.md`](./scripts/install-agentteams.md)；安装后控制台 http://localhost:18088（已接入 tradeguard-net）。

## 目录结构（对应 docs/04 §3 部署拓扑）

```
├── docker-compose.yml          # US-E1-01 中间件编排（healthcheck 依赖顺序）
├── db/
│   ├── init/01-schema.sql      # 12 表 DDL + 索引（08 §3/§4，含 velocity_json BA-BR-14）
│   ├── init/02-roles.sql       # 权限矩阵账号（03 §6，只增表禁改）
│   ├── init/03-umodel-fallback.sql  # UnifiedModel 退化路径（图视图 + 2 跳查询）
│   ├── export/                 # 命名卷数据导出件（克隆即完整启动，密钥扫描闸门，R-37）
│   └── backup.sh               # US-E1-04 备份/恢复
├── config/rocketmq/broker.conf
├── services/
│   ├── web-api/                # FastAPI（04 §10.1 三端真实调用链）
│   ├── mcp-core/               # AA-MCP-01（审批门控 + 幂等）
│   ├── mcp-external-mock/      # AA-MCP-02（唯一允许的数据源模拟）
│   └── data-generator/         # PaySim 式合成数据（09 对标借鉴）
├── web-portal/                 # Vue 3 + Element Plus（4 页面 × 4 角色）
└── docs/                       # 方案文档集 v1.4
```

## 本地等价与替换声明

- PolarDB-PG → `pgvector/pgvector:pg16`（可平滑迁移，docs/04 §2 替换成本列）；
- UnifiedModel 语义运行时 → SQL 视图退化路径（`fn_related_graph`），接入正式运行时仅换 mcp-core 查询后端；
- LLM 凭据生产走 Higress 透传；本地凭据仅经 `.env` / `secrets/`（均 gitignore）流转，
  仓库只含 CHANGE_ME 格式模板（`.env.example` / `secrets/dashscope.env.example`），
  克隆后新建对应文件填入真实值即可，Git 提交不携带任何密钥（R-37）。
