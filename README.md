# TradeGuard · 交易风控中枢

信用卡/支付交易反欺诈与自动化处置多 Agent 系统。

- 方案文档：[`docs/`](./docs/00-总则.md)（4A 架构 + BDD/TDD + 敏捷拆分 + OpenAPI 契约 + 数据字典 + 社区对标）
- 机读契约：[`docs/openapi/tradeguard-openapi.yaml`](./docs/openapi/tradeguard-openapi.yaml)

## Sprint 0 一键起栈

```powershell
copy .env.example .env
docker compose up -d --build        # 中间件 + 自研服务（web-portal 首次构建较慢）
docker compose run --rm data-generator python generate.py --scale small   # 冒烟档合成数据
```

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
- LLM 凭据生产走 Higress 透传，本地 `.env` 仅调试用，勿提交真实 Key。
