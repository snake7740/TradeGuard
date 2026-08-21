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
| --- | --- | --- | --- | --- |
| E1 | 基础设施与治理底座 | — | 通用域 | M1 |
| E2 | 数据基础与合成数据 | BA-CAP-01 | 交易数据 | M1 |
| E3 | 信号聚合闭环 | BA-CAP-01/02/03 | 风险事件 | M1 |
| E4 | 欺诈调查与关联分析 | BA-CAP-04 | 欺诈调查 | M3 |
| E5 | 处置执行与审批回滚 | BA-CAP-05 | 风控处置 | M2 |
| E6 | 核验审计与知识沉淀 | BA-CAP-06/07 | 合规审计 / 风控知识 | M3 |
| E7 | 门户、可观测与演示 | 全体（人机界面） | 跨上下文 | M3→验收 |

---

## 3. User Story 目录

编号 `US-{Epic}-{序号}`；验收列引用 SC 场景（06 §2）与不变量（03 §9.3）。

### E1 基础设施与治理底座

| Story | 描述 | 验收标准 | 点 |
| --- | --- | --- | --- |
| US-E1-01 | docker compose 一键拉起中间件（PolarDB/RocketMQ/Nacos/Higress），healthcheck 依赖顺序就绪 | `/api/health` 返回 UP（API-W-15） | 3 |
| US-E1-02 | AgentTeams 官方脚本安装 + Manager/5 Worker 创建（身份 Prompt 按 02 §3） | Matrix 房间可开、Manager 可分派 | 5 |
| US-E1-03 | Nacos 注册：MCP/Skill 元数据 + BA-BR 阈值动态配置（SC-06 前置） | 配置变更不重启生效 | 3 |
| US-E1-04 | backup.sh 备份脚本 + 恢复演练（04 §9） | 恢复后 `/api/cases` 数据一致 | 2 |

> **Sprint 0 执行记录（2026-08-13 实测）**：US-E1-01 ✅（10 容器全 Up，5 healthcheck healthy，`/api/health` UP + 立案落库）；US-E1-02 基础设施 ✅（AgentTeams Manager/Matrix/MinIO/内置 Higress 安装就绪并接入 tradeguard-net，控制台 <http://localhost:18088；5> Worker 创建待真实 LLM Key）；US-E1-04 备份 ✅（dump 产出，恢复演练 Sprint 1 补全）；US-E1-03 排入 Sprint 1。部署坑位见 04 §3 实测备注与 scripts/rocketmq-init.md。
>
> **架构设计落地补充（2026-08-13）**：三端按设计植入架构构件——后端 web-api 分层重构（状态机 12 态/16 事件/18 迁移路径 + 仓储模式 + 事件发布端口/适配器 + 乐观锁写路径模板，API-W-01~15 全量声明，见 services/web-api/README.md）；MCP 补齐 API-M-05/06（工具全集 API-M-01~09 实测可列）；存储端新增 db/init/04-invariants.sql（DA-INV-01 状态迁移白名单触发器 + DA-INV-06 知识发布人工把关，与应用层状态机构成双守护，实测非法迁移两层均拒绝）；前端 api.js 全量契约声明 + 角色路由守卫（见 web-portal/README.md）。冒烟取证：立案→聚合→调查→复核确认→审批批准全链迁移 version 0→2，审计链 3 条，SSE 首帧连通，前端构建通过。

### E2 数据基础与合成数据

| Story | 描述 | 验收标准 | 点 |
| --- | --- | --- | --- |
| US-E2-01 | PolarDB DDL 全量落地（08 §3 十二表 + 索引 + 权限矩阵账号） | DA-INV-05 权限集成测试通过 | 5 |
| US-E2-02 | 合成数据发生器：5000 账户/10 万交易/5 组欺诈团伙（04 §8） | 四类欺诈模式数据可查（API-W-01 触发前预置） | 5 |
| US-E2-03 | UnifiedModel 语义模型装载（03 §3 Node/Link） | `query_related_graph` 返回 2 跳图 | 3 |

> **Sprint 0 执行记录（2026-08-13 实测）**：US-E2-01 ✅（12 表 + 索引 + tg_web/tg_app 权限矩阵落地，含分区表授权修正，见 05 R-20）；US-E2-02 冒烟档 ✅（500 账户/5261 交易/2 团伙入库，演示档 Sprint 1 重跑 --scale demo）；US-E2-03 退化路径 ✅（fn_related_graph 2 跳查询实测返回，正式语义运行时接入为后续事项，见 TA-C-08）。

### E3 信号聚合闭环

| Story | 描述 | 验收标准 | 点 |
| --- | --- | --- | --- |
| US-E3-01 | 告警受理：API-W-01 → CaseRegistered 事件 → 主控立案 | 事件落 DA-T-03，状态 REGISTERED | 3 |
| US-E3-02 | AA-MCP-02 模拟外部源（征信/舆情/投诉）+ 防腐层翻译为标准信号 | 契约测试：Schema/错误码/降级 | 5 |
| US-E3-03 | AA-SK-01 聚合降噪评分（含降噪合并、权重评分、velocity 频次统计 BA-BR-14 单测） | 单元测试≥60% 覆盖；信号落 DA-T-04 含 velocity_json；**SC-11 通过** | 6 |
| US-E3-04 | 风险分级裁决 + 低风险自动放行 | **SC-01 通过** | 5 |

### E4 欺诈调查与关联分析

| Story | 描述 | 验收标准 | 点 |
| --- | --- | --- | --- |
| US-E4-01 | AA-SK-02 根因假设匹配（DA-KB-01 RAG 检索 + 引用对齐 doc_id） | 结论附 doc_id，未匹配显式声明 | 5 |
| US-E4-02 | 关联网络扩展（四类边 2 跳，DA-INV 图深度上限） | API-W-05 返回图谱，BA-BR-06 加分生效 | 5 |
| US-E4-03 | 影响面报告与证据链固化（DA-T-05 只增） | DA-INV-04 测试通过 | 3 |

### E5 处置执行与审批回滚

| Story | 描述 | 验收标准 | 点 |
| --- | --- | --- | --- |
| US-E5-01 | AA-SK-03 处置执行器 + 幂等键（DA-INV-03） | **SC-07 通过** | 5 |
| US-E5-02 | 审批把关：无 approval_ref 触边界返回 E-DISP-AUTH | **SC-02 前半段通过** | 3 |
| US-E5-03 | 审批门户写路径：API-W-09 回填→ApprovalApproved/Rejected 事件→状态机 | **SC-02/SC-03 通过** | 5 |
| US-E5-04 | 中风险转人工复核（BA-BR-01 分段 + API-W-07） | **SC-10 通过** | 3 |
| US-E5-05 | 审批时效升级定时器（BA-BR-13） | **SC-09 通过**，超时标红 | 3 |
| US-E5-06 | 状态机守护与乐观锁（DA-INV-01，DA-T-03 version） | 非法迁移全部拒绝（单测） | 3 |

### E6 核验审计与知识沉淀

| Story | 描述 | 验收标准 | 点 |
| --- | --- | --- | --- |
| US-E6-01 | AA-SK-04 核验（10 分钟计时，BA-BR-08）+ 不一致 P0 升级 | VerificationFailed → 反向处置 | 5 |
| US-E6-02 | 审计链全链路写入 + API-W-10 追溯 | **SC-08 通过** | 3 |
| US-E6-03 | AA-SK-05 复盘摘要与入库申请（DA-T-09 pending） | 申请单可见于 API-W-11 | 3 |
| US-E6-04 | 知识库人工发布把关（API-W-12/13，DA-INV-06）+ 向量化流水线 | **SC-05 通过** | 5 |

### E7 门户、可观测与演示

| Story | 描述 | 验收标准 | 点 |
| --- | --- | --- | --- |
| US-E7-01 | web-api FastAPI 骨架 + bearer 鉴权 + 审计中间件 | OpenAPI 契约校验通过 | 3 |
| US-E7-02 | 事件工作台页面（列表/详情/信号/图谱，真实读库） | 零内置静态数据 | 5 |
| US-E7-03 | SSE 实时事件流（API-W-14）+ 演示话术对标（引用 AWS 参考架构证主链路通用、突出审批把关差异点，[09](./09-社区对标分析.md)） | 演示时流转现场可见 | 3 |
| US-E7-04 | AgentScope Studio 接入 + 离线评估脚本（BA-KPI-02/03/04） | 评估报告可复现 | 3 |
| US-E7-05 | 3 个场景化演示事件编排（04 §8，采样参数借鉴 PaySim 分布，与场景测试夹具同源） | 演示=测试复现 | 3 |

