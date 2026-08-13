# 07 · 敏捷拆分与 API 契约

> 本文档是 TradeGuard 文档集的**交付视角**：把 [01 BA](./01-业务架构BA.md) 的能力与 [02 AA](./02-应用架构AA.md) 的构件拆为可独立验证的敏捷工作项，并把全部接口收敛为统一的 OpenAPI 3.0 契约（机读文件：[`openapi/tradeguard-openapi.yaml`](./openapi/tradeguard-openapi.yaml)）。验收标准一律引用 [06](./06-BDD-TDD工程验证体系.md) 的 SC 场景编号。

---

## 1. 拆分原则

1. **垂直切片**：每个 Story 从数据层→服务层→接口层→界面层可独立验证，拒绝"先做完全部后端再做前端"；
2. **依赖驱动排序**：数据基础→聚合→调查→处置审批→审计知识→门户演示，与 [06 §5](./06-BDD-TDD工程验证体系.md#5-tdd-实施纪律与分阶段-dod) 里程碑 M1→M3 对齐；
3. **单 Story ≤4 小时执行粒度**，超出即继续拆分；每个 Story 完成即跑对应自动化测试（TDD 先测后码）；
4. **DoD 统一**：代码合入 + 对应测试绿 + 审计留痕可用 + OpenAPI 契约同步更新。

---

## 2. Epic 清单（映射 BA 能力与限界上下文）

| Epic | 名称 | 映射能力 | 限界上下文（01 §10） | 里程碑 |
|---|---|---|---|---|
| E1 | 基础设施与治理底座 | — | 通用域 | M1 |
| E2 | 数据基础与合成数据 | BA-CAP-01 | 交易数据 | M1 |
| E3 | 信号聚合闭环 | BA-CAP-01/02/03 | 风险事件 | M1 |
| E4 | 欺诈调查与关联分析 | BA-CAP-04 | 欺诈调查 | M3 |
| E5 | 处置执行与审批回滚 | BA-CAP-05 | 风控处置 | M2 |
| E6 | 核验审计与知识沉淀 | BA-CAP-06/07 | 合规审计 / 风控知识 | M3 |
| E7 | 门户、可观测与演示 | 全体（人机界面） | 跨上下文 | M3→决赛 |

---

## 3. User Story 目录

编号 `US-{Epic}-{序号}`；验收列引用 SC 场景（06 §2）与不变量（03 §9.3）。

### E1 基础设施与治理底座

| Story | 描述 | 验收标准 | 点 |
|---|---|---|---|
| US-E1-01 | docker compose 一键拉起中间件（PolarDB/RocketMQ/Nacos/Higress），healthcheck 依赖顺序就绪 | `/api/health` 返回 UP（API-W-15） | 3 |
| US-E1-02 | AgentTeams 官方脚本安装 + Manager/5 Worker 创建（身份 Prompt 按 02 §3） | Matrix 房间可开、Manager 可分派 | 5 |
| US-E1-03 | Nacos 注册：MCP/Skill 元数据 + BA-BR 阈值动态配置（SC-06 前置） | 配置变更不重启生效 | 3 |
| US-E1-04 | backup.sh 备份脚本 + 恢复演练（04 §9） | 恢复后 `/api/cases` 数据一致 | 2 |

> **Sprint 0 执行记录（2026-08-13 实测）**：US-E1-01 ✅（10 容器全 Up，5 healthcheck healthy，`/api/health` UP + 立案落库）；US-E1-02 基础设施 ✅（AgentTeams Manager/Matrix/MinIO/内置 Higress 安装就绪并接入 tradeguard-net，控制台 http://localhost:18088；5 Worker 创建待真实 LLM Key）；US-E1-04 备份 ✅（dump 产出，恢复演练 Sprint 1 补全）；US-E1-03 排入 Sprint 1。部署坑位见 04 §3 实测备注与 scripts/rocketmq-init.md。
>
> **架构设计落地补充（2026-08-13）**：三端按设计植入架构构件——后端 web-api 分层重构（状态机 12 态/16 事件/18 迁移路径 + 仓储模式 + 事件发布端口/适配器 + 乐观锁写路径模板，API-W-01~15 全量声明，见 services/web-api/README.md）；MCP 补齐 API-M-05/06（工具全集 API-M-01~09 实测可列）；存储端新增 db/init/04-invariants.sql（DA-INV-01 状态迁移白名单触发器 + DA-INV-06 知识发布人工门控，与应用层状态机构成双守护，实测非法迁移两层均拒绝）；前端 api.js 全量契约声明 + 角色路由守卫（见 web-portal/README.md）。冒烟取证：立案→聚合→调查→复核确认→审批批准全链迁移 version 0→2，审计链 3 条，SSE 首帧连通，前端构建通过。

### E2 数据基础与合成数据

| Story | 描述 | 验收标准 | 点 |
|---|---|---|---|
| US-E2-01 | PolarDB DDL 全量落地（08 §3 十二表 + 索引 + 权限矩阵账号） | DA-INV-05 权限集成测试通过 | 5 |
| US-E2-02 | 合成数据发生器：5000 账户/10 万交易/5 组欺诈团伙（04 §8） | 四类欺诈模式数据可查（API-W-01 触发前预置） | 5 |
| US-E2-03 | UnifiedModel 语义模型装载（03 §3 Node/Link） | `query_related_graph` 返回 2 跳图 | 3 |

> **Sprint 0 执行记录（2026-08-13 实测）**：US-E2-01 ✅（12 表 + 索引 + tg_web/tg_app 权限矩阵落地，含分区表授权修正，见 05 R-20）；US-E2-02 冒烟档 ✅（500 账户/5261 交易/2 团伙入库，演示档 Sprint 1 重跑 --scale demo）；US-E2-03 退化路径 ✅（fn_related_graph 2 跳查询实测返回，正式语义运行时接入为复赛事项，见 TA-C-08）。

### E3 信号聚合闭环

| Story | 描述 | 验收标准 | 点 |
|---|---|---|---|
| US-E3-01 | 告警受理：API-W-01 → CaseRegistered 事件 → 主控立案 | 事件落 DA-T-03，状态 REGISTERED | 3 |
| US-E3-02 | AA-MCP-02 模拟外部源（征信/舆情/投诉）+ 防腐层翻译为标准信号 | 契约测试：Schema/错误码/降级 | 5 |
| US-E3-03 | AA-SK-01 聚合降噪评分（含降噪合并、权重评分、velocity 频次统计 BA-BR-14 单测） | 单元测试≥60% 覆盖；信号落 DA-T-04 含 velocity_json；**SC-11 通过** | 6 |
| US-E3-04 | 风险分级裁决 + 低风险自动放行 | **SC-01 通过** | 5 |

### E4 欺诈调查与关联分析

| Story | 描述 | 验收标准 | 点 |
|---|---|---|---|
| US-E4-01 | AA-SK-02 根因假设匹配（DA-KB-01 RAG 检索 + 引用对齐 doc_id） | 结论附 doc_id，未命中显式声明 | 5 |
| US-E4-02 | 关联网络扩展（四类边 2 跳，DA-INV 图深度上限） | API-W-05 返回图谱，BA-BR-06 加分生效 | 5 |
| US-E4-03 | 影响面报告与证据链固化（DA-T-05 只增） | DA-INV-04 测试通过 | 3 |

### E5 处置执行与审批回滚

| Story | 描述 | 验收标准 | 点 |
|---|---|---|---|
| US-E5-01 | AA-SK-03 处置执行器 + 幂等键（DA-INV-03） | **SC-07 通过** | 5 |
| US-E5-02 | 审批门控：无 approval_ref 触边界返回 E-DISP-AUTH | **SC-02 前半段通过** | 3 |
| US-E5-03 | 审批门户写路径：API-W-09 回填→ApprovalApproved/Rejected 事件→状态机 | **SC-02/SC-03 通过** | 5 |
| US-E5-04 | 中风险转人工复核（BA-BR-01 分段 + API-W-07） | **SC-10 通过** | 3 |
| US-E5-05 | 审批时效升级定时器（BA-BR-13） | **SC-09 通过**，超时标红 | 3 |
| US-E5-06 | 状态机守护与乐观锁（DA-INV-01，DA-T-03 version） | 非法迁移全部拒绝（单测） | 3 |

### E6 核验审计与知识沉淀

| Story | 描述 | 验收标准 | 点 |
|---|---|---|---|
| US-E6-01 | AA-SK-04 核验（10 分钟计时，BA-BR-08）+ 不一致 P0 升级 | VerificationFailed → 反向处置 | 5 |
| US-E6-02 | 审计链全链路写入 + API-W-10 回放 | **SC-08 通过** | 3 |
| US-E6-03 | AA-SK-05 复盘摘要与入库申请（DA-T-09 pending） | 申请单可见于 API-W-11 | 3 |
| US-E6-04 | 知识库人工发布门控（API-W-12/13，DA-INV-06）+ 向量化流水线 | **SC-05 通过** | 5 |

### E7 门户、可观测与演示

| Story | 描述 | 验收标准 | 点 |
|---|---|---|---|
| US-E7-01 | web-api FastAPI 骨架 + bearer 鉴权 + 审计中间件 | OpenAPI 契约校验通过 | 3 |
| US-E7-02 | 事件工作台页面（列表/详情/信号/图谱，真实读库） | 零内置静态数据 | 5 |
| US-E7-03 | SSE 实时事件流（API-W-14）+ 演示话术对标（引用 AWS 参考架构证主链路通用、突出审批门控差异点，[09](./09-社区对标分析.md)） | 演示时流转现场可见 | 3 |
| US-E7-04 | AgentScope Studio 接入 + 离线评估脚本（BA-KPI-02/03/04） | 评估报告可复现 | 3 |
| US-E7-05 | 3 个剧本化演示事件编排（04 §8，采样参数借鉴 PaySim 分布，与场景测试夹具同源） | 演示=测试回放 | 3 |

**合计**：7 Epic / 29 Story / 约 107 点。

---

## 4. Sprint 计划与里程碑映射

| 里程碑 | Sprint | 内容 | 出口标准（对齐 06 §5） | 状态 |
|---|---|---|---|---|
| M1 最小闭环 | S1（E1+E2）、S2（E3） | 底座 + 数据 + 聚合放行 | SC-01 通过；契约测试绿 | S1 ✅（2026-08-13）；S2 ✅（2026-08-13） |
| M2 审批链路 | S3–S4（E5） | 处置 + 审批回滚 + 时效 | SC-02/03/07/09/10 通过；状态机/幂等单测全绿 | S3–S4 ✅（2026-08-13） |
| M3 调查与知识 | S5–S6（E4+E6+E7-01~03） | 调查、审计、知识、门户 | SC-05/08 通过；集成测试全绿 | 待启动 |
| 决赛 | S7（E7-04~05） | 可观测 + 演示剧本 | 11/11 场景通过 + KPI 报告 | 待启动 |

> **Sprint 1 执行记录（2026-08-13 实测）**：R-22 测试补债 ✅（46/46 绿：18 迁移参数化 + 5 非法迁移 + 6 human_only 守卫 + 乐观锁冲突 + DB 触发器双守护负路径 + 审计链回放）；US-E1-03 ✅（Nacos v3 admin API：3 配置文档 + 3 服务实例注册，scripts/nacos_register.py；web-api ConfigService 5s 热加载，实测改阈值 70→75 不重启 7s 生效，新增 API-W-16 /api/config/thresholds）；US-E1-04 ✅（scripts/backup.sh + backup-restore-drill.ps1，恢复演练 10 表行数全对账，dump 604K）；E2 演示档 ✅（5000 账户/105916 交易/67 watch 账户/5 团伙，单账户峰值 55 笔支撑 SC-11）；US-E1-02 后半当日解锁（见下方 LLM 解锁记录）。环境修正：宿主 5432 被本机 PostgreSQL 占用，compose 宿主映射改 5433。Skill 体系落库：skills/AA-SK-01~05 官方技能可执行定义 + SKILL-DISPATCH 调度矩阵。
>
> **US-E1-02 LLM 解锁记录（2026-08-13 实测）**：真实 DashScope Key 经安全链路注入（`scripts/set-dashscope-key.ps1` SecureString 录入 → `secrets/dashscope.env`（gitignore）→ 环境变量注入，全程不落明文仓库件）。模型取证：专属 MaaS 端点 `/api/v1/models` 确认 **qwen3.8-max** 可用（MoE 旗舰/1M 上下文/function-calling），OpenAI 兼容路径 `/compatible-mode/v1/chat/completions` 实测连通。注入方式：`scripts/update-agentteams-llm.sh`（sed env + KEEP_ALL installer + 防御回写），随后经 `agt update manager --name default --model qwen3.8-max` 交 controller 按 CRD 重建 Manager。排障根因：controller 创建 Manager 的正解形态是 `agentteams-net` 桥接网络 + `AGENTTEAMS_RUNTIME=k8s`（Manager 自行从 MinIO 拉取 workspace 并 touch `.initialized`），此前手工重建误用共享网络命名空间 + local 模式导致死等崩溃循环。验收取证：`agt llm-preflight` passed；Manager Status=running/Restarts=0；Matrix 登录 + sync loop；经 Higress 网关 key-auth 实测 `model=qwen3.8-max` 返回正常；qwenpaw `active_model.json`=agentteams-gateway/qwen3.8-max（Key ENC 加密存储）；控制台 18888 HTTP 200。Worker 创建 ✅：`agt create worker --model qwen3.8-max --soul-file`（SOUL 按 02 §3 身份清单）创建 aa-ag-02~05 四 Worker，`agt get workers` 全部 Running/model=qwen3.8-max/runtime=copaw，SOUL.md 已注入各 Worker 容器；Matrix 房间实测：发消息→Manager 经 qwen3.8-max 生成完整中文回复入房（分派链路可用）。至此 **US-E1-02 全部完成**（5 Agent：1 Manager + 4 Worker，Sprint 1 收官）。
>
> **Sprint 2 执行记录（2026-08-13 实测，E3 信号聚合闭环）**：四 Story 全完成，测试 89/89 绿（基线 46 + 新增 43）。US-E3-02 ✅：`tests/test_acl_contract.py` 对运行中 mcp-external-mock 经官方 streamable-http 通道实测 9 例（缺 query_reason 拒收 BA-BR-10 / 成功载荷 schema / 确定性）。US-E3-03 ✅：AA-SK-01 内核落 `services/web-api/app/skills/aggregation.py`（纯函数可测：velocity 统计/降噪合并/加权评分/分级裁决 + AggregationService 编排），覆盖率 97%（≥60% 要求）；信号落库经 mcp-core 新工具 API-M-10 `record_case_signals`（tg_app 写角色，DA-INV-05 权限矩阵不破）。US-E3-04 ✅：分级裁决四路由（noise 降噪归档 / auto_release 低风险自动放行 / investigate 转调查 / all_fail 转人工）；新增状态迁移 AGGREGATING→DISPOSING（BA-CAP-05 低风险自动通道，边界守卫在聚合裁决层，应用层状态机 + DB 白名单表双守护同步 19 条）。SC-11 ✅：12 笔高频簇 velocity_json 与流水统计一致且 velocity 加分 ≥30；SC-01 ✅：低风险小额（风险分 23<40、涉案 800<5000）自动放行至 DISPOSED，DispositionExecuted 事件 + 审计 actor=AA-AG-04 依据含风险分，幂等重入仅 1 条处置记录。环境排障沉淀：① mcp SDK 全栈统一 1.9.4（1.2.1 无 streamable_http）；② streamable-http 挂载点需尾斜杠 `/mcp/`（无斜杠 307）；③ 宿主系统代理会拦截 httpx 回环连接返回 502，入口注入 NO_PROXY 旁路；④ FastMCP 对形似 JSON 的字符串参数会预解析，工具入参用 list[dict] 而非 JSON 字符串。
>
> **Sprint 3-4 执行记录（2026-08-13 实测，E5 处置执行与审批回滚）**：六 Story 全完成，测试 95/95 绿（基线 89 + 新增 6，SC-02/03/07/09/10 各一例 + BA-BR-07 聚合守卫一例）。AA-SK-03 内核落 `services/web-api/app/skills/disposition.py`（覆盖率 92%，≥60% 要求）：submit 四路由（refused_mid_risk / approval_required / idempotent_hit / executed）+ approve/reject 闭环 + scan_pending_escalations（BA-BR-13，web-api lifespan 后台任务 30s 轮询）。US-E5-01 ✅：SC-07 同幂等键重投返回首次凭证且仅 1 条 executed 记录；US-E5-02 ✅：SC-02 风险分 82 无凭证冻结触 E-DISP-AUTH，建单经 mcp-core 新工具 API-M-11 `create_approval_request`（tg_app 写角色，DA-INV-05 内建单），案转 PENDING_APPROVAL；US-E5-03 ✅：API-W-09 委托内核编排——批准→ApprovalApproved→自动执行至 DISPOSED（执行凭证 receipt 含 approval_ref 关联）；驳回→ApprovalRejected→RollbackToReview 回退 MANUAL_REVIEW + context_json.auto_channel=disabled（BA-BR-07，聚合裁决层持久守卫，同档低风险亦不再自动放行）；US-E5-04 ✅：SC-10 风险分 55 自动放行被拒 E-DISP-SCOPE，无处置记录仅审计 disposition.refused；US-E5-05 ✅：SC-09 超 30 分钟未决工单 escalated_at 打标 + approval.escalate 审计（actor=system:timer-BA-BR-13）+ ApprovalEscalated 事件，二次扫描幂等；US-E5-06 ✅：沿用 Sprint 1 状态机/乐观锁 46 例基线（E5 未新增迁移）。DB 扩展：db/init/05-approval-extension.sql（approval_record +requested_action/requested_amount/escalated_at，运行库已执行）。mcp-core execute_disposition 同事务内置 executed+receipt（SC-02 审批与凭证关联落库）。

依赖提示：E4 依赖 E3 的信号结构；E5 依赖 E2 的 DDL；E7-02/03 依赖 E3/E5 产生的真实事件数据。

---

## 5. API 目录（统一 OpenAPI 3.0）

全量 Schema/参数/响应以 [`openapi/tradeguard-openapi.yaml`](./openapi/tradeguard-openapi.yaml) 为唯一事实来源（复赛仓库以 spectral/openapi-validator 纳入 CI 校验）。REST 与 MCP 工具共用同一套 components.schemas，杜绝两套数据结构漂移。

### 5.1 web-api REST（API-W-x）

| 编号 | 接口 | 方法 | 页面/用途 | 场景载体 |
|---|---|---|---|---|
| API-W-01 | `/api/alerts` | POST | 告警受理/演示触发（AA-CL-01） | SC-01~04 入口 |
| API-W-02 | `/api/cases` | GET | 事件列表 | — |
| API-W-03 | `/api/cases/{case_id}` | GET | 事件详情（含共享状态） | — |
| API-W-04 | `/api/cases/{case_id}/signals` | GET | 信号清单 | SC-04 |
| API-W-05 | `/api/cases/{case_id}/graph` | GET | 关联网络图谱 | BA-BR-06 |
| API-W-06 | `/api/cases/{case_id}/evidence` | GET | 证据链 | BA-BR-03 |
| API-W-07 | `/api/cases/{case_id}/review` | POST | 中风险人工复核 | SC-10 |
| API-W-08 | `/api/approvals` | GET | 待审批队列 | SC-02 |
| API-W-09 | `/api/approvals/{approval_id}/decide` | POST | 批准/驳回 | SC-02/03/09 |
| API-W-10 | `/api/audit/{case_id}` | GET | 审计链回放 | SC-08 |
| API-W-11 | `/api/kb/applications` | GET | 入库申请列表 | SC-05 |
| API-W-12 | `/api/kb/applications/{doc_id}/publish` | POST | 确认发布 | SC-05 |
| API-W-13 | `/api/kb/applications/{doc_id}/reject` | POST | 驳回申请 | SC-05 |
| API-W-14 | `/api/events/stream` | GET(SSE) | 领域事件实时推送 | 全体演示 |
| API-W-15 | `/api/health` | GET | 健康检查 | US-E1-01 |
| API-W-16 | `/api/config/thresholds` | GET/PUT | 阈值配置（Nacos 热加载） | US-E1-03 |
| API-W-17 | `/api/cases/{case_id}/aggregate` | POST | 触发信号聚合（AA-SK-01） | SC-01/SC-11 |

### 5.2 MCP 工具契约（API-M-x，Schema 见 openapi.yaml `x-mcp-tool`）

| 编号 | 工具 | Server | 权限 | 幂等 |
|---|---|---|---|---|
| API-M-01 | `query_transactions` | AA-MCP-01 | 只读 | 是 |
| API-M-02 | `query_related_graph` | AA-MCP-01 | 只读 | 是 |
| API-M-03 | `execute_disposition` | AA-MCP-01 | 写（审批门控） | 幂等键强制 |
| API-M-04 | `query_disposition_result` | AA-MCP-01 | 只读 | 是 |
| API-M-05 | `submit_kb_application` | AA-MCP-01 | 写（仅申请） | 是 |
| API-M-06 | `query_audit_trail` | AA-MCP-01 | 只读 | 是 |
| API-M-07 | `query_credit` | AA-MCP-02 | 只读（模拟） | 是 |
| API-M-08 | `query_sentiment` | AA-MCP-02 | 只读（模拟） | 是 |
| API-M-09 | `query_complaint` | AA-MCP-02 | 只读（模拟） | 是 |
| API-M-10 | `record_case_signals` | AA-MCP-01 | 写（tg_app，信号只增+评分回写） | 是（同案重复聚合仅追加信号） |
| API-M-11 | `create_approval_request` | AA-MCP-01 | 写（tg_app，审批建单 DA-T-07） | 否（每次建单新工单，调用方幂等由编排层保证） |

**契约纪律**：任何接口/工具变更必须先改 openapi.yaml 再改代码（与 03 §9.4 领域事件纪律同级）；错误码统一见 [08 §6](./08-数据模型与数据字典.md#6-错误码表)。

---

## 6. 与其他文档的回接

- Story 验收标准 → SC 场景（06 §2）→ 测试层（06 §3），形成"需求→场景→测试"单链；
- API Schema 字段 ↔ 数据字典（08 §3）字段一一对应，变更双向同步；
- Epic 完成状态纳入 [05 追溯矩阵](./05-追溯矩阵与整体评审报告.md) 复审范围。
