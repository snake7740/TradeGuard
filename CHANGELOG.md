# 更新日志（Changelog）

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式与语义化版本。

## [Unreleased]

### Added
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

### Fixed
- D2 浏览器断链：调查→审批交接（API-W-23 提交处置端点 + UI 入口 + 一案一单防重）
- D3 审计缺口：审计员核验入口（Verification 回执 UI + 端点）
- KPI 业务口径：主体可关联账户档案 + 非 TEST 来源双保险，消除测试残留污染（KPI-01 883→0.0 分，KPI-03 50%→0%，KPI-05 98.4%→100%）
- KB 发布审计 operator 取当前登录角色（X-Operator 头三级优先级），不再固定 `human:kb_admin` 占位
- CI 依赖实质缺陷：pytest 此前不在任何 requirements——Actions 线上必红（本地"全绿"是 .venv 假象）；同版钉版 pytest==9.1.1 进安装步骤
- 全量回归两处 KB 污染型失败（KPI-06 A/B 对照、SC-DG-05）：planner 待定档第二检索词「待定 手法特征」可命中同轮前序测试留下的含该子串文档（hash embedding cosine≈0.27>0.22 阈值）——conftest 增函数级 KB 清场 fixture，A 组"无知识"前提自隔离
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
