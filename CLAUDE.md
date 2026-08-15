# CLAUDE.md — TradeGuard 项目工作指引

TradeGuard · 交易风控中枢：AI 多 Agent 金融风控系统，五阶段闭环
「信号聚合 → 根因定位 → 处置执行 → 核验审计 → 知识沉淀」。
设计文档体系 v1.4.10（docs/00~09），代码已全量落地（Sprint 1-13，十五轮评审留痕）。

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
scripts/                   start_all / demo_playbook / kpi_report / nacos_register / higress_routes / agentteams_doctor / check_pg / data_retention 等
```

## 常用命令（Windows + Git Bash）

```bash
.venv/Scripts/python scripts/start_all.py               # 一键启动+数据通路自证（首选）：拉起全栈→真实探活→端到端取证 20 项，全绿 exit 0
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

- **Higress 已承载门户流量**：portal nginx→higress:8080→web-api.dns:8000（dns 型服务源，
  compose 配 `*.tg.local` 网络别名）。数据面 HTTP=容器内 8080（宿主 8180）、HTTPS 8443；
  控制台 :8001 首次初始化未完成（路由经容器 /data 文件仓下发）。`down -v` 清卷后用
  `scripts/higress_routes.py` 幂等重建路由。web-api→MCP 保持直连是刻意的场景化取舍（04 §5）。
- **AgentTeams 独立部署栈（非 compose）**：controller/manager/4 worker 跑在 Docker Desktop 的
  agentteams-net，经 `docker network connect` 接 tradeguard-net 才能解析 mcp-core。Docker Desktop
  重启后 controller 可能 Exited(127) 不自愈、worker 停 Sleeping、丢组网与容器层 mcporter 配置——
  一律跑 `scripts/agentteams_doctor.py` 幂等体检恢复（拉起→唤醒→组网→注入 MCP 桥→校验 12+3 工具）。
  端口：controller 18001/18080/18088、manager 18888、dashboard 13000（宿主回环）。
- **Windows Docker Desktop 宿主端口监听者是引擎进程本身**：发布端口由 `com.docker.backend.exe`
  进程直接 LISTEN，**绝不能 taskkill 清端口**（会杀掉整个引擎，docker CLI 立即失联）——
  占用清理只能停归属容器或等自然释放。`docker ps` 归属解析需兼容 `0.0.0.0:`（compose）与
  `127.0.0.1:`（agentteams 回环发布）两种形态。此坑与解法已固化进 `scripts/start_all.py`。
- **一键启动入口 `scripts/start_all.py`**：无论服务存活/端口占用与否均可重入——引擎不可达自动拉起
  Docker Desktop→compose down 保卷→逐端口清障（外部进程才 taskkill）→up→逐服务真实探活→
  数据就位（空库自动重灌）→Higress 重建→AgentTeams 体检→C1~C9+X1 端到端硬证据，20 项全绿 exit 0。
  `--build` 重建镜像；`--no-agentteams` 跳过协同栈。
- **KPI 全量口径未达标是诚实结果**：pytest 残留 source=TEST 案件抬升 KPI-03/04；
  验收以演示口径为准，报告按口径分列判定，不得以演示达标掩盖全量未达标。
- asyncpg 单次 executemany 10 万行会在客户端挂死（pg_stat_activity 呈 ClientRead）——大批量插入必须分批。
- compose 端口：pg 5433、nacos 8848、higress 控制台 8001/网关 HTTP 8180、studio 3000、mcp 8101/8102、web-api 8200(:8000)、portal 8300。

## 文档回写纪律

代码行为变更 → 同步回写对应文档并在 05 追溯矩阵留痕（R 编号续接）、07 Sprint 执行记录补行。
历史 Sprint 记录是留痕档案，只续不改。编号规范（BA-BR-x / AA-SK-x / DA-T-x / SC-x / US-Ex-xx）见 00 §3。

## 安全约束（硬性）

- **未经用户要求不做 git commit/push。**
- secrets/（真实 DASHSCOPE_API_KEY + 租户 MaaS endpoint）、.env、.claude/ 绝不提交、绝不在输出中展示。
- 容器内自签证书私钥等敏感产物不得回显、落盘或写入任何文档。
