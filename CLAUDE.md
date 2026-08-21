# CLAUDE.md — TradeGuard 项目工作指引

TradeGuard · 交易风控中枢：AI 多 Agent 金融风控系统，五阶段闭环
「信号聚合 → 根因定位 → 处置执行 → 核验审计 → 知识沉淀」。
设计文档体系 v1.5.2（docs/00~14，含 docs/14 v1.6 增强路线 US-E16），代码已全量落地（Sprint 1-13 + 增强批次，十六轮评审留痕）。

## 目录结构

```
services/web-api/          FastAPI 后端（API-W-01~30）：app/ 核心域 + skills/ 四内核 + tests/（339 例，2026-08-22 全量回归实证）
services/mcp-core/         业务库 MCP（:8101，API-M 12 工具，处置执行唯一通道）
services/mcp-external-mock/ 外部源模拟 MCP（:8102，征信/舆情/投诉/企业资质四契约源 + pyod 统计离群三算法（iforest/lof/ecod），共 7 工具，确定性播种；企业资质双轨：有厂商凭据走真实源，降级 mock）
services/data-generator/   合成数据（PaySim 式分布，爆发簇近 1h 可触发 velocity）
web-portal/                Vue3 + Element Plus 前端门户
docs/                      00 总则 / 01 BA / 02 AA / 03 DA / 04 TA / 05 追溯评审 /
                           06 BDD-TDD / 07 敏捷+API 契约 / 08 数据字典 / 09 社区对标
docs/openapi/tradeguard-openapi.yaml   REST 契约唯一事实来源（22 路径）
db/init/                   SQL：01-schema / 04-invariants（白名单+双守护触发器）/
                           05-approval-extension / 06-closedloop-fix（幂等迁移，新卷旧卷双写一致）
db/export/                 命名卷数据导出件（克隆即完整启动；volume_export 再生成，密钥扫描闸门）
skills/                    AA-SK-01~05 官方技能可执行定义 + SKILL-DISPATCH 调度矩阵
scripts/                   start_all / demo_playbook / kpi_report / nacos_register / higress_routes / agentteams_doctor / check_pg / data_retention / volume_export 等
```

## 常用命令（Windows + Git Bash）

```bash
.venv/Scripts/python scripts/start_all.py               # 一键启动+数据通路自证（首选）：.env 凭证自举→拉起全栈→真实探活→数据就位（空库先读 db/export）→端到端取证核心 19 项，全绿 exit 0
docker compose up -d --build          # 起全栈（postgres/rocketmq/nacos/higress/studio/mcp×2/web×2）
.venv/Scripts/python -m pytest services/web-api/tests   # 全量回归（31 文件 339 例，约 40 分钟；需先起栈）
.venv/Scripts/python scripts/demo_playbook.py           # 演示=测试复现，D1~D3 三个场景，目标 3/3
.venv/Scripts/python scripts/kpi_report.py              # KPI 报告重生成（全量/演示范围分列判定）
cd web-portal && npm run build                          # 前端构建
# PG 宿主侧端口 5433（容器间仍 postgres:5432；宿主 5432 被本机 PG 占用）：
docker compose exec postgres psql -U postgres -d tradeguard
```

测试用 DSN：`tg_web:tg_web_dev` / `tg_app:tg_app_dev` / `postgres:tradeguard_dev` @ `localhost:5433/tradeguard`。
数据重灌：`docker compose run --rm data-generator`（10 万行分批插入，约 2 分钟）→
`python scripts/nacos_register.py`（阈值播种，仅补缺键不覆盖）。

## 核心架构事实（改代码前必读）

- **状态机**：`app/core/state_machine.py` 纯函数，12 态（`CaseState`，不是 CaseStatus）/
  19 事件 / 22 迁移。改迁移必须同步：state_machine + db/init 白名单 + 03 §9.2 + openapi 事件枚举 + 08 数据字典。
- **事件闭环**：扁平信封 `{case_id, trace_id, occurred_at, event, actor, payload}`。
  进程内总线必达 + RocketMQ 尽力而为；EventWorker DB 轮询主力（2s，仅 REGISTERED 且非 TEST 源，单飞锁，
  失败有限重试，耗尽驻车 DLQ：processing_deadletter + E-WORKER-DLQ，人工经 /api/deadletter 复位，LoopEngine BA-BR-22）。
  `TG_EVENT_WORKER` 代码缺省 OFF、compose 显式 on——**测试环境绝不能开**（会抢跑用例造成 flake）。
