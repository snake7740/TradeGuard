# 更新日志（Changelog）

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式与语义化版本。

## [Unreleased]

### Added
- A0 端点级角色门控（`app/api_guards.py` PATH_ROLE_RULES）：review 仅审批官、decide 仅审批官/策略管理员、kb publish/reject 仅策略管理员、config 写仅策略管理员/审批官；越权 403 E-FORBIDDEN-ROLE 并 `api.forbidden` 留痕，未识别调用方 `api.unknown_actor` 留痕放行（兼容收敛节奏同 R-37）；前端 api.js 按角色携带 X-Operator；流程 E 角色边界契约测试（SC-18）
- CI 全量化：19 个测试文件全量接入（PG service + 双 MCP 服务实链路 + RocketMQ 降级），前端构建产物校验，本地全真模拟 206 例两轮全绿
- SAST 安全门禁：bandit（Python 静态分析）+ pip-audit（依赖漏洞）进 CI
- 并发/性能验证：异步压测脚本与基线报告（`scripts/perf_smoke.py` + `docs/reports/perf-report.md`）
- KPI 口径清洗运维工具 `scripts/kpi_clean.py`（dry-run / --execute，三次清洗共剔除 1564 件测试残留）
- LLM 自主性：Manager 规划-反思循环（`app/skills/planner.py`，DashScope qwen 接线 + 确定性降级保底）、处置前 Agent 互审、记忆进化量化 KPI-06
- 第二场景实证：device-guard 设备异常处置（复用 12 态状态机/审批工单/审计链的同骨架实现）
- 公开数据集回放：ULB creditcard 284,807 笔全量验证 AA-SK-01 内核（`scripts/dataset_replay.py`，无标签泄漏/对照基线/负结果如实披露，docs/11）
- Skill 自包含打包：AA-SK-01~05 迁移至 Agent Skills 生态格式（SKILL.md frontmatter + 依赖声明 + 质量指标），`scripts/skill_pack_validate.py` 防漂移门禁进 CI（test-cases 与实际 def test_ 计数零漂移）
- Skill 运行时注册表：`app/skills/loader.py` + `GET /api/skills`（API-W-24）——frontmatter 同源装载、entrypoint 导入校验、坏包留痕不阻断，skill 包从文档自包含升级为基座可发现/可分派
- R-49 动态处置分派：委托通道动作由 AG-03 调查结论（图谱影响面 + KB 佐证）动态协商（规则档位下限 + LLM 白名单降级），替换硬编码 freeze
- 开源化：Apache-2.0 LICENSE、英文 README、CONTRIBUTING、CHANGELOG
- 增强路线图 docs/14 全量集成（US-E8~E12，13 增强点，零新中间件/零状态机变更/既有不变量只增不改）：A1 自适应基线（DA-T-14 account_baseline + EWMA/分位双轨评分 BA-BR-15）、B2 三时序模式（资金回路/快进快出/夜间突发 BA-BR-17）、B1 图拓扑 topology_stats（星型/环型/二部，仅线索不裁决 BA-BR-16 + DA-INV-07）、B3 并行假设编排+「为什么没查 X」豁免留痕（BA-BR-18）、C1 控辩互审 debate_json 入审批单与审计（BA-BR-19 + DA-INV-09）、D1 专家清单预检 precheck 只读 API、E1 知识代谢 effectiveness 自动降级（BA-BR-20）、E2 rule_proposal 人审门（BA-BR-21 + DA-INV-08）、C2 disposition_outcome T+7/T+30 回填、F1 pyod 工具族白名单门禁；4 新领域事件（E-INV-HYPOTHESIS/E-REVIEW-DEBATE/E-KB-DECAY/E-OUTCOME-FOLLOW）OpenAPI SseEvent 枚举逐字同步
- 门户增强展示：审批工单控辩抽屉（控方/辩方/裁判倾向三段 + BA-BR-19 不替代人工裁决提示）、审计工作台专家清单预检六项卡片、策略工作台待审核队列+审核发布人审门
- SC-12~17 六场景 BDD 矩阵落地（test_scenario_matrix +190 行），全量金字塔回归 296 例全绿；浏览器四角色×多菜单协作完整前端路径复测通过（立案→聚合→调查→提请→控辩→批准→执行→预检→核验归档→知识沉淀人审发布）
- LoopEngine 三层环设施（环记录只增、人工门不被环绕过）：**L1 失败归宿**——EventWorker 重试耗尽入 `processing_deadletter`（DA-T-16，累计 9=3轮×3次驻车停扫 + 审计 `worker.deadletter` + E-WORKER-DLQ 环设施事件，第 26 个），人工经 `POST /api/deadletter/{case_id}/retry` 复位（human:* actor 门）恢复候选；**L2 双轮有界环**——调查反思 verdict=gaps 触发 `replan_from_gaps` 二轮补查（仅降级/缺失可行动源，上限 MAX_REFLECT_ROUNDS=2，rounds 轮次留痕进证据 claim）；**L3 慢环归因**——`proposal_attribution`（DA-T-17）观测 rule_proposal 发布后同主体是否再犯，代谢环周期内 `attribute_rule_proposals` 自动回填，规则进化环可度量；mcp-core 提案申请携带 source_case_id 溯源；复位端点双重门控：守卫层角色白名单（值班员/策略管理员，越权 403 E-FORBIDDEN-ROLE）+ 端点 human_only（agent: 自声明 409 E-HUMAN-ONLY）；新增 `db/init/09-loop-engine.sql`（tg_web 读写/tg_app 只读）与 `test_loop_engine.py` 14 例；方法论补齐（2026-08-21）：环设施按 4A→DDD→BDD→TDD→敏捷排期全层入 docs/14 v1.3——新增 BA-BR-22（环治理）入 01 §5，SC-19/20 入 BDD 矩阵（06 §2/§4，18→20 场景）与 test_scenario_matrix，US-E13 排期落地，API-W-25/26 编号入 openapi 与 07 §5.1，全量回归 30 文件 312 例全绿
- RAG 深化：案例分析语料 × B 端知识助手（docs/14 v1.4，US-E14，零新表零新事件）：**语料面**——归档复盘 `verification._retrospective` 升级结构化案例分析四段（案件概况/手法指纹（主型×分布）/处置结论/复用提示，标题携主型“手法特征”检索锚点，发布仍走 DA-INV-06 人工门）；**消费面**——`POST /api/kb/ask`（API-W-27）B 端问答：仅引用已发布知识（doc_id 引用对齐，构造性防幻觉），未命中声明“无先例”，人工角色门（agent:/未识别 403 E-FORBIDDEN-ROLE）+ kb.ask 留痕可追责；`agentteams/souls/aa-ag-06.md` 知识助手 soul（AgentTeams 首次自流水线 worker 扩展 B 端角色，只检索引用不裁决不处置不发布），agentteams_doctor 接入 aa-ag-06；方法论全链：BA-BR-23（问答治理）入 01 §5，SC-21/22 入 BDD 矩阵（06 §2/§4，20→22 场景）与 test_scenario_matrix，US-E14 排期落地，openapi 29 paths；全量回归 30 文件 315 例全绿（2026-08-21 实证：监督批跑 314 绿 + test_multi_role_flow 1 例环境性 PG 挂起复跑单绿）
- 企业资质外部源五维扩维（docs/14 v1.5，US-E15，BA-BR-24，零新表零新事件零状态机变更）：`query_enterprise` 工具（API-M-16，external 第四工具）——企业资质五维（reg_status / abnormal_ops_count / admin_penalty_12m / judicial_risk_count / related_entity_count + 合成 risk_flag）；**双轨集成**：有厂商凭据（ENTERPRISE_VENDOR_KEY/URL）走真实源（防腐层字段翻译），缺失/失败降级 mock 且 degraded+vendor_error 留痕（调用链路与生产一致，真实集成是换实现不是建新路）；planner 四源接线（无特征案件保守全查含 enterprise）+ `ExternalSourcesClient.query_enterprise` + 查询事由门 E-REASON-REQUIRED（BA-BR-10）；**仅线索不裁决**：五维入 findings 与证据链但不驱动评分与处置（继承 INV-07/BR-16 精神）；合规边界：个人征信持牌红线保留 mock，企业资质为可合规接入点；方法论全链：BA-BR-24 入 01 §5，SC-23 入 BDD 矩阵（06 §2/§4，22→23 场景）与 test_scenario_matrix（活栈 AA-MCP-02 实链路），US-E15 排期落地（Sprint 9），openapi McpQueryEnterpriseReq/Resp + API-M-16 编号入 07 §5.2；全量回归 30 文件 317 例全绿（2026-08-21 实证：看门狗监督脚本批跑——EDR 杀软间歇冻结 python 环境性灾难取证后改用拉起重试+文件级超时看门狗+冷却机制，零失败零超时）
- 社区对标分析 v1.2（docs/09，2026-08-21 行业核实）：新增 §4 代码实证功能盘点（mcp-core 12 工具 + external 7 注册 = 契约 4 + pyod 3——pyod 三工具已在码带双重门禁但无技能调用方/无契约编号，属"能力在码未接线"不计入 16 口径）、§5 行业核实数据（Stripe Radar/Featurespace/Actimize/Feedzai/GNN 学术端/Agentic AI 趋势，区分官方宣称与第三方口径）、§6 五维水准定位（编排闭环/合规审计差异化优势、工程验证领先开源基准、算法/图/集成层诚实标注落后）、§7 诚实披露六条（无模型训练管线/非毫秒级决策/合成数据/mock 为主/行业数据口径纪律）
- 能力增强路线图 v2.0（docs/13）：§6 增强点落地盘点（A1~F3 逐项代码实证：10 项已落地 / A2·D2 未落地 / F1·F2·F3 半落地——pyod 三工具无调用方无契约编号属"能力在码未接线"）；§7 补齐差距迭代升级计划（输入 = docs/09 §6 五维定位）：G1 检测算法层补强（pyod 接线闭环 + LLM 零样本建议层 + offline_eval 度量口径固化，Sprint 14 P0）、G2 外部集成层补强（query_device 设备情报第五外部源双轨 + 行为特征端口预留，Sprint 15）、G3 合规审计深化（案件审计摘要 D2 引用防幻觉 + TRiSM 对齐声明，Sprint 16）、G4 工程验证深化（端到端 p50/p95 延迟基线 + soak 长跑闭合 docs/12 扣分，Sprint 16~17）、G5 图关联 training-free 深化（二部集中度 + 连通分量团伙切分，Sprint 17 P2）；统一约束零新中间件/零状态机变更/LLM 无决策权不动，明确不采纳毫秒级流式引擎/设备指纹采集 SDK/GNN 训练；v2.1 复核补遗：发现两处真实遗漏并修复——G4-3 承接 F2/F3 生态半落地（docs/12 扣分 -2/-1）、G1-4 响应 agentic commerce 行业趋势（场景层前置），新增差距覆盖复核矩阵（docs/09 §6/§7 逐项→批次，区分能力闭合/决策性差距/维持披露）
- G1-1 pyod 接线闭环（docs/14 v1.6，US-E16，BA-BR-25，零新表零新事件零状态机变更）：金额序列统计离群检测从"能力在码未接线"升级为调查第五可选源 stat——**契约编号落地**：API-M-17/18/19（pyod_iforest/lof/ecod，工具全集 16→19：core 12 + external 7）入 07 §5.2 + openapi McpQueryStatReq/Resp；**planner 接线**：STAT_SOURCE 仅无特征保守全查路径纳入（priority=2，特征命中路径豁免留痕），不进 SOURCE_WHITELIST/LLM 提示词（LLM 只见四源）；execute_plan 增 amounts kwarg（样本 <5 跳过留痕不阻断，与工具端 ≥5 门槛对齐），stat 降级不计反思缺口、不二轮补查（建议线不空转）；**调查侧取样**：investigation.subject_amounts 经 API-M-01 流水回查近 7 日金额序列（失败返空不阻断）；CoreClient.query_transactions + ExternalSourcesClient.query_stat_outliers 适配器补齐；**仅 advisory 参谋不裁决**：输出入 findings 不改评分与处置（同 BR-16/BR-24 精神）；降级链：依赖缺失 E-TOOL-UNAVAILABLE/样本不足 E-BAD-INPUT/无理由 E-REASON-REQUIRED 三重门禁均留痕不阻断；方法论全链：BA-BR-25 入 01 §5，SC-24（含 SC-24b 豁免）入 BDD 矩阵（06 §2/§4，23→24 场景）与 test_scenario_matrix（活栈实链路，写"两种结果之一"防未来装 pyod 失配），US-E16 排期落地（Sprint 10）；全量回归 30 文件 322 例全绿（317 + 4 stat 单测 + 1 SC-24，2026-08-21 看门狗监督批跑零失败零超时）
- 新智基座缺口闭合批次（docs/12 复评 89→96 证据链，2026-08-21）：**维度3** Skill 包可安装发布——`scripts/skill_install.py`（manifest/install/verify 三子命令，纯标准库，复用 skill_pack_validate 单一事实源）+ `skills/RELEASE-MANIFEST.json`（包清单/版本/sha256/commit）+ README 安装发布章节，端到端 5/5 绿，闭合「不能被 install 消费」扣分；**维度4** soak 60min 长跑——`scripts/soak_run.py`（perf_smoke 三层读路径画像循环打压 + 容器 RSS 采样），119,385 请求零错误、内存首 96.7MB→末 105.1MB（+9% 无单调泄漏），报告 `docs/reports/soak-report.md`；覆盖率门禁 `--cov-fail-under=70` 进 Linux CI；**维度2** G4-3 第三方框架实测——`scripts/interop_probe.py`（隔离 venv .venv-interop + langgraph/langchain-mcp-adapters，不改 TradeGuard 代码）三项全过：发现 19 工具装载/LangGraph StateGraph 消费 query_related_graph 契约形态/事由门禁对第三方同样生效（E-REASON-REQUIRED，BA-BR-10）；环境坑实证：httpx trust_env 把 loopback 交给系统代理致 502 假象（NO_PROXY 即解，已注入探针）
- CI 线上多轮修复实证（门禁履职，终态全绿）：pip-audit 2.10 `--find-links` 与 `-r` argparse 互斥（指令移入 requirements 首行）；03 SQL 顺序缺陷（mv_graph_edge 须在 fn_fraud_ring 之前建，check_function_bodies 从零建库报错）；迁移 glob `0*.sql` 漏跑 10-case-source（source_type 列缺失致 49 failed）；pytest-asyncio==1.4.0 补钉（asyncio_mode=auto 执行者缺失致 fixture 链断裂）；skill frontmatter 零漂移回写（AA-SK-02 22→27/AA-SK-04 5→6）；终态：Actions run 32501628392 四 job 全绿（SAST/前端构建/密钥扫描/全量测试 4m49s，含覆盖率门禁 --cov-fail-under=70 线上实证，复评维度4 计 +0.5 闭合至 96）
- 案件治理批次：赛道对标三缺口闭合（docs/09 v1.3 §8 八条最佳实践对表，docs/14 v1.7，US-E17~19，零新表零新态）：**缺口#2 优先级队列**（BA-BR-26 / SC-25 / API-W-28）——`GET /api/cases/queue` 风险优先而非立案时序排布（risk_score DESC + 同档滞留最久置顶），分级 high/mid/low 复用 BA-BR-02/01 边界派生，aging 超期阈值 br-26-aging-hours 热配置，归档不入队；**缺口#4 STR 叙事生成**（BA-BR-27 / SC-26 / API-W-30，docs/13 D2 落地）——`POST /api/cases/{id}/narrative` 证据链唯一素材五段装配，引用 token（SIG/EV/DSP/APR）词法对齐构造性防幻觉，校验门对注入产出强制回查未对齐降级规则轨（R-49 先例），DRAFT 待人工审校不入证据链，人工角色门 + narrative.generated 留痕；**缺口#5 可治理自动关闭**（BA-BR-28 / SC-27 / API-W-29）——复用 noise 降噪通道叠加 `auto_close_eligible` 准入（零信号且金额 < br-28-auto-close-max-amount 热配置，缺省 5000），关闭留痕 case.auto_closed 带当时标准引用可复算，复位通道 `POST /api/cases/{id}/reopen`（ARCHIVED→MANUAL_REVIEW human_only，事由必填，仅值班员/策略管理员，agent: 穿透 409 E-HUMAN-ONLY）；新领域事件 CaseReopened（26→27，openapi SseEvent/docs/03 §9.2/docs/08 event_tag 三端逐字同步）；`db/init/11-case-governance.sql` 白名单迁移对 + sys_config 两键种子（IF NOT EXISTS/ON CONFLICT 幂等双跑实证）；方法论全链：BA 规则 25→28 条（01 §5）、BDD 场景 24→27（06 §2/§4 + test_scenario_matrix）、US-E17~19 排期落地；全量回归 31 文件 339 例全绿（本批新增 14 例：案件治理单测 11 + SC-25~27 矩阵 3；状态机对账基线同步抬升至 22 迁移 / 7 条 human_only 入口，2026-08-22）