**合计**：7 Epic / 29 Story / 约 107 点。

---

## 4. Sprint 计划与里程碑映射

| 里程碑 | Sprint | 内容 | 出口标准（对齐 06 §5） | 状态 |
| --- | --- | --- | --- | --- |
| M1 最小闭环 | S1（E1+E2）、S2（E3） | 底座 + 数据 + 聚合放行 | SC-01 通过；契约测试绿 | S1 ✅（2026-08-13）；S2 ✅（2026-08-13） |
| M2 审批链路 | S3–S4（E5） | 处置 + 审批回滚 + 时效 | SC-02/03/07/09/10 通过；状态机/幂等单测全绿 | S3–S4 ✅（2026-08-13） |
| M3 调查与知识 | S5–S6（E4+E6+E7-01~03） | 调查、审计、知识、门户 | SC-05/08 通过；集成测试全绿 | S5–S6 ✅（2026-08-13） |
| 验收 | S7（E7-04~05） | 可观测 + 演示场景 | 11/11 场景通过 + KPI 报告 | S7 ✅（2026-08-13） |

> **Sprint 1 执行记录（2026-08-13 实测）**：R-22 测试补债 ✅（46/46 绿：18 迁移参数化 + 5 非法迁移 + 6 human_only 守卫 + 乐观锁冲突 + DB 触发器双守护负路径 + 审计链追溯）；US-E1-03 ✅（Nacos v3 admin API：3 配置文档 + 3 服务实例注册，scripts/nacos_register.py；web-api ConfigService 5s 热加载，实测改阈值 70→75 不重启 7s 生效，新增 API-W-16 /api/config/thresholds）；US-E1-04 ✅（scripts/backup.sh + backup-restore-drill.ps1，恢复演练 10 表行数全对账，dump 604K）；E2 演示档 ✅（5000 账户/105916 交易/67 watch 账户/5 团伙，单账户峰值 55 笔支撑 SC-11）；US-E1-02 后半当日解锁（见下方 LLM 解锁记录）。环境修正：宿主 5432 被本机 PostgreSQL 占用，compose 宿主映射改 5433。Skill 体系落库：skills/AA-SK-01~05 官方技能可执行定义 + SKILL-DISPATCH 调度矩阵。
>
> **US-E1-02 LLM 解锁记录（2026-08-13 实测）**：真实 DashScope Key 经安全链路注入（`scripts/set-dashscope-key.ps1` SecureString 录入 → `secrets/dashscope.env`（gitignore）→ 环境变量注入，全程不落明文仓库件）。模型取证：专属 MaaS 端点 `/api/v1/models` 确认 **qwen3.8-max** 可用（MoE 旗舰/1M 上下文/function-calling），OpenAI 兼容路径 `/compatible-mode/v1/chat/completions` 实测连通。注入方式：`scripts/update-agentteams-llm.sh`（sed env + KEEP_ALL installer + 防御回写），随后经 `agt update manager --name default --model qwen3.8-max` 交 controller 按 CRD 重建 Manager。排障根因：controller 创建 Manager 的正解形态是 `agentteams-net` 桥接网络 + `AGENTTEAMS_RUNTIME=k8s`（Manager 自行从 MinIO 拉取 workspace 并 touch `.initialized`），此前手工重建误用共享网络命名空间 + local 模式导致死等崩溃循环。验收取证：`agt llm-preflight` passed；Manager Status=running/Restarts=0；Matrix 登录 + sync loop；经 Higress 网关 key-auth 实测 `model=qwen3.8-max` 返回正常；qwenpaw `active_model.json`=agentteams-gateway/qwen3.8-max（Key ENC 加密存储）；控制台 18888 HTTP 200。Worker 创建 ✅：`agt create worker --model qwen3.8-max --soul-file`（SOUL 按 02 §3 身份清单）创建 aa-ag-02~05 四 Worker，`agt get workers` 全部 Running/model=qwen3.8-max/runtime=copaw，SOUL.md 已注入各 Worker 容器；Matrix 房间实测：发消息→Manager 经 qwen3.8-max 生成完整中文回复入房（分派链路可用）。至此 **US-E1-02 全部完成**（5 Agent：1 Manager + 4 Worker，Sprint 1 收官）。
>
> **Sprint 2 执行记录（2026-08-13 实测，E3 信号聚合闭环）**：四 Story 全完成，测试 89/89 绿（基线 46 + 新增 43）。US-E3-02 ✅：`tests/test_acl_contract.py` 对运行中 mcp-external-mock 经官方 streamable-http 通道实测 9 例（缺 query_reason 拒收 BA-BR-10 / 成功载荷 schema / 确定性）。US-E3-03 ✅：AA-SK-01 内核落 `services/web-api/app/skills/aggregation.py`（纯函数可测：velocity 统计/降噪合并/加权评分/分级裁决 + AggregationService 编排），覆盖率 97%（≥60% 要求）；信号落库经 mcp-core 新工具 API-M-10 `record_case_signals`（tg_app 写角色，DA-INV-05 权限矩阵不破）。US-E3-04 ✅：分级裁决四路由（noise 降噪归档 / auto_release 低风险自动放行 / investigate 转调查 / all_fail 转人工）；新增状态迁移 AGGREGATING→DISPOSING（BA-CAP-05 低风险自动通道，边界守卫在聚合裁决层，应用层状态机 + DB 白名单表双守护同步 19 条）。SC-11 ✅：12 笔高频簇 velocity_json 与流水统计一致且 velocity 加分 ≥30；SC-01 ✅：低风险小额（风险分 23<40、涉案 800<5000）自动放行至 DISPOSED，DispositionExecuted 事件 + 审计 actor=AA-AG-04 依据含风险分，幂等重入仅 1 条处置记录。环境排障沉淀：① mcp SDK 全栈统一 1.9.4（1.2.1 无 streamable_http）；② streamable-http 挂载点需尾斜杠 `/mcp/`（无斜杠 307）；③ 宿主系统代理会拦截 httpx 回环连接返回 502，入口注入 NO_PROXY 旁路；④ FastMCP 对形似 JSON 的字符串参数会预解析，工具入参用 list[dict] 而非 JSON 字符串。
>
> **Sprint 3-4 执行记录（2026-08-13 实测，E5 处置执行与审批回滚）**：六 Story 全完成，测试 95/95 绿（基线 89 + 新增 6，SC-02/03/07/09/10 各一例 + BA-BR-07 聚合守卫一例）。AA-SK-03 内核落 `services/web-api/app/skills/disposition.py`（覆盖率 92%，≥60% 要求）：submit 四路由（refused_mid_risk / approval_required / idempotent_hit / executed）+ approve/reject 闭环 + scan_pending_escalations（BA-BR-13，web-api lifespan 后台任务 30s 轮询）。US-E5-01 ✅：SC-07 同幂等键重投返回首次凭证且仅 1 条 executed 记录；US-E5-02 ✅：SC-02 风险分 82 无凭证冻结触 E-DISP-AUTH，建单经 mcp-core 新工具 API-M-11 `create_approval_request`（tg_app 写角色，DA-INV-05 内建单），案转 PENDING_APPROVAL；US-E5-03 ✅：API-W-09 委托内核编排——批准→ApprovalApproved→自动执行至 DISPOSED（执行凭证 receipt 含 approval_ref 关联）；驳回→ApprovalRejected→RollbackToReview 回退 MANUAL_REVIEW + context_json.auto_channel=disabled（BA-BR-07，聚合裁决层持久守卫，同档低风险亦不再自动放行）；US-E5-04 ✅：SC-10 风险分 55 自动放行被拒 E-DISP-SCOPE，无处置记录仅审计 disposition.refused；US-E5-05 ✅：SC-09 超 30 分钟未决工单 escalated_at 打标 + approval.escalate 审计（actor=system:timer-BA-BR-13）+ ApprovalEscalated 事件，二次扫描幂等；US-E5-06 ✅：沿用 Sprint 1 状态机/乐观锁 46 例基线（E5 未新增迁移）。DB 扩展：db/init/05-approval-extension.sql（approval_record +requested_action/requested_amount/escalated_at，运行库已执行）。mcp-core execute_disposition 同事务内置 executed+receipt（SC-02 审批与凭证关联落库）。
>
> **Sprint 5-6 执行记录（2026-08-13 实测，E4+E6+E7-01~03）**：E4/E6/E7-01 全完成，测试 110/110 绿（基线 95 + 新增 15，连续两轮稳定全绿；覆盖率 investigation 95%/verification 93%/knowledge 92%/api_guards 84%，TOTAL 87%，均≥ 60% 要求）。US-E4-01~03 ✅：AA-SK-02 内核落 `services/web-api/app/skills/investigation.py`——假设匹配（规则兜底：跑分/盗卡/团伙盗刷，无匹配返回待定转人工）+ DA-KB-01 检索引用 doc_id（未匹配显式声明"无库内匹配"，不虚构引用）+ fn_related_graph 2 跳扩展 + BA-BR-06 黑名单匹配 +30（幂等，API-M-13 context_json 打标）+ 影响面统计（图内账户数/近24h涉险金额）+ 证据固化 DA-T-05（API-M-12 同 claim+source_ref 幂等）→ InvestigationCompleted 转 PENDING_APPROVAL。DA-INV-04 ✅：冻结缺证据链拒写 E-EVIDENCE-MISSING（mcp-core 守卫先于审批把关：证据是受理前提，审计 disposition.refused_evidence）；US-E5-04 ✅：复核确认自动建单（DispositionService.review_confirm → ReviewConfirmed human_only + API-M-11 建 freeze 工单，清 cases.py TODO）。US-E6-01/02 ✅：AA-SK-04 内核落 `verification.py`——一致→VerificationPassed→VERIFIED→CaseArchived→ARCHIVED，审计报告落 DA-T-05 + 复盘提入库申请（AA-SK-05 pending）；不一致→VerificationFailed→反向处置（幂等键 :rollback 后缀）→RollbackExecuted→MANUAL_REVIEW + verification.p0 审计升级；BA-BR-08 十分钟核验超时扫描（幂等，并入 lifespan 30s 轮询）。US-E6-04 ✅：向量化流水线落 `knowledge.py`——确定性哈希 embedding（字符一/二/三元组→1024维 L2 归一，无外部依赖，生产替换 UnifiedModel 仅换端口）+ 200 字切块 + SIMILARITY_MIN=0.22（同主题实测≈0.29/异主题≤0.09 实证分隔）；kb_embedding 写入 ON CONFLICT DO NOTHING（tg_web 仅 INSERT 权限，02-roles.sql）。SC-05 ✅：Agent 申请 pending 检索不可见→人工发布（应用层 human:* 守卫 + 事务内 tg.actor + DB 触发器三重守护，绕过直置被拒 E-KB-HUMAN-GATE）→向量化→检索匹配附 doc_id；SC-08 ✅：全链审计追溯（立案→聚合→调查→审批→执行→核验→归档 10 动作序列完整，ts 单调，逐项 actor/basis/trace_id 非空；mcp-core disposition.submit/approval.create 审计补 trace_id）。US-E7-01 ✅：`api_guards.py` bearer 鉴权（TG_API_TOKEN 配置时强制，/api/health 与 SSE 豁免，未配置开发直通）+ 写操作审计 api.request（X-Operator 取操作者，异常不阻断）。新路由 API-W-18/19（/investigate /verify）已入 openapi.yaml；测试基建：会话级 KB 清场（超级用户 TRUNCATE，只增表无 DELETE 授权）+ kb_document 播种改走 tg_app（INSERT 权限归属）。
>
> **Sprint 7 执行记录（2026-08-13 实测，E7-04~05 可观测 + 演示场景）**：US-E7-04/05 全完成，测试 **121/121 连续两轮全绿**（基线 110 + 场景矩阵 11；覆盖率核心内核层 aggregation 97%/investigation 96%/verification 94%/disposition 93%/knowledge 92%）。**11/11 场景矩阵** ✅：`tests/test_scenario_matrix.py` 单一取证文件收拢 SC-01~11（补齐此前缺口 SC-04 黑名单 block 把关 + SC-06 阈值热更降级 db 双校验），SC-08 含全链审计 + AA-SK-01/03/04 技能 span 齐备校验。**可观测** ✅：`app/core/tracing.py` skill_span 埋点（JSONL 落 logs/traces.jsonl + 内存复现），四内核技能（aggregation/investigation/disposition/verification）全部包裹；新路由 API-W-20 `/api/observability/traces`（limit/case_id 查询，openapi 契约同步 +TraceSpan schema）。mcp-core 补漏：signals.record/kb.apply 两处审计补 trace_id（同事务 fetchval 取案件 trace_id，避免 SQL 参数类型推断冲突）。**演示场景** ✅：`scripts/demo_playbook.py` 3/3 场景通过 22 步校验全绿——D1 SC-01 自动放行复现 / D2 SC-02+08 审批全链（velocity 簇→调查→冻结审批→决策→归档+KB 申请，四技能 span 齐备）/ D3 误报申诉回滚（故障注入→核验不一致→反向回滚→人工复核归档）；人机边界：人类动作走真实 HTTP（localhost:8200），Agent 侧处置经确定性内核直调（等价 API-M MCP 调用，演示=测试复现）。**KPI 报告** ✅：`scripts/kpi_report.py` 双范围（全量+演示）落盘 `docs/reports/kpi-report.md/.json`——KPI-02 召回率 100%（≥85% 达标）/ KPI-03 误报率演示范围 0%（≤10% 达标）/ KPI-05 留痕 100%（达标）/ KPI-04 人工介入率未达标（样本构成偏人工审批场景所致，报告如实呈现并注明全量范围含 source=TEST pytest 残留案件）。
> **Sprint 8 执行记录（2026-08-14 实测，闭环加固 + 契约对账）**：测试 **169/169 全绿**（基线 121 + 新增/修订 48：EventWorker、approval_ref 三态、ROLLBACK 豁免、中风险拒绝、actor 守卫集成、verification 三分支等）；`scripts/demo_playbook.py` **3/3 连续两轮通过、24 步校验**（主体经确定性探针预选，D1 autopilot 无人工推动）。**闭环接通（AA-CL-01/02）** ✅：新增 `app/core/event_worker.py`（DB 轮询主力 2s 扫 REGISTERED + RocketMQ 尽力而为；进程内按 case_id 单飞锁，与 /aggregate 共用；开关 TG_EVENT_WORKER 缺省 off、compose 显式 on），lifespan 挂载；rocketmq-client-python 0.5.0rc2 实测接通（case-events 4 读写队列有消息，rocketmq-init 一次性建 topic）；事件 envelope 扁平化并透传案件 trace_id。**状态机 21 迁移**（+DispositionFailed 出口、+RollbackEscalated 拆事件）：DISPOSING 死胡同修复；disposition 先转 DISPOSING 后执行 + 重试分类（E-IDEMPOTENT-CONFLICT 按成功处理）；verification 三分支（执行一致≠回滚，trace 缺失仅告警 / 不一致反向处置 / 无凭证升级 MANUAL_REVIEW）；_decide 条件 UPDATE 消除 TOCTOU。**mcp-core 准入**：approval_ref 验真（case 匹配 + requested_action 匹配或逆动作对——"批准冻结即含解冻纠错授权"，C1/DA-INV-03）；高风险 release 豁免仅限 ROLLBACK 态；40≤score<70 中风险段 E-DISP-SCOPE；建单存在性校验 E-NOT-FOUND；70 分线经 sys_config 与 web-api 同源（DB 镜像作 Nacos 降级源）。**SC-06 阈值真接线**：聚合/处置常量全部 config.snapshot() 读取（纯函数关键字缺省参兼容），Nacos 写回顺序修正（先 Nacos 后 DB），nacos_register.py 发放前读现值仅补缺键。**DB 人类把关**：trg_case_actor_gate（E-ACTOR-REQUIRED→白名单→五对 human-only）+ repositories.transition 事务内 set tg.actor。**契约对齐**：新增 API-W-21/22 与 API-M-14/15 编号；web-portal 五页字段对齐（risk_flag 恒真修复、流水线按钮接线、approval 展示 requested_action/escalated_at）；openapi.yaml 22 路径校验通过；demo_playbook 契约迁移（202 / severity 枚举 / decide / review / 分页）。**工程卫生**：db/init/06-closedloop-fix.sql 幂等迁移（新种子键 + 2027 分区 + 触发器双写 01/04）；KPI 判定按全量/演示范围分列独立判定（不以 demo 达标掩盖全量未达标）；data-generator 爆发簇改近 1 小时（velocity 窗口可实证触发）；health.py namesrv 缺省与 main.py 同源。