- **双守护**：应用层状态机 + DB 触发器。写路径 `repositories.transition` 事务内
  `set_config('tg.actor', ...)` → `trg_case_actor_gate` 按 actor 前缀拦截（human:* 才许五对人类迁移）。
  两道触发器均 `BEFORE UPDATE OF status`（不拦 INSERT）。**测试装配两种合法姿势**：
  ①UPDATE 直改 status 必须先 set tg.actor；②需要 INVESTIGATING 等目标态时优先
  **单事务直插**（test_routes._reviewable_case 模式）——compose EventWorker 只轮询
  REGISTERED，直插案件对其不可见，杜绝同库抢跑竞态（R-37 实测教训）。
- **处置准入**（mcp-core execute_disposition）：approval_ref 验真 = case 匹配 + requested_action
  匹配或逆动作对。`INVERSE_ACTION = {freeze/block/reduce→release, release→block}`
  在 mcp-core server.py 与 web-api verification.py **双处同源**，改一处必须同步另一处。
  高风险 release 豁免仅限案件 ROLLBACK 态；70 分线经 sys_config `br-01-auto-block-score` 双端同源。
- **阈值热更新（SC-06）**：聚合/处置阈值从 `config.snapshot()` 读（Nacos 5s 快照 + sys_config 镜像），
  纯函数用关键字缺省参（缺省=原常量）保证单测兼容。PUT 写回顺序：键白名单/值域 →
  键存在性闸门（未播种键先 400，不污染 Nacos 权威源）→ Nacos → DB → reload。