### Changed
- 四角色 × 四专属工作台分化（消除「所有角色共用一个案件工作台」的归属错乱）：案件工作台归值班员专属（剥离复核 tab/复核弹窗/角色判断）；`ApprovalPortal.vue` 重写为审批官专属「复核审批工作台」（人工复核队列默认 tab + 审批工单双 tab，复核 UI 自案件工作台迁入）；审计查询/知识库分别更名审计工作台（审计员专属）/策略工作台（策略管理员专属）；`router.js` meta.roles 白名单 + `App.vue` menus roles + HOME_BY_ROLE 三层同步，可观测面板为唯一共享页

### Fixed
- fund_loop 时序回路检测恒不命中：transaction.account_hash/payee_hash 为 char(64) 读回带尾随空格、risk_case.subject_ref 为 varchar 未填充，进程内比较永不匹配——_detect_fund_loop 全链 .strip()（SC-14 取证修复；SQL 层 bpchar 自动对齐不受影响）
- topology_stats 三角形枚举只认 a→c 闭边、漏 c→a 有向环，与 docstring A→B→C→A 语义不符——investigation.py 与 mcp-core server.py 同源双修
- v_graph_edge 视图性能隐患：清理 transaction 表 20 万行 TX- 测试残留（452 万边自连接单次 25s）并 VACUUM ANALYZE
- 角色业务边界纸面化隐患：此前 API 层仅全局 Bearer 认证、X-Operator 仅作审计标识，任意角色可调用他角色写端点（生产实证值班员越权 review 返回 200）——A0 端点级 RBAC 落地后复测 403 生效；前端 UI 角色归属同步对齐：复核队列/复核按钮归审批官专属复核审批工作台、立案按钮归值班员、角色切换统一回各自首页
- D2 浏览器断链：调查→审批交接（API-W-23 提交处置端点 + UI 入口 + 一案一单防重）
- D3 审计缺口：审计员核验入口（Verification 回执 UI + 端点）
- KPI 业务口径：主体可关联账户档案 + 非 TEST 来源双保险，消除测试残留污染（KPI-01 883→0.0 分，KPI-03 50%→0%，KPI-05 98.4%→100%）
- KB 发布审计 operator 取当前登录角色（X-Operator 头三级优先级），不再固定 `human:kb_admin` 占位
- CI 依赖实质缺陷：pytest 此前不在任何 requirements——Actions 线上必红（本地"全绿"是 .venv 假象）；同版钉版 pytest==9.1.1 进安装步骤
- 全量回归两处 KB 污染型失败（KPI-06 A/B 对照、SC-DG-05）：planner 待定档第二检索词「待定 手法特征」可命中同轮前序测试留下的含该子串文档（hash embedding cosine≈0.27>0.22 阈值）——conftest 增函数级 KB 清场 fixture，A 组"无知识"前提自隔离
- 共享库自动环与测试迁移竞态（test_mcp_gate 等偶发 E-BAD-TRANSITION flake）：活栈 web-api 容器 EventWorker 每 2s 轮询共享库 REGISTERED 案件并自动聚合，与测试立案后的显式迁移争抢——`db/init/10-case-source.sql` risk_case 增列 source_type（API-W-01 入参落库），轮询/委托扫描确定性排除 TEST 源（合成案件归测试显式驱动，与 KPI 非 TEST 口径同节奏）；12 态状态机与不变量零变更
- 双轮环测试配 DashScope Key 环境下 LLM 反思非确定（temperature 0.1 外呼致 verdict 漂移）：环机制测试注入 available=False 替身隔离至确定性规则反思（同 R-49 先例，LLM 协商由注入 client 单测覆盖）
- R-49 委托通道测试在配 DashScope Key 环境下真实外呼致非确定：接线测试 monkeypatch 隔离至确定性规则档（LLM 协商由注入 client 单测覆盖）
- 规划/假设排序提示词宣称的「知识库提示」输入此前生产恒传空串（多角度 review 定位为宣称能力未接线）：investigation 规划前增 KB 预检（信号特征词），命中摘要真实进入 make_plan/rank 提示词；HypothesisRanker.kb_hints 类型修正为 str（原 list 声明与字符串拼接消费矛盾，传 list 会 TypeError 后静默降级）；新增接线断言测试（test_plan_kb_hints_grounded_from_kb）