> **Sprint 9 执行记录（2026-08-14 实测，行业术语对齐 + 核心技术栈连通复核）**：测试 **169/169 全绿**、`scripts/demo_playbook.py` **3/3（24 步校验）**、`npm run build` 通过。**①前端行业术语对齐（R-29）**：对全部展示文案做金融风控行业语境审计——risk_case 统一「案件」（立案/结案自洽）、approval_record 改「审批单」（弃 ITSM「工单」）、kb_document 改「知识条目」（弃「文档」）、disposition_record 改「处置记录」（「凭证」系会计用语）、「事件」仅指领域事件；labels.js 12 态标签/流程指引/21 事件名/裁决路由/严重度/错误提示全量改写，五页 views + web-api 用户态报错（cases/approvals/kb/aggregation/investigation/verification/disposition/knowledge 的状态错误文案 status_zh 化）同步，术语规范写入 labels.js 头注释；保留「信号聚合/根因定位/处置执行/核验审计/知识沉淀」五阶段正式命名（全量文档统一称谓），文档层「工单」（25 处，设计建模术语 + 历史记录只续不改）不随 UI 改动。**②Skills Registry 元数据一致性（R-30）**：nacos_register.py AA-SK-05 kernel 原指向不存在的 retrospective.py → 修正 knowledge.py + kernel_note（复盘入库申请入口实为 verification.py::VerificationService._retrospective），重注册并经 Nacos 读回核账一致；顺带补齐 Nacos 缺失阈值键 br-05/br-08。**③可观测 OTLP 数据流接通（R-31，TA-C-07）**：AgentScope Studio 原已部署但零数据流入 → `app/core/tracing.py` 增 OTLP/HTTP 直推（best-effort 双通道：JSONL 留痕 + OTLP 上报，断链仅降级不阻断处置面），docker-compose 注入 TG_OTLP_ENDPOINT；按 Studio v1.0.9 源码实证混合字段语义适配 _to_otlp()（顶层 resourceSpans camelCase / 嵌套 span 字段与 attribute 值 snake_case——标准全 camelCase OTLP JSON 会被静默丢弃），case_id→traceId md5 映射 + gen_ai.conversation.id 实现按案件分组；演示复现后 Studio SQLite 实测落库技能 span 并按案件分组；Observability 外链文案如实标注连通状态（Studio/Nacos 已打通、Higress 旁路直连）。

