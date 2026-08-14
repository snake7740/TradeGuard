# CLAUDE.md — TradeGuard 项目工作指引

TradeGuard · 交易风控中枢：AI 多 Agent 金融风控竞赛作品（方向四），五阶段闭环
「信号聚合 → 根因定位 → 处置执行 → 核验审计 → 知识沉淀」。
设计文档体系 v1.4.4（docs/00~09），代码已全量落地（Sprint 1-8，九轮评审留痕）。

## 目录结构

```
services/web-api/          FastAPI 后端（API-W-01~22）：app/ 核心域 + skills/ 四内核 + tests/（169 例）
services/mcp-core/         业务库 MCP（:8101，API-M 12 工具，处置执行唯一通道）
services/mcp-external-mock/ 外部源模拟 MCP（:8102，征信/舆情/投诉，确定性播种）
services/data-generator/   合成数据（PaySim 式分布，爆发簇近 1h 可触发 velocity）
web-portal/                Vue3 + Element Plus 前端门户
docs/                      00 总则 / 01 BA / 02 AA / 03 DA / 04 TA / 05 追溯评审 /
                           06 BDD-TDD / 07 敏捷+API 契约 / 08 数据字典 / 09 社区对标
docs/openapi/tradeguard-openapi.yaml   REST 契约唯一事实来源（22 路径）
db/init/                   SQL：01-schema / 04-invariants（白名单+双守护触发器）/
                           05-approval-extension / 06-closedloop-fix（幂等迁移，新卷旧卷双写一致）
skills/                    AA-SK-01~05 官方技能可执行定义 + SKILL-DISPATCH 调度矩阵
scripts/                   demo_playbook / kpi_report / nacos_register / check_pg / data_retention 等
```

## 常用命令（Windows + Git Bash）

```bash
docker compose up -d --build          # 起全栈（postgres/rocketmq/nacos/higress/studio/mcp×2/web×2）
.venv/Scripts/python -m pytest services/web-api/tests   # 全量回归（169 例，约 6 分钟；需先起栈）
.venv/Scripts/python scripts/demo_playbook.py           # 演示=测试回放，D1~D3 三剧本，目标 3/3
.venv/Scripts/python scripts/kpi_report.py              # KPI 报告重生成（全量/演示口径分列判定）
cd web-portal && npm run build                          # 前端构建
# PG 宿主侧端口 5433（容器间仍 postgres:5432；宿主 5432 被本机 PG 占用）：
docker compose exec postgres psql -U postgres -d tradeguard
```

测试用 DSN：`tg_web:tg_web_dev` / `tg_app:tg_app_dev` / `postgres:tradeguard_dev` @ `localhost:5433/tradeguard`。
数据重灌：`docker compose run --rm data-generator`（10 万行分批插入，约 2 分钟）→
`python scripts/nacos_register.py`（阈值播种，仅补缺键不覆盖）。

## 核心架构事实（改代码前必读）

- **状态机**：`app/core/state_machine.py` 纯函数，12 态（`CaseState`，不是 CaseStatus）/
  19 事件 / 21 迁移。改迁移必须同步：state_machine + db/init 白名单 + 03 §9.2 + openapi 事件枚举 + 08 数据字典。
- **事件闭环**：扁平信封 `{case_id, trace_id, occurred_at, event, actor, payload}`。
  进程内总线必达 + RocketMQ 尽力而为；EventWorker DB 轮询主力（2s，仅 REGISTERED，单飞锁，失败不重试）。
  `TG_EVENT_WORKER` 代码缺省 OFF、compose 显式 on——**测试环境绝不能开**（会抢跑用例造成 flake）。
- **双守护**：应用层状态机 + DB 触发器。写路径 `repositories.transition` 事务内
  `set_config('tg.actor', ...)` → `trg_case_actor_gate` 按 actor 前缀拦截（human:* 才许五对人类迁移）。
  **测试里直改 risk_case.status 必须先 set tg.actor**（见 test_routes._reviewable_case 模式）。
- **处置门控**（mcp-core execute_disposition）：approval_ref 验真 = case 匹配 + requested_action
  匹配或逆动作对。`INVERSE_ACTION = {freeze/block/reduce→release, release→block}`
  在 mcp-core server.py 与 web-api verification.py **双处同源**，改一处必须同步另一处。
  高风险 release 豁免仅限案件 ROLLBACK 态；70 分线经 sys_config `br-01-auto-block-score` 双端同源。
- **阈值热更新（SC-06）**：聚合/处置阈值从 `config.snapshot()` 读（Nacos 5s 快照 + sys_config 镜像），
  纯函数用关键字缺省参（缺省=原常量）保证单测兼容。PUT 写回顺序：Nacos → DB → reload。
- **契约先行**：先改 docs/openapi/*.yaml → schemas.py → 路由。接口编号 API-W-01~22 / API-M-01~15，
  mcp-core 在码 12 工具 + external 3 工具，零未编号工具（新增工具必须回写 07 §5 编号）。

## 已知环境实况（勿当 bug 修）

- **Higress 旁路直连**：网关已部署但路由注入不通（console 401、数据面仅 8443 自签 TLS），
  演示流量为浏览器→:8200、Agent→:8101/:8102 直连。04 §5 已如实声明，勿伪造网关流量路径。
- **KPI 全量口径未达标是诚实结果**：pytest 残留 source=TEST 案件抬升 KPI-03/04；
  决赛验收以演示口径为准，报告按口径分列判定，不得以演示达标掩盖全量未达标。
- asyncpg 单次 executemany 10 万行会在客户端挂死（pg_stat_activity 呈 ClientRead）——大批量插入必须分批。
- compose 端口：pg 5433、nacos 8848、higress 控制台 8001、studio 3000、mcp 8101/8102、web-api 8200(:8000)、portal 8300。

## 文档回写纪律

代码行为变更 → 同步回写对应文档并在 05 追溯矩阵留痕（R 编号续接）、07 Sprint 执行记录补行。
历史 Sprint 记录是留痕档案，只续不改。编号规范（BA-BR-x / AA-SK-x / DA-T-x / SC-x / US-Ex-xx）见 00 §3。

## 安全约束（硬性）

- **未经用户要求不做 git commit/push。**
- secrets/（真实 DASHSCOPE_API_KEY + 租户 MaaS endpoint）、.env、.claude/ 绝不提交、绝不在输出中展示。
- 容器内自签证书私钥等敏感产物不得回显、落盘或写入任何文档。
