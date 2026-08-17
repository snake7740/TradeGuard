# 本机 Skill 库治理映射（TradeGuard 专用）

> 治理日期：2026-08-17 ｜ skill 库：`C:\Users\junzh\.agents\skills`（共 99 个顶层条目）
> 目的：为本项目每类工作钉死「该用哪个 skill」，杜绝误选与幻觉叠加；未列入本表的 skill 默认不可用于本项目交付。

## 1. 元 skill 管线（每次修改/评审强制，顺序执行）

| 序 | skill | 职责 |
|---|---|---|
| 1 | engineering-execution-protocol | 三合一执行协议（含 P0 中断恢复），最先加载 |
| 2 | meta-cognitive-evolution | 元认知层：全局业务视角、关键决策点确认 |
| 3 | engineering-first-principles | 五问门控 + 幻觉检测 + 举一反三 |
| 4 | engineering-problem-solving | 四维思维 + 减法优先（改旧不写新） |
| 5 | ao-essence-injector | 六不可协商行为 + 基于证据的完成 |

多 Agent 协作场景由 Leader 额外加载 `expert-team-orchestration`（纪律经 Prompt 合同传递给子 Agent）。

## 2. 领域 skill 分派矩阵（项目区域 → 强制/推荐 skill）

| 项目区域 / 工作类型 | 强制 skill | 推荐 skill |
|---|---|---|
| web-api 后端（FastAPI/asyncpg/状态机） | python-skill | refactoring-skill |
| REST/MCP 契约（openapi.yaml 先行，API-W/M 编号） | api-design | — |
| 测试（pytest 169 例 / BDD-TDD 06 体系） | tdd-skill | auto-testing、test-automation |
| 前端 web-portal（Vue3 + Element Plus） | vue-skill | ui-ux-expert、wcag-design |
| PostgreSQL（schema/索引/物化视图/触发器） | db-design | analytics-data-analysis（评分与 velocity 分析） |
| MCP 服务（mcp-core / external-mock） | superpowers/mcp-builder | python-skill |
| 容器编排（compose/Higress/Nacos/RocketMQ） | docker | devops-deploy（GitHub Actions CI） |
| 可观测性（OTLP → AgentScope Studio、KPI） | sre-observability/observability-stack | sre-observability/alerting-design、sli-slo-definition |
| 安全（R-37 基线、凭据、CORS、鉴权守卫） | security-engineering/api-security、security-engineering/security-code-review | threat-modeling、devsecops-pipeline、supply-chain-security |
| 文档体系交叉核对（docs 00~09 口径一致性） | doc-cross-audit | reference-tracker（跨文档引用链）、tokenized-expert-review（四真纲领审查） |
| 图谱/团伙发现/知识沉淀（UnifiedModel、KB） | knowledge-graph | ai-ml-engineering（LLM 适配器/语义 RAG） |
| 调试（状态机竞态、事件闭环、容器端口） | superpowers/systematic-debugging | — |
| 完成前验证（禁止叙述性声称） | superpowers/verification-before-completion | — |
| Git 提交与分支（中文 conventional commit） | superpowers/chinese-commit-conventions | chinese-git-workflow、chinese-code-review |
| 架构演进（ADR、4A 文档、Sprint 管理） | architecture-skill | dev-project |
| 联网调研（社区对标 09、CVE 查证） | multi-search | tavily-search、summarize |
| 报告输出（KPI/评审报告 Word/Excel/PDF 件） | office-productivity | docx、xlsx、pdf |

## 3. 明确不适用清单（防误选）

| skill | 不适用原因 |
|---|---|
| **fintech-trading**（全部 18 个子技能） | 美股交易策略（突破/配对/期权/组合），与「交易反欺诈」无关，名字近似但领域错位 |
| java-skill、mysql-skill | 本项目后端为 Python/FastAPI、数据库为 PostgreSQL，无 Java/MySQL 面 |
| next、nextjs-typescript-tailwindcss-supabase、modern-web-development | 前端为 Vue3，非 Next/React 体系 |
| novel-*、chinese-novelist、world-builder、character-forge、prose-style、story-consistency-monitor | 小说创作类 |
| auth-wechat-miniprogram、miniprogram-ci | 微信小程序类 |
| ai-assisted-music、science-ted-speech-craft、xiaohongshu-search | 创意/演讲/社媒类 |
| ecommerce、it-consulting、consulting-research、lead-research-assistant、market-research-reports | 商业咨询类，与工程交付无关 |
| render-deploy、vercel-deploy（未入库则忽略） | 部署目标为本地 Docker Compose 全栈，非云托管 |

## 4. 治理规则

1. **契约先行不变**：任何 API/工具变更先改 `docs/openapi/tradeguard-openapi.yaml`，再动 schemas/路由，领域 skill 不豁免此纪律。
2. **skill 只增不改史**：本表新增行需在 05 追溯矩阵留痕（R 编号续接）。
3. **发现误用即回写**：若执行中发现某 skill 指引与本项目实况冲突（如端口、凭据、状态机约束），以 CLAUDE.md「核心架构事实」为准，并将冲突回写本表备注。
4. 元 skill 管线输出必须附证据（测试输出/探活结果/SQL 回读），叙述性声称一律不算完成。

## 5. 治理修复记录（2026-08-17 执行）

| 问题 | 处置 | 验证 |
|---|---|---|
| 3 处 `__pycache__` 构建产物混入库（docx/multi-search/tokenized-expert-review） | 删除 | 全库复扫残留 0 |
| `memory-enhanced/node_modules` 依赖膨胀 | 删除（package.json + lock 齐全，`npm ci` 可恢复） | 同上 |
| `fintech-trading` 无顶层 SKILL.md，18 个子技能易被误当风控加载 | 补顶层 SKILL.md：领域边界声明 + 子技能索引 | 文件就位 3043 字节 |
| 全库凭据扫描（*.env/key/pem/p12） | — | 0 命中；multi-search quota/cache 文件无密钥 |