> **Sprint 10 执行记录（2026-08-14 实测，Higress 网关接通 TA-C-04，R-32）**：`scripts/demo_playbook.py` 网关重建后复跑 **3/3（24 步校验）**、`npm run build` 通过。**①端口修正**：all-in-one 2.2.3 数据面 HTTP 实测监听容器内 8080（GATEWAY_HTTP_PORT 缺省），compose 旧映射 `8180:80` 恒不通 → 改 `8180:8080`（宿主入口实测 /api/health 200）。**②路由下发（文件仓路径，规避控制台 401）**：控制台首次初始化未完成（REST API 一律 Login required），改经容器 /data 文件仓下发——McpBridge 将 web-api/mcp-core/mcp-external-mock 注册为 dns 型服务源（dns 型要求带点域名，单标签 `web-api` 被拒 invalid domain format，故 compose 为三服务配置 `*.tg.local` 网络别名，经 Docker 内嵌 DNS 解析），Ingress `/api`（Prefix）经 `higress.io/destination: web-api.dns:8000` 指向 web-api；实测坑位：运行中改写 /data 不总被 controller watch 拾取，写入后 restart 网关全量加载最可靠。**③流量切换与取证**：web-portal/nginx.conf 上游 `web-api:8000`→`higress:8080`，门户全部 /api 业务请求与 SSE 真实过网关；取证：envoy admin 集群计数 `outbound|8000||web-api.dns.upstream_rq_200` 随门户请求递增、经网关 GET /api/cases 返回真实库数据（total/items 分页）、SSE text/event-stream 透传不缓冲。**④场景化取舍（不为用而用，避免孤岛耦合）**：web-api→mcp-core/mcp-external-mock 内核通道保持直连——处置执行是状态机关键路径（mcp-core 为唯一通道），不将网关单点耦合进处置可用性，该链路为容器内东西向调用无外部入口治理需求；三个 MCP 服务已全量注册为网关服务源，生产形态按需加路由即开，应用代码零改动。**⑤可复现**：新增 `scripts/higress_routes.py` 幂等重建（写 McpBridge+Ingress 入 /data → restart → 探活 :8180/api/health=200），应对 `down -v` 清卷；Observability 外链改「网关入口（已承载）/控制台（初始化未完成）」如实双列。如实保留项：控制台 UI :8001 首次初始化（设管理员密码）未完成，路由经文件仓下发而非控制台。