### Changed
- 方案总览 HTML（docs/reports/tradeguard-overview.html）全面同步至 p1~p5 终态：21 页 → 25 页六部分编号体系（目录/kicker/页码三套编号归一，消除章节交叉跳跃）；业务规则补全 14 条（原仅列 10）；新增 LLM 增强与降级、质量保障、六维 KPI、五维度终评四页；痛点页增「对策 + 闭环验证（页码索引）」三层结构；P07/P08 流程框增环节徽标（与封面五阶段①~⑤同编号）与「做什么→得到什么」标注，风险分流点与三通道卡色标联动；全部数字与权威源对齐（252 例/25 路径/89 分/13 表）
- 方案总览 HTML 逻辑密度升级（25 → 26 页，对齐 docs/00~12 文档体系）：新增「BDD·DDD·TDD 验证体系」页（SC-01~11 追溯矩阵 11 行 + DDD 三件套 5 聚合/21 领域事件/6 不变量 + TDD 金字塔 + 完备性声明）；编号追溯链贯通全篇（P06 能力表挂 BA-CAP-01~07 与闭环阶段、P07 补 DA-T-01~13 全表名录、P09 规则表增守护列 14 条逐条挂 SC/INV、P17 状态机 12 态全展开主线 9+分支 3 并逐分支挂规则与场景）；各章开场增承接句（能力→岗位→技能三层对齐、P08→P16 业务/技术双视角）；三场景页标注剧本 D1~D3 与 SC 回放编号及状态机路径
- 方案总览 HTML 实感深化（咨询式理念 + 角色协同 + 技术栈组成逻辑）：P05 三条设计主线各增「机制落点」行（理念→机制→页码，咨询式 Why/How/Where）；P10 重写为「四角色 × 五 Agent 接力办案」——角色表闭环职责挂五阶段徽标，新增接力表 6 环节逐棒标注谁在做/做什么/解决什么问题（告警疲劳、多源割裂、处置无把关逐项对应痛点）+ 接口级边界卡；P14 技术栈页增 Vue 3 + Element Plus 前端行、五平面组合逻辑卡（入口 Higress→应用 FastAPI→协同 AgentTeams 故障隔离→数据 PG 一体三用→事件 RocketMQ+Nacos）与选型三原则卡（成熟开源/可替换/保守版本双通道）