- **契约先行**：先改 docs/openapi/*.yaml → schemas.py → 路由。接口编号 API-W-01~30 / API-M-01~19，
  mcp-core 在码 12 工具 + external 7 工具（契约 4 + pyod 3），零未编号工具（新增工具必须回写 07 §5 编号）。
- **安全基线（R-37）**：`.env`（gitignore）为唯一凭据源——仓库只提交 CHANGE_ME 模板
  （.env.example / secrets/*.example），start_all 缺失时自动生成随机强凭据；compose `${VAR:?}`
  fail-fast 拒绝缺凭据启动。web-api 全量 `/api` 过 TG_API_TOKEN bearer 守卫（app/api_guards.py，
  仅 `/api/health` 豁免、SSE 也鉴权、`secrets.compare_digest` 恒时比较、api.request/api.denied
  落审计）；门户 nginx envsubst 注入令牌，浏览器零改动——**脚本探活 /api 必须从
  env/.env 装载令牌随行**（higress_routes/demo_playbook 已示范），否则 401。
  全部宿主端口仅绑 127.0.0.1；CORS 走 `TG_CORS_ORIGINS` 白名单。
  守卫取 `request.scope["path"]`（路由同源，防 Host 头污染绕过——CVE-2026-48710，
  starlette<1.0.1 的 url.path 可被恶意 Host 改写）；依赖钉版 fastapi 0.141.1 /
  starlette 1.6.0（CVE 全修复线）双层设防。config PUT 顺序：白名单+值域→键存在性
  闸门→Nacos→DB→reload（存在性前置，杜绝未播种键污染权威源）。

## 已知环境实况（勿当 bug 修）

- **Higress 已承载门户流量**：portal nginx→higress:8080→web-api.dns:8000（dns 型服务源，
  compose 配 `*.tg.local` 网络别名）。数据面 HTTP=容器内 8080（宿主 8180）、HTTPS 8443；
  控制台 :8001 首次初始化未完成（路由经容器 /data 文件仓下发）。`down -v` 清卷后用
  `scripts/higress_routes.py` 幂等重建路由。web-api→MCP 保持直连是刻意的场景化取舍（04 §5）。
- **AgentTeams 独立部署栈（非 compose）**：controller/manager/4 worker 跑在 Docker Desktop 的
  agentteams-net，经 `docker network connect` 接 tradeguard-net 才能解析 mcp-core。Docker Desktop
  重启后 controller 可能 Exited(127) 不自愈、worker 停 Sleeping、丢组网与容器层 mcporter 配置——
  一律跑 `scripts/agentteams_doctor.py` 幂等体检恢复（拉起→唤醒→组网→控制台回环守护→注入 MCP 桥→校验 12+7 工具）。
  端口：controller 18001/18080/18088、manager 18888、dashboard 13000、worker 控制台 18092~18095
  （aa-ag-0N→18090+N，宿主回环；doctor 发现非回环发布自动重建收口，R-37）。
- **Windows Docker Desktop 宿主端口监听者是引擎进程本身**：发布端口由 `com.docker.backend.exe`
  进程直接 LISTEN，**绝不能 taskkill 清端口**（会杀掉整个引擎，docker CLI 立即失联）——
  占用清理只能停归属容器或等自然释放。`docker ps` 归属解析需兼容 `0.0.0.0:`（compose）与
  `127.0.0.1:`（agentteams 回环发布）两种形态。此坑与解法已固化进 `scripts/start_all.py`。
- **一键启动入口 `scripts/start_all.py`**：无论服务存活/端口占用与否均可重入——**.env 凭证自举**
  （缺失自动生成随机强凭据，R-37）→引擎不可达自动拉起 Docker Desktop→compose down 保卷→
  逐端口清障（外部进程才 taskkill）→up→逐服务真实探活→数据就位（空库优先从 db/export
  导出件 TRUNCATE+psql 恢复，导出缺失才回退 data-generator 重灌）→Higress 重建→AgentTeams
  体检→C1~C9+X1 端到端硬证据，核心 19 项全绿 exit 0。`--build` 重建镜像；`--no-agentteams`
  跳过协同栈。克隆/复制的项目零手工配置即可完整启动（凭据自举 + 卷导出恢复）。
- **KPI 全量范围未达标是诚实结果**：pytest 残留 source=TEST 案件抬升 KPI-03/04；
  验收以演示范围为准，报告按范围分列判定，不得以演示达标掩盖全量未达标。
- asyncpg 单次 executemany 10 万行会在客户端挂死（pg_stat_activity 呈 ClientRead）——大批量插入必须分批。
- compose 端口：pg 5433、nacos 8848 + **控制台 8850（BUG-07：v3 控制台在容器 8080，/nacos 是 v2 已移除路径；宿主 8080 被占故映射 8850，302→/next/）**、higress 控制台 8001/网关 HTTP 8180、studio 3000、mcp 8101/8102、web-api 8200(:8000)、portal 8300。**宿主侧全部仅绑定 127.0.0.1**（R-37），容器间互访不受影响。
- **db/export 卷导出件**：pg-data→tradeguard-data.sql.gz（data-only，schema 由 db/init 幂等迁移建立）、
  higress-data→higress-data.tar.gz（离线快照，路由实际由 higress_routes.py 重建；打包剔除
  data/secrets/ 与 *.key/*.pem 私钥素材）。数据演进后用 `scripts/volume_export.py` 再生成
  （内置密钥扫描闸门：明文/base64 封装 PEM、sk-*、GitHub/AWS 凭据特征，检出即删件报错）并随代码提交。

## Skill 治理（本机库分派，2026-08-17 建档）

本机 skill 库 `C:\Users\junzh\.agents\skills`，开工前按 [`docs/reports/skill-governance.md`](docs/reports/skill-governance.md) 选定强制 skill，未列入治理表的 skill 不得用于本项目交付。

- **元 skill 管线（每次修改/评审强制，顺序执行）**：engineering-execution-protocol → meta-cognitive-evolution → engineering-first-principles → engineering-problem-solving → ao-essence-injector；多 Agent 场景 Leader 加 expert-team-orchestration。
- **领域分派摘要**：后端 python-skill、契约 api-design、测试 tdd-skill、前端 vue-skill、库 db-design、容器 docker、安全 security-engineering/*、文档核对 doc-cross-audit、调试 superpowers/systematic-debugging、完成验证 superpowers/verification-before-completion。
- **防误选**：`fintech-trading` 是美股交易策略（名字近似），与本项目「交易反欺诈」无关，禁用；java-skill/mysql-skill/next 系与本项目技术栈不匹配。

## 文档回写纪律

代码行为变更 → 同步回写对应文档并在 05 追溯矩阵留痕（R 编号续接）、07 Sprint 执行记录补行。
历史 Sprint 记录是留痕档案，只续不改。编号规范（BA-BR-x / AA-SK-x / DA-T-x / SC-x / US-Ex-xx）见 00 §3。

## 安全约束（硬性）

- **未经用户要求不做 git commit/push。**
- secrets/（真实 DASHSCOPE_API_KEY + 租户 MaaS endpoint）、.env、.claude/ 绝不提交、绝不在输出中展示。
- 容器内自签证书私钥等敏感产物不得回显、落盘或写入任何文档。