> **Sprint 11 执行记录（2026-08-14 实测，AgentTeams 协同执行真接线 TA-C-01，R-33）**：**①运行态复原**：Docker Desktop 重启后 agentteams-controller 因 docker.sock bind-mount 瞬时失败 Exited(127) 且 unless-stopped 不自愈、4 Worker 停 Sleeping——`docker start agentteams-controller` 拉起（内置 minio/tuwunel-matrix/higress/element 随 supervisord 恢复）+ `agt worker wake --name` 逐个唤醒，`agt get managers/workers` 全 Running（Manager=default + aa-ag-02~05，model=qwen3.8-max，runtime=copaw），`agt llm-preflight` passed。**②组网复原**：Worker sleep/wake 重建容器丢失手工接入的 tradeguard_tradeguard-net（解析不到 mcp-core），重新 `docker network connect` controller/manager/4 worker。**③协同执行接线**：发现 4 Worker 的 mcporter 工具桥均"No MCP servers configured"（协同执行链路断裂）——逐 Worker `mcporter config add` 注入 tg-core(<http://mcp-core:8101/mcp/)/tg-external(http://mcp-external-mock:8102/mcp/>) 两个 streamable-http MCP Server，`mcporter list` 全 healthy（tg-core 12 工具 + tg-external 3 工具在场），并实测 Worker aa-ag-02 经工具桥调用 `tg-core.query_transactions` 返回真实库内交易行（Worker→MCP→业务库端到端打通）。**④五维映射落地**：04 §4 将"协同映射声明"一行扩为五维协同→框架能力对照表（角色编排=Manager CRD+Worker SOUL 身份 / 任务拆解=Manager 分派+任务文件协议 / 上下文传递=Matrix 房间+DA-T-03+扁平事件信封 / 协同执行=Worker 经 mcporter 调 MCP / 状态追踪=状态机+RocketMQ+审计+Studio span），逐项附实测证据 + 集成深度如实声明（独立部署栈、演示场景与 Worker 同源技能内核共用状态机/MCP/审计底座、无分叉实现）。**⑤可复现**：Worker 重建会丢手工组网与容器层 mcporter 配置，固化 `scripts/agentteams_doctor.py` 幂等体检恢复（拉起 controller→唤醒 Worker→接 tradeguard-net→注入 MCP 桥→校验 12+3 工具在场），实测一次跑通全绿。AgentTeams 端口实况：controller 18001/18080/18088、manager 18888、dashboard 13000（均宿主回环）。

> **Sprint 12 执行记录（2026-08-14 实测，一键启动 + 真实数据通路验证，R-34）**：新增 `scripts/start_all.py`——无论当前服务存活与否、端口占用与否，一条命令拉起全栈并以**真实请求与真实数据**自证可用（拒绝"容器都 Up 即没问题"的幻觉）。**①启动链**：引擎预检（不可达自动拉起 Docker Desktop 并轮询就绪）→ `docker compose down`（保留数据卷）→ 逐端口清障 → `docker compose up -d` → 逐服务真实探活（pg_isready / nacos 控制台 / 双 MCP FastMCP 4xx 即活 / web-api /api/health 四组件 UP / 门户 / Studio）→ 数据就位（DB 空则 data-generator 重灌 + nacos_register 播种）→ higress_routes.py 路由重建 → agentteams_doctor.py 协同栈恢复。**②端口清障铁律（实测教训固化）**：Windows Docker Desktop 的宿主端口监听者是引擎进程 `com.docker.backend.exe`——taskkill 之整个引擎消失（首跑实测翻车）；故清障先经 `tasklist` 识别进程映像：外部进程→taskkill；Docker 进程→找归属容器（`docker ps` 解析兼容 `0.0.0.0:` 与 `127.0.0.1:` 两种发布形态）停容器释放（worker 随机端口撞线/compose 残留），合法归属者（agentteams 固定端口自持容器）保留。**③数据通路验证 C1~C9 + X1（端到端硬证据）**：C1 直连健康四组件 UP / C2 Higress 网关转发 /api/health / C3 经网关读真实案件数据（total=743）/ C4 门户 :8300/api 经网关可达 / C5 Nacos 阈值快照可读 / C6 真实立案 POST /api/alerts 202（source_type 契约枚举内取 demo_script，枚举外 422 被契约守门实测验证）/ C7 EventWorker 无人工干预自动推进至 DISPOSED / C8 审计含自动通道准入 + 处置凭证 release/executed / C9 AA-SK-01 技能 span 落库可追溯 / X1 AgentTeams 4 Worker 全 Running。实测首跑 16/20 暴露 2 真问题（引擎进程误杀 + source_type 契约外 422），修复后复跑 **20/20 全绿 exit 0**——系统可用性由真实启动取证而非文档陈述。

> **方案展示交付记录（2026-08-14）**：新增 `docs/reports/tradeguard-overview.html`——单文件自包含 HTML 幻灯片（21 页，PPT 行业标准叙事结构：定位→场景→业务模型/流程/规则/角色→Agent 与技术体系→数据流→端到端演示指引→证据墙）。**每条演示陈述均对应实测路径**：当日 demo_playbook 复跑 3/3（24 步校验，复现案件号 CASE-20260814-82064e/0e4fce/b321c7）、start_all.py 20/20、pytest 169/169、KPI 双范围分列；角色操作表逐一核对 web-portal router.js/role.js/api.js 在码实现（4 角色×5 页面×API-W 编号）。结构校验 + Edge 无头渲染实测 21 页全量呈现。

> **Sprint 13 执行记录（2026-08-15 实测，文档×代码×展示 HTML 三端一致对齐，R-35）**：对 docs/00~09、services（web-api/mcp-core/data-generator）、web-portal、docs/reports/tradeguard-overview.html 做约 130 条陈述逐项反向核实，约 30 处失配以**最齐全一端为准补齐而非降格陈述**。**①后端补齐**：`db/init/03-umodel-fallback.sql` v_graph_edge 补第四类边 SAME_CONTACT（account.contact_hash 自连接，权重恒 1，CREATE OR REPLACE 幂等并对运行库执行，回填 5 团伙共享联系方式后实测 456 条边，四类边全可实证）；investigation 补 `_cfg_int` 热读，BR-06 加分改经热键 `br-06-fraud-link-bonus`（01-schema 种子 + nacos_register THRESHOLDS + 纯函数缺省参三处同源，web-api 5s 快照实测拾取 source=nacos），01 §5「7 条规则 Nacos 热更新」由陈述变事实；`scripts/nacos_register.py` AA-MCP-01 工具元数据改为在码真实 12 工具全集（剔除 update_case_context/publish_kb_document 等未编号失真工具名）；`services/data-generator/generate.py` 团伙播种补 contact_hash（重灌即含第四类边）。**②前端补齐（消除孤岛接口与空白陈述）**：审批/审计/知识库三页接入 SSE 事件驱动防抖刷新（「全页面 SSE」成立，五页全覆盖）；案件工作台新增处置凭证区块（API-W-22 可视化消费方）与审计时间线 trace_id 展示；可观测面板新增动态阈值配置区块（API-W-16 消费方，暴露 source nacos/sys_config 降级与 updated_at）；角色切换由侧栏移入顶栏（对齐展示材料陈述）；labels.js 补 SAME_CONTACT/DISP_STATUS_META/THRESHOLD_LABELS。**③展示 HTML 修正约 20 处**：compose 9→12 服务（补 rocketmq-init）、20 万→10 万行、mcp-core 12 真实工具名单、双守护表述（case_transition_guard + case_actor_gate）、阶段 D 名称对齐 §4.1（调查/处置）、KPI 全量范围精确为 01/03/04/05 未达标仅 02 达标、真问题 3→2 处、状态机图（ROLLBACK_ESCALATED 系事件非状态、PENDING_APPROVAL 单向、AGGREGATING 无直达 MANUAL_REVIEW）、人机边界出处 01 §6、一职能一 Worker 出处 02 §1、进程内总线补慢消费者 64 槽降级丢弃注；并修复翻页 bug（.cover display:flex 覆盖 .slide display:none 致翻页恒为最后一页，改 .slide.active.cover）。**④文档回写**：01 §2.2 阶段字母对齐 §4.1（E→K、D→D/H）；skills/AA-SK-04 补分支 3（RollbackEscalated 诚实语义）；05 第十四轮 + R-35 留痕；00 版本 v1.4.9。**⑤取证（当日实测）**：视图对运行库执行成功且四类边计数实证；Nacos 补键 RESULT: OK；pytest **169/169 全绿**（4m32s；期间发现并修复 BR-06 basis 串漂移破坏 API-M-13 md5 幂等标记 1 处回归——basis 必须逐字稳定，已注释固化）；npm run build 通过（无循环依赖告警）；app 全模块导入无循环依赖；demo_playbook 复跑 **3/3 场景 24 步校验全绿**（案件号 CASE-20260815-f31387/33dd68/2d3aa0，审计计数 6/17/19 条与展示材料一致）。

> **Sprint 13 补充记录（2026-08-16，全文档表述专业化，R-36）**：对全仓做表述审计——外部活动相关的直白字样（活动阶段、需求出处、评审对象等措辞）全量语义替换为工程与业务术语：需求基线/需求原文（替代需求出处类字样）、设计阶段/实现 M1~M3/验收（替代活动阶段字样）、核心基座/核心技术栈/架构基点（替代强制性字样）、评审对象类字样移除或转为客观风险表述。共 24 文件 120+ 处，docs/00~09、skills/、services/、scripts/、README/CLAUDE.md 与展示 HTML 全覆盖；改动的标题锚点（01 §2.2、02 §3/§4/§6、03 §5.3、04 §4.1、05 §2）跨文档链接同步更新；kpi_report.py 输出文案与 kpi-report.md 同源一致。语义、证据与编号体系不变，文档体系升版 v1.4.10（00 §6 修订行 + 05 第十五轮/R-36 留痕 + 展示 HTML 封面/末页版本同步）。

> **Sprint 13 补充记录（2026-08-16，深度安全审计与全量加固，R-37）**：六领域并行安全审计（凭据与密钥/认证与访问控制/网络暴露面/依赖与供应链/注入与输入验证/数据与备份）发现九类隐患并全量修复，修复后全量回归不破坏任何业务功能与技术调用链。**①凭据治理**：全部被跟踪文件中的明文密钥（LLM Key、Nacos 互信值/鉴权 token、租户 MaaS 端点）清除；`.env`（gitignore）为唯一凭据源，入库 `.env.example` 与 `secrets/dashscope.env.example` 提供 CHANGE_ME 格式模板；`scripts/start_all.py` 新增凭证自举（缺 .env 自动生成随机强凭据：TG_API_TOKEN 64 位 hex / Nacos 互信值 / 鉴权 token）；compose 以 `${VAR:?}` fail-fast 插值，缺凭据拒绝启动。**②API 鉴权**：web-api 新增 `app/api_guards.py` bearer 守卫——全量 `/api` 校验 `Authorization: Bearer <TG_API_TOKEN>`（仅 `/api/health` 豁免，SSE 纳入鉴权），`secrets.compare_digest` 恒定时间比较，拒绝（api.denied）与请求（api.request）均落审计；门户 nginx envsubst 自动注入令牌，浏览器端零改动；`scripts/higress_routes.py` / `demo_playbook.py` 探活改从 .env 装载令牌随行携带。CORS 由 `*` 改 `TG_CORS_ORIGINS` 环境白名单。**③网络暴露面**：compose 全部宿主端口改 `127.0.0.1` 绑定（pg 5433 / nacos 8848 / higress 8001,8180 / studio 3000 / mcp 8101,8102 / web-api 8200 / portal 8300）。**④依赖 CVE 修复**：mcp SDK 1.29.0（CVE-2025-66416 DNS rebinding）、pydantic 2.13.4（随 mcp 下限对齐）、uvicorn 0.34.3 安全修订线、web-portal 基座 nginx:1.31-alpine（CVE-2026-42945 NGINX Rift）。**⑤输入验证加固**：mcp-core approval_ref 验真对 requested_action 为 NULL 失败关闭（显式检查 + 兜底查询双处）；处置金额有限且区间 [0,1e7]；apply_risk_bonus 仅受 (0,100] 整数（堵负分减分后门）；record_case_signals 分数整型化与信号结构校验；web-api X-Operator 40 字上限（E-OPERATOR-TOO-LONG）、config PUT 键名 `^br-\d{2}-…` 格式白名单与值域 [0,1e5] 前置校验。**⑥脚本加固**：check_pg 端口数字校验、data_retention 分区名标识符白名单（防 SQL 注入拼接）、update-agentteams-llm.sh 移除硬编码租户端点（仅经 secrets/ 流转）、nacos_register 凭据改 env/.env 装载且 fetch 失败关闭（网络异常中止写回防覆盖现值，HTTP 404 为首次部署正常播种）。**⑦克隆即完整启动**：新增 `scripts/volume_export.py` 导出命名卷至 `db/export/`（pg-data→data-only SQL gzip 25.1MB、higress-data→/data 快照 tar.gz，内置密钥扫描闸门检出即删除导出件并报错）；start_all 空库时自动 TRUNCATE + psql 管道恢复，恢复失败显式报错不静默回落。**⑧测试工程修复**：test_routes review/decide 装配被 compose EventWorker 经同库抢跑（案件停留 REGISTERED 窗口被自动链路驱至 DISPOSED，7/7 假失败）——`_reviewable_case` 改单事务直插 INVESTIGATING（双守护触发器均 BEFORE UPDATE OF status 不拦 INSERT，worker 仅轮询 REGISTERED 故不可见），竞态窗口清零，_set_status 补竞态警示。**⑨残留风险如实声明*... (line truncated to 2000 chars)

> **Sprint 13 补充记录（2026-08-16，第二轮复审收口，R-37）**：修复后按"再进行一轮检查，直到解决所有安全隐患"对全部修复域做六域并行复审（绕过尝试 + 残留扫描 + 活体探测），新发现并修复 6 类问题（1 CRITICAL / 2 HIGH / 2 MEDIUM / 1 LOW 组），复审直至清零，业务回归零破坏。**①CRITICAL 框架级鉴权旁路（CVE-2026-48710）**：starlette<1.0.1 的 `request.url.path` 可被恶意 `Host: x@evil.com/` 污染为 `//api/...`，绕过第一轮 bearer 守卫的 `startswith("/api/")` 而路由仍匹配（全量未鉴权）——双层设防：`api_guards.py` 改取 `request.scope["path"]`（路由同源）+ fastapi 0.141.1 / starlette 1.6.0 升级（一并覆盖 CVE-2025-62727/2026-54283/2025-54121），mcp 双服 requirements 显式钉 starlette==1.6.0；活体探针 5/5（Host 污染→401、非 ASCII 令牌→401+api.denied、无令牌→401、合法→200、health→200）。**②卷导出件私钥（HIGH）**：higress-data.tar.gz 会打入容器自签证书私钥——volume_export 增 tarfile filter 剔除 secrets/ 与 *.key/*.pem/*.crt 等，扫描闸门补 base64 封装 PEM（`LS0tLS1CRUdJTi`）/GitHub/AWS 凭据特征；再生成实证 6 KB、0 检出。**③Worker 控制台暴露（HIGH）**：AgentTeams 4 Worker 8088 控制台非回环发布——doctor 固化 `127.0.0.1:(18090+N)`=18092~18095 并新增 ensure_console_loopback 自愈（非回环即重建容器，env 经临时文件用后即删），实测 4/4 回环 OK。**④恢复原子性（MEDIUM）**：start_all 卷恢复 psql 补 `--single-transaction`（半恢复库不再被误判就位）。**⑤迁移真幂等（MEDIUM）**：db/init 01 全量 `CREATE TABLE/INDEX IF NOT EXISTS`（37 表 + 10 索引）+ sys_config 播种 `ON CONFLICT DO NOTHING`、04 `DROP TRIGGER IF EXISTS` 前置——对运行库重跑零改动实证（INSERT 0 0、sys_config=11、tables=38、triggers=3、白名单 18 行）。**⑥输入面四处收敛（LOW 组）**：config PUT 正则改 `\Z`（`$` 尾换行绕过）+ 键存在性闸门（未播种键先 400 E-CONFIG-KEY 不污染 Nacos 权威源，顺序：白名单/值域→存在性→Nacos→DB→reload）；audit basis 五写入点统一 `[:300]` 截断（不收紧契约，opinion 全文仍在 approval_record.opinion varchar(500)）；X-Operator 控制字符过滤；mcp-core 幂等键>60/审批凭证>40 列宽闸门（E-BAD-KEY/E-DISP-AUTH）。另加固 1 处测试 flake（test_repositories 尾随状态校验改"审计链无 ApprovalApproved 痕"，EventWorker 抢跑免疫）。**取证（当日实测）**：pytest **169/169 全绿**（8m00s）、demo_playbook **3/3（24 步校验）**、openapi 22 路径校验通过、导出件密钥扫描 0 检出、迁移重跑零改动、doctor 4/4 回环。详见 docs/reports/security-audit-v1.4.11.md §4。

> **Sprint 14 执行记录（2026-08-16，务实化提升——对标行业分层风控，R-39~R-45）**：按「边界→AI→韧性→风控→交付」五步提升，7 项落地：**①边界契约固化（R-39）**：新增 `app/core/ports.py`（DispositionExecutor/ExternalDataSource 两 Protocol 端口，契约写入 docstring）+ `schemas.py` TransactionEvent（上游交易事件契约，L1 实时决策输入）+ `test_ports.py` 契约测试，将「可替换性」从文档承诺固化为代码契约。**②AI 实质（R-40）**：新增 EmbeddingProvider/HypothesisRanker 端口 + `llm_adapters.py`（baseline Hash/Rule + LLM 版 LlmClient/DashScopeEmbeddingProvider/LlmHypothesisRanker，无 Key 降级）；investigation/knowledge 接线经端口调用（LLM 可插拔）；AA-AG-03 SOUL 补 LLM 推理职责 + 动态编排语义（<40 快通道/40~69 复核/≥70 深调查）。**③工程韧性（R-41）**：信号落库幂等化（确定性 signal_id md5 + mcp-core ON CONFLICT）+ EventWorker 有限重试（3 次线性退避，耗尽转人工）+ McpToolClient 连接复用（单会话 + 锁 + 失败重建）。**④评分模型抽象（R-42）**：RiskScorer 端口 + RuleRiskScorer baseline（scoring.py）+ AggregationService 接线。**⑤团伙发现（R-43）**：fn_fraud_ring 弱连通分量 SQL 函数（递归 CTE）+ investigation.py 团伙发现接线（ring_size 入 graph 返回）。**⑥可交付底线（R-44）**：.github/workflows/ci.yml（gitleaks 密钥扫描 + 契约测试/纯单元 + 前端构建三 job）+ .gitleaks.toml。**⑦离线评估（R-45）**：scripts/offline_eval.py（混淆矩阵 + precision/recall/F1 + 按月漂移监控，ground truth=account.list_flag）。契约测试 15 例全绿、collect 184 tests、纯单元 84 passed；文档体系升版 v1.4.13~v1.4.19。

> **Sprint 15 执行记录（2026-08-17，浏览器多角色协同实测与缺陷收口，R-46）**：以真实浏览器多角色操作（四轮 browser_use + 用户手测）验证系统稳健性，暴露 5 缺陷逐一根因修复，台账入库 `docs/reports/bug-ledger-20260817.md`。**①BUG-01（演示语义，A 类高危）**：API-W-21 `/api/demo/subjects` 增 severity 过滤参数（契约先行：openapi.yaml 同步枚举参数；high→list_flag∈{black,block} 名单主体、low→none 且 risk_level=0、medium/缺省保持随机兼容 v1.4.4），CaseWorkbench triggerDemo 按档位传参——"高风险"演示现恒取黑名单主体（垫分 75）必入"调查→审批"人机链。**②BUG-03（前端 UX）**：CaseWorkbench onEvent 防抖回调改 `detailVisible ? refreshDetail() : load()`，详情抽屉打开期间 SSE 事件驱动轻量刷新。**③BUG-04（前端 UX）**：Observability loadTraces 提交前 `^CASE-\d{8}-[0-9a-f]{6}$` 正则校验，短号/错格式 ElMessage.warning 拦截（消除"span 丢失"误判）。**④BUG-05（错误契约）**：main.py 注册 StarletteHTTPException 处理器，契约外路径默认 404 统一改写为 `{"code":"E-NOT-FOUND","message":…}` 契约信封（含正确契约路径指引），与 R-20 错误语义对齐；路由内显式 raise 的 code 信封原样透传互不干扰。**⑤契约顺手项**：CaseId pattern 修正 6 位 hex（示例同步）、info.description 工具数 12+3、main.py 描述对齐 22 接口、00 版本头 v1.4.11→v1.4.20。**⑥回归测试**：test_routes.py 新增 2 例（severity 过滤三分支 + 404 契约信封断言）。**⑦BUG-02（协同断链，B 类高危）待决策**：纯浏览器流审批队列空转根因为 Agent 触发自动化空档，方案甲（EventWorker INVESTIGATING 超时委托内核）/乙（门户"委托 Agent 处置"显式入口）已入台账待架构决策；本轮以 `scripts/seed_browser_case.py` 播种待审批案件支撑多角色测试。**取证（当日实测）**：pytest test_routes.py **19 passed**（56.6s，含新增 2 例）；npm run build 通过（7.7s）；web-api/web-portal 镜像重建 healthy；活体探针——用户手测原始 URL `/api/cases/CASE-20260816-75435a/events` 返回 404 + E-NOT-FOUND 信封、severity=high 主体全 black / low 全 none-0；浏览器三场景 PASS（高风险演示 CASE-…-33a244 score=75 入 INVESTIGATING；同源 fetch 审批放行后 2~4s 抽屉自动「待审批」→「已处置」且时间线新增；短案号格式告警 + 完整案号 span 查询正常）。复测方法论沉淀（误报澄清 6 条入库）：浏览器缓存击穿 URL、稳态案件无事件的观察协议。

> **Sprint 15 补充记录（2026-08-17 晚，BUG-02 方案甲落地 + BUG-06 可观测性，R-46 续）**：**①BUG-02 收口（方案甲）**：EventWorker 构造器增 investigation/disposition 可选内核，`_delegate_sweep` 以 30s 周期扫描滞留 INVESTIGATING 超 `TG_DELEGATE_INVESTIGATING_SECONDS`（compose=900s，代码缺省 0=OFF，900s > 全量 pytest 8 分钟杜绝同库竞态）的案件，单飞锁 + 锁内状态复核后代 Agent 依次执行 AA-SK-02 调查与 AA-SK-03 提交（freeze、幂等键 `<case_id>:delegate`），异常语义与 `_sweep` 同款（状态类吞咽/未知留待下轮）；main.py worker 装配注入双内核，test_event_worker.py 新增 5 例。容器实测：222 案历史积压 ~5 案/分钟持续消化，audit_log actor=agent:AA-AG-03/04 合规，待决审批工单每 ~13s 新增——纯浏览器 D2 主流程无需外部脚本。**②BUG-06（新发现，B 类低）**：容器 stdout 丢失全部应用层日志（uvicorn 默认只配 uvicorn.* logger，root 无 handler，INFO 被 lastResort 静默丢弃），main.py 模块级 `logging.basicConfig(level=TG_LOG_LEVEL 缺省 INFO)` 修复，EventWorker 生命周期与逐案委托（route=approval_required/refused_mid_risk）可直查 docker logs。**③BUG-05 复测澄清**：用户次日重测仍见默认 404 信封系 web-api 容器未随代码重建；已固化教训——后端变更后必须 `docker compose up -d --build web-api` 再验证（8300/8200 双路复测均返回契约信封）。**取证**：pytest test_event_worker + test_routes **31 passed**；docker logs 出现「EventWorker 已启动（轮询 2.0s，窗口 10 分钟，INVESTIGATING 委托 900s/30.0s）」。

> **Sprint 15 补充记录二（2026-08-17 晚，可观测面板外部组件入口点击级实测，R-46 续）**：对 /observe「外部组件入口」做浏览器点击穿透实测（两轮 browser_use + 缓存击穿复测），验证外链与系统的真实关联及角色关系。**关联实证**：AgentScope Studio Traces 含 AA-SK-02/03 技能 span 与 case_id 元信息（OTLP 链路真实）；Higress /api/health status=UP 四组件正常；Higress 控制台呈 /init 初始化引导（环境实况）；**Nacos 控制台发现 BUG-07（D 类中）**——外链 :8848/nacos 双重失效（v3 控制台在容器 8080 独立端口、/nacos 为 v2 已移除路径、容器 8080 未发布宿主且宿主 8080 被占），探测标签"可达"与实际不可用不符；修复：compose nacos 增发布 `127.0.0.1:8850:8080`、Observability.vue 外链改 `http://localhost:8850/`、start_all COMPOSE_PORTS 补 8850、CLAUDE.md 端口实况同步；复测 PASS（href=8850、进入 v3 控制台 /next/#/register 初始化密码页）。**角色关系**：/observe 唯一 `roles:[]` 全角色可见（4/4 实测），各角色专属菜单正确；运维观测对四业务角色开放属设计取舍（04 §10 定位）。误报澄清 2 条入库（浏览器缓存旧 chunk 同型、两控制台"首次初始化"属环境实况）。

依赖提示：E4 依赖 E3 的信号结构；E5 依赖 E2 的 DDL；E7-02/03 依赖 E3/E5 产生的真实事件数据。增强路线 US-E8~E13（docs/14 §5）与主线解耦并行，不改动上述 Epic 依赖链。

> **Sprint 执行记录（2026-08-20~21，US-E13 LoopEngine 环设施，docs/14 v1.3）**：四 Story 全完成（E13-01~04），全量回归 **30 文件 312 例全绿**（基线 310 + SC-19/20 矩阵 2 例）。三层环落地：L1 失败归宿（`app/core/loop_engine.py` + DA-T-16 驻车表 + E-WORKER-DLQ 第 26 事件 + API-W-25/26 双重人工门：守卫层角色白名单 403 + 端点 human_only 409）；L2 双轮有界环（planner.replan_from_gaps，MAX_REFLECT_ROUNDS=2，rounds 留痕）；L3 慢环归因（DA-T-17 proposal_attribution，attribute_rule_proposals 幂等只增）。配套：db/init/09-loop-engine.sql + 10-case-source.sql（risk_case.source_type 落库，自动环排除 TEST 源消除共享库竞态）；test_loop_engine.py 14 例（DLQ 生命周期/驻车排除/双轮补查/慢环归因/双重门契约）+ test_scenario_matrix SC-19/20；SC 矩阵自此覆盖 SC-01~20，BA 规则 BA-BR-22 环治理入 01 §5。纪律实证：驻车不改案件状态（状态机仍是迁移权威）、复位仅 human:*、环记录只增不删。

---

## 5. API 目录（统一 OpenAPI 3.0）

全量 Schema/参数/响应以 [`openapi/tradeguard-openapi.yaml`](./openapi/tradeguard-openapi.yaml) 为唯一事实来源（仓库以 spectral/openapi-validator 纳入 CI 校验）。REST 与 MCP 工具共用同一套 components.schemas，杜绝两套数据结构漂移。

### 5.1 web-api REST（API-W-x）

| 编号 | 接口 | 方法 | 页面/用途 | 场景载体 |
| --- | --- | --- | --- | --- |
| API-W-01 | `/api/alerts` | POST | 告警受理/演示触发（AA-CL-01） | SC-01~04 入口 |
| API-W-02 | `/api/cases` | GET | 事件列表 | — |
| API-W-03 | `/api/cases/{case_id}` | GET | 事件详情（含共享状态） | — |
| API-W-04 | `/api/cases/{case_id}/signals` | GET | 信号清单 | SC-04 |
| API-W-05 | `/api/cases/{case_id}/graph` | GET | 关联网络图谱 | BA-BR-06 |
| API-W-06 | `/api/cases/{case_id}/evidence` | GET | 证据链 | BA-BR-03 |
| API-W-07 | `/api/cases/{case_id}/review` | POST | 中风险人工复核 | SC-10 |
| API-W-08 | `/api/approvals` | GET | 待审批队列 | SC-02 |
| API-W-09 | `/api/approvals/{approval_id}/decide` | POST | 批准/驳回 | SC-02/03/09 |
| API-W-10 | `/api/audit/{case_id}` | GET | 审计链追溯 | SC-08 |
| API-W-11 | `/api/kb/applications` | GET | 入库申请列表 | SC-05 |
| API-W-12 | `/api/kb/applications/{doc_id}/publish` | POST | 确认发布 | SC-05 |
| API-W-13 | `/api/kb/applications/{doc_id}/reject` | POST | 驳回申请 | SC-05 |
| API-W-14 | `/api/events/stream` | GET(SSE) | 领域事件实时推送 | 全体演示 |
| API-W-15 | `/api/health` | GET | 健康检查 | US-E1-01 |
| API-W-16 | `/api/config/thresholds` | GET/PUT | 阈值配置（Nacos 热加载） | US-E1-03 |
| API-W-17 | `/api/cases/{case_id}/aggregate` | POST | 触发信号聚合（AA-SK-01） | SC-01/SC-11 |
| API-W-18 | `/api/cases/{case_id}/investigate` | POST | 触发欺诈调查（AA-SK-02） | US-E4-01~03 |
| API-W-19 | `/api/cases/{case_id}/verify` | POST | 触发结果核验（AA-SK-04） | US-E6-01/02 |
| API-W-20 | `/api/observability/traces` | GET | 技能 span 追溯（可观测取证） | US-E7-04 |
| API-W-21 | `/api/demo/subjects` | GET | 演示触发候选主体（无在办案件的账户，limit 1-50；severity 过滤：high→名单主体/low→干净主体，R-46） | US-E7-05 |
| API-W-22 | `/api/cases/{case_id}/dispositions` | GET | 处置凭证列表（receipt 反序列化，核验/审计取证） | US-E6-01/02 |
| API-W-25 | `/api/deadletter` | GET | LoopEngine DLQ 驻车清单（失败归宿可见性，parked_only 缺省 true） | US-E13/SC-19 |
| API-W-26 | `/api/deadletter/{case_id}/retry` | POST | DLQ 人工复位放行（双重门控：角色白名单 + human_only，环不得自清） | US-E13/SC-19 |
| API-W-27 | `/api/kb/ask` | POST | B 端知识问答（仅引用已发布知识，doc_id 引用对齐；未命中声明无先例；人工角色门 + kb.ask 留痕） | US-E14/SC-22 |

> **增强路线执行记录**：US-E13 LoopEngine 环设施（2026-08-20~21，docs/14 v1.3）：DA-T-16/17 两表 + EventWorker 有限重试驻车 + 双轮有界环 + 慢环归因，API-W-25/26 双重人工门，SC-19/20 绿。US-E14 RAG 深化（2026-08-21，docs/14 v1.4）：复盘升级结构化案例分析（verification._retrospective 四段）+ API-W-27 B 端问答 × AA-AG-06 知识助手 soul，SC-21/22 绿。

### 5.2 MCP 工具契约（API-M-x，Schema 见 openapi.yaml `x-mcp-tool`）

| 编号 | 工具 | Server | 权限 | 幂等 |
| --- | --- | --- | --- | --- |
| API-M-01 | `query_transactions` | AA-MCP-01 | 只读 | 是 |
| API-M-02 | `query_related_graph` | AA-MCP-01 | 只读 | 是 |
| API-M-03 | `execute_disposition` | AA-MCP-01 | 写（审批把关） | 幂等键强制 |
| API-M-04 | `query_disposition_result` | AA-MCP-01 | 只读 | 是 |
| API-M-05 | `submit_kb_application` | AA-MCP-01 | 写（仅申请） | 是 |
| API-M-06 | `query_audit_trail` | AA-MCP-01 | 只读 | 是 |
| API-M-07 | `query_credit` | AA-MCP-02 | 只读（模拟） | 是 |
| API-M-08 | `query_sentiment` | AA-MCP-02 | 只读（模拟） | 是 |
| API-M-09 | `query_complaint` | AA-MCP-02 | 只读（模拟） | 是 |
| API-M-10 | `record_case_signals` | AA-MCP-01 | 写（tg_app，信号只增+评分回写） | 是（同案重复聚合仅追加信号） |
| API-M-11 | `create_approval_request` | AA-MCP-01 | 写（tg_app，审批建单 DA-T-07） | 否（每次建单新工单，调用方幂等由编排层保证） |
| API-M-12 | `record_case_evidence` | AA-MCP-01 | 写（tg_app，证据只增 DA-T-05） | 是（同 claim+source_ref 不重复插入） |
| API-M-13 | `apply_risk_bonus` | AA-MCP-01 | 写（tg_app，BA-BR-06 关联网络加分） | 是（同案同 basis 仅生效一次，context_json 打标） |
| API-M-14 | `record_agent_memory` | AA-MCP-01 | 写（tg_app，DA-T-12 阶段执行摘要） | 是（只增） |
| API-M-15 | `query_case_signals` | AA-MCP-01 | 只读（信号聚合回查） | 是 |
| API-M-16 | `query_enterprise` | AA-MCP-02 | 只读（企业资质五维；双轨：ENTERPRISE_VENDOR_KEY 在则真实厂商开放平台，失败/无 Key 降级 mock；仅线索不裁决 BA-BR-24，US-E15） | 是 |
| API-M-17 | `pyod_iforest` | AA-MCP-02 | 只读（金额序列隔离森林离群检测；仅 advisory 参谋分不裁决 BA-BR-25；pyod/numpy 未装即 E-TOOL-UNAVAILABLE 白名单拒绝，主链无感，US-E16） | 是 |
| API-M-18 | `pyod_lof` | AA-MCP-02 | 只读（局部离群因子：小额高频簇识别；约束同 API-M-17，US-E16） | 是 |
| API-M-19 | `pyod_ecod` | AA-MCP-02 | 只读（经验累积分布：大额单笔尾部异常；约束同 API-M-17，US-E16） | 是 |

在码工具全集 = 上表 19 项（mcp-core 12 + mcp-external-mock 7），与 `services/mcp-core/server.py`、`services/mcp-external-mock/server.py` 的 `@mcp.tool()` 逐项对齐（Sprint 8 核账 + US-E16 pyod 三工具补编号，零未编号工具）。

**契约纪律**：任何接口/工具变更必须先改 openapi.yaml 再改代码（与 03 §9.4 领域事件纪律同级）；错误码统一见 [08 §6](./08-数据模型与数据字典.md#6-错误码表)。

---

## 6. 与其他文档的回接

- Story 验收标准 → SC 场景（06 §2）→ 测试层（06 §3），形成"需求→场景→测试"单链；
- API Schema 字段 ↔ 数据字典（08 §3）字段一一对应，变更双向同步；
- Epic 完成状态纳入 [05 追溯矩阵](./05-追溯矩阵与整体评审报告.md) 复审范围；
- 增强路线排期（US-E8~E14）以 [14 §5](./14-增强路线图多层分拆-4A到敏捷排期.md#5-敏捷排期us-e8e14先-p0-后-p1p2) 为权威，与本文档 Epic 链解耦并行。