## [1.4.19] - 2026-08-16

### Added
- R-37 安全纵深：常量时间比较、CORS 白名单、回环端口绑定、gitleaks、Host 头污染防护
- R-38 三端口径统一（测试/演示/全量），实现下载即完整启动
- 一键启动真实取证脚本 `scripts/start_all.py`（495 行可重入，凭据自举→探活→数据恢复→C1~C9 取证全绿）
- R-39~R-43 核心链路增强：交易事件契约、评分与假设端口化、团伙发现、幂等重试

## [1.4.x] - 2026-08-14

### Added
- Higress AI 网关统一入口接入 + OTLP 追踪增强
- 闭环处置、事件工作者、MCP 门禁、数据留存能力完善
- 前端统一设计系统（global.css 品牌主题 + 5 视图骨架）与 404 路由兜底
- 可观测 + 演示剧本：11/11 场景通过，121/121 测试绿，KPI 报告落盘
- Sprint 2~7：信号聚合、处置执行、审批回滚、调查、核验、知识沉淀全闭环

### Changed
- 项目更名 FinancialRisk → TradeGuard

## [1.4.2] - 2026-08-13

### Added
- Sprint 0：全栈技术底座（FastAPI + PG16/pgvector + RocketMQ + Nacos + Higress + AgentScope Studio）与架构设计落地
- AgentTeams Worker 接入（qwen + SOUL 身份落库 + Matrix 分派）
- DashScope Key 安全供给链路（SecureString + secrets/ gitignore + 网关环境变量注入）
- R-21/R-22 实现反哺回写；skills/ 官方技能库入库；Nacos 动态配置；恢复演练

[Unreleased]: https://github.com/snake7740/TradeGuard/compare/v1.4.19...HEAD
[1.4.19]: https://github.com/snake7740/TradeGuard/compare/v1.4.14...v1.4.19
[1.4.x]: https://github.com/snake7740/TradeGuard/compare/v1.4.2...v1.4.14
[1.4.2]: https://github.com/snake7740/TradeGuard/releases/tag/v1.4.2
