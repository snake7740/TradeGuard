# 06 · BDD / TDD 工程验证体系

> 本文档是 TradeGuard 4A 文档集的**工程验证视角**：[01 BA](./01-业务架构BA.md) 的规则与 [03 DA](./03-数据架构DA.md) 的不变量，在本文档转译为可执行的 BDD 验收场景与 TDD 测试策略。三者关系：**DDD 定义结构（01 §10 / 03 §9），BDD 定义行为验收，TDD 定义实现纪律**。

---

## 1. 方法论定位与三件套分工

| 方法 | 在本体系中的角色 | 产出物 | 所在文档 |
| --- | --- | --- | --- |
| DDD 战略设计 | 划定限界上下文与上下文映射，防 Agent/模块职责越界 | 7 个上下文 + 5 种映射关系 | [01 §10](./01-业务架构BA.md#10-ddd-战略设计限界上下文与上下文映射) |
| DDD 战术设计 | 定义聚合、领域事件、不变量，作为代码模块与测试的设计依据 | 5 聚合 + 8 组领域事件（10 种事件类型） + 6 不变量 | [03 §9](./03-数据架构DA.md#9-ddd-战术设计聚合领域事件与不变量) |
| BDD | 把业务规则写成 Given-When-Then 可执行验收场景，作为设计方案的行为规格、实现代码的验收标准 | SC-01～SC-24 场景目录 | 本文档 §2 |
| TDD | 红-绿-重构循环实现 Skill/状态机/MCP 契约，测试金字塔守护闭环 | 测试金字塔 + 追溯矩阵 | 本文档 §3/§4 |

对工程验收的价值：验收要求"端到端闭环证据"——BDD 场景即闭环的行为化证据，TDD 保证该证据可重复执行而非演示一次性脚本。

---

## 2. BDD 场景目录（Given-When-Then）

场景编号 `SC-x`，每个场景映射业务规则（BA-BR）、不变量（DA-INV）与闭环要素（AA-CL）。Feature 标题即业务能力。

### SC-01 低风险事件自动放行

```gherkin
Feature: BA-CAP-05 风控处置-低风险自动通道
  Scenario: SC-01 低风险小额事件自动放行
    Given 风险事件已立案且信号聚合完成，风险分 = 25
    And 涉案金额 800 元（< BA-BR-01 自动处置上限 5000 元）
    When 处置执行 Agent 调用 AA-SK-03（action=release）
    Then 事件状态流转为"已处置"并发布 DispositionExecuted 事件
    And 审计表新增一条含操作者=AA-AG-04、依据=风险分25 的记录（BA-BR-09）
    And 10 分钟内审计 Agent 完成核验（BA-BR-08）
```

映射：BA-BR-01/08/09，DA-INV-01，AA-CL-04/05/06。

### SC-02 高风险事件强制人工审批

```gherkin
Feature: AA-CL-07 审批与回滚
  Scenario: SC-02 风险分≥70 禁止自动处置
    Given 风险事件风险分 = 82，调查结论为"确认欺诈（证据链非空）"
    When 处置执行 Agent 尝试不带 approval_ref 执行冻结（AA-SK-03）
    Then 执行被拒绝并返回错误码 E-DISP-AUTH（DA-INV-02 + AA-SK-03 安全边界）
    And 系统生成审批工单并在 Matrix 房间通知审批官
    When 审批官批准并回填 approval_ref
    Then 冻结执行成功，审批记录与执行凭证关联落库
```

映射：BA-BR-02/03，DA-INV-02，AA-CL-07。

### SC-03 审批驳回回滚

```gherkin
Feature: AA-CL-07 审批与回滚
  Scenario: SC-03 驳回后回滚为人工复核
    Given 事件处于"待审批"状态
    When 审批官驳回并填写意见
    Then 事件状态回退"人工复核"且自动处置通道被禁用（BA-BR-07）
    And ApprovalRejected 事件发布，驳回意见写入 DA-T-07 与审计表
```

映射：BA-BR-07，DA-INV-01。

### SC-04 黑名单主体自动拦截

```gherkin
Feature: BA-CAP-03 风险信号聚合
  Scenario: SC-04 黑名单账户新交易自动拦截升级
    Given 账户 A 的 list_flag = black（BA-BR-04）
    When 账户 A 发起新交易触发告警立案
    Then 事件直接标记高风险，处置建议为拦截
    And 无论金额大小均进入人工审批通道（BA-BR-02 优先）
```

映射：BA-BR-04/02。

### SC-05 知识入库人工确认把关

```gherkin
Feature: BA-CAP-07 知识沉淀
  Scenario: SC-05 Agent 不得直接发布知识
    Given 事件结案且复盘摘要已生成
    When 合规审计 Agent 调用 AA-SK-05 提交入库申请
    Then 文档状态为 pending，检索接口对其不可见（DA-INV-06）
    When 策略管理员在知识库后台确认发布
    Then 状态变为 published，向量化入库，AA-SK-02 检索可匹配到
```

映射：BA-BR-11，DA-INV-06，AA-CL-08。

### SC-06 风控阈值热更新

```gherkin
Feature: 治理-动态配置
  Scenario: SC-06 Nacos 阈值变更实时生效
    Given 自动处置上限当前为 5000 元
    When 策略管理员在 Nacos 将上限改为 3000 元
    Then 处置执行 Agent 无需重启即按 3000 元判定（TA-C-05）
    And 配置变更快照写入 DA-T-11 供审计追溯
```

映射：BA-BR-01，TA-C-05。

### SC-07 处置幂等

```gherkin
Feature: AA-SK-03 处置执行
  Scenario: SC-07 重复提交不重复执行
    Given 事件 C1 已成功执行冻结，幂等键 = C1+freeze
    When 因消息重投再次以相同幂等键提交冻结
    Then 返回首次执行凭证，不产生第二条 disposition 记录（DA-INV-03）
```

映射：AA-SK-03 失败处理，DA-INV-03。

### SC-08 审计留痕完整性

```gherkin
Feature: BA-CAP-06 合规审计
  Scenario: SC-08 全链路留痕可追溯
    Given 任一事件从立案到结案
    When 合规审计员按 case_id 查询审计链
    Then 返回按时间排序的完整动作序列：立案→聚合→(调查)→审批→执行→核验→归档
    And 每条记录含操作者（Agent/人）、依据、trace_id（BA-BR-09，AA-CL-06）
```

映射：BA-BR-09，DA-INV-05，AA-CL-06。

### SC-09 审批时效升级

```gherkin
Feature: BA-BR-13 审批时效
  Scenario: SC-09 审批超时自动升级
    Given 高风险事件已生成审批工单且 30 分钟内无人处理
    When 超时定时器触发
    Then 值班 Matrix 房间收到升级提醒，审批门户工单标红
    And 升级动作写入审计表（BA-BR-09）
```

映射：BA-BR-13，AA-CL-07。

### SC-10 中风险事件禁止自动处置

```gherkin
Feature: BA-BR-01 自动处置边界（中风险分段）
  Scenario: SC-10 风险分 40–69 转人工复核
    Given 事件风险分 = 55，调查结论为"排除欺诈"
    When 处置执行 Agent 尝试自动放行
    Then 执行被拒绝，事件转入人工复核队列（事件工作台可见）
    And 不产生 disposition 记录，仅审计留痕
```

映射：BA-BR-01（中风险分段），DA-INV-01。

### SC-11 velocity 频次特征计算与填充

```gherkin
Feature: BA-BR-14 信号频次统计特征
  Scenario: SC-11 高频交易簇聚合产出 velocity 特征
    Given 主体近 1 小时发生 12 笔小额交易（合成数据集高频簇，PaySim 式分布参数）
    When 信号聚合 Skill（AA-SK-01）执行聚合
    Then 产出的 tx 源信号 velocity_json 含 velocity_1h/velocity_24h 且与流水统计一致
    And velocity 特征参与风险评分（同等信号下分数高于无 velocity 基线）
```

映射：BA-BR-14，AA-SK-01，DA-T-04 velocity_json。

### SC-12 自适应基线捕获渐进盗用

```gherkin
Feature: BA-BR-15 账户自适应基线双轨评分
  Scenario: SC-12 平稳基线突增交易入中通道
    Given 账户 30 天小额平稳基线（DA-T-14 account_baseline 已建立）
    When 突增约 8 倍金额交易并聚合
    Then baseline_dev ≥ 3.0 且双轨评分取高上调风险分
    And 全局阈值未触发亦不入自动放行通道
```

映射：BA-BR-15，DA-T-14，AA-SK-01（见 [14](./14-增强路线图多层分拆-4A到敏捷排期.md) A1）。

### SC-13 拓扑分命中团伙但不驱动处置

```gherkin
Feature: BA-BR-16 / DA-INV-07 拓扑仅线索不裁决
  Scenario: SC-13 同设备二部子图高嫌疑分不触发状态迁移
    Given 同设备 5 账户二部子图（SAME_DEVICE 边集中度 ≥ 0.8）
    When 调查查询 query_related_graph 返回 topology_stats
    Then 嫌疑分 ≥ 0.3 且 degraded=false 仅作线索展示
    And 案件状态迁移不因该分发生（risk_score 保持不变）
```

映射：BA-BR-16，DA-INV-07，AA-MCP-01。

### SC-14 时序回路命中跑分剧本

```gherkin
Feature: BA-BR-17 三时序模式进入聚合评分
  Scenario: SC-14 A→B→C→A 90 分钟内闭环命中资金回路
    Given 主体出账→一级收款方转账→回款主体 90 分钟内闭环
    When 信号聚合执行时序模式匹配
    Then temporal_json 命中 fund_loop 且评分上调 ≥ 20
```

映射：BA-BR-17，AA-SK-01 temporal_patterns。

### SC-15 并行假设留痕完备

```gherkin
Feature: BA-BR-18 并行假设与豁免留痕
  Scenario: SC-15 高风险案 ≥2 假设并行深查且豁免有痕
    Given 高风险案件含 velocity_high 与 large_amount_burst 双信号
    When 调查 planner 生成假设并并行深查
    Then E-INV-HYPOTHESIS 载荷 parallel=true 且 hypotheses ≥ 2
    And 失败/跳过假设的豁免原因（如 BA-BR-10 查询事由门）入审计留痕
```

映射：BA-BR-18，AA-SK-02 planner。

### SC-16 控辩辩论入审计不改裁决

```gherkin
Feature: BA-BR-19 / DA-INV-09 控辩互审
  Scenario: SC-16 冻结建议经控辩互审且裁决权仍归审批官
    Given 处置建议 freeze 触发 approval_required
    When AG-01 控辩互审生成 debate_json（控/辩/裁三段）
    Then 审批单含六键集辩论记录且 E-REVIEW-DEBATE 入审计
    And 最终批准/驳回仍由审批官作出（debate 仅建议）
```

映射：BA-BR-19，DA-INV-09，AA-SK-03/planner debate。

### SC-17 知识降级自动、发布人工

```gherkin
Feature: BA-BR-20/21 知识代谢与人审门
  Scenario: SC-17 零引用自动降级且 Agent 直发被拒
    Given 知识条目 30 天零引用（effectiveness 统计）
    When 代谢任务运行
    Then 条目自动转 pending 且 E-KB-DECAY 留痕
    When Agent 试图直接 publish
    Then 被拒（E-KB-HUMAN-GATE，DA-INV-06/08）
```

映射：BA-BR-20/21，DA-INV-06/08，AA-SK-05。

### SC-18 角色边界 API 层强制（A0）

```gherkin
Feature: 03 §6 权限矩阵 × BA-BR-09 端点级 RBAC
  Scenario: SC-18 越权 403 与未识别调用方留痕放行
    Given 值班员持有效令牌
    When 调审批/发布/配置端点
    Then 403 E-FORBIDDEN-ROLE 且 api.forbidden 留痕
    When 未识别调用方
    Then 放行 + api.unknown_actor 留痕（收敛节奏同 R-37）
```

映射：03 §6，BA-BR-09，app/api_guards.py（test_multi_role_flow 流程 E）。

### SC-19 环失败归宿驻车与复位人工门（LoopEngine L1）

```gherkin
Feature: BA-BR-22 环失败归宿与人工门
Scenario: SC-19 重试耗尽驻车且环不得自清
    Given 聚合环重试耗尽（累计达 DLQ 上限）
    Then 驻车 processing_deadletter + E-WORKER-DLQ 告知，轮询候选排除（不再无限重试）
    When agent 或越权角色调 /api/deadletter/{case_id}/retry
    Then 409 E-HUMAN-ONLY / 403 E-FORBIDDEN-ROLE 拒绝
    When 风控值班员复位
    Then attempts 清零放行，resolved_by=human:*（环记录只增不删）
```

映射：BA-BR-22，DA-T-16，app/core/loop_engine.py + api/deadletter.py（test_loop_engine / test_scenario_matrix SC-19）。

### SC-20 双轮有界环不空转与慢环归因可度量（LoopEngine L2/L3）

```gherkin
Feature: BA-BR-22 环有界与慢环可度量
Scenario: SC-20 反思缺口触发二轮补查且上限 2 轮
    Given 首选调查源首轮降级
    When 调查反思 verdict=gaps
    Then 二轮仅补查降级源，merge 覆盖后 sufficient，rounds 留痕 2 轮（上限）
    Given 全部源首轮成功
    Then 环一轮终止不空转
Scenario: SC-20b 规则提案发布后效果归因
    Given rule_proposal 人审发布（DA-INV-08）
    When 同主体再犯立案
    Then proposal_attribution.recurred_after=True（归因只增幂等）
```

映射：BA-BR-22，DA-T-17，app/skills/planner.py replan_from_gaps + knowledge.py attribute_rule_proposals（test_loop_engine / test_scenario_matrix SC-20）。

### SC-21 归档复盘产结构化案例分析且可检索复用（RAG 语料面）

```gherkin
Feature: BA-BR-23 案例分析语料沉淀
Scenario: SC-21 复盘四段结构化且发布即可检索命中
    Given 案件全链归档（核验一致 → ARCHIVED）
    Then 复盘申请含四段：案件概况/手法指纹（主型×分布）/处置结论/复用提示
    When 人审发布（DA-INV-06）
    Then 以主型“手法特征”检索即命中该复盘（后续调查与 B 端问答同源受益）
```

映射：BA-BR-23，DA-KB-01/02，app/skills/verification.py _retrospective（test_verification 单测 / test_scenario_matrix SC-21）。

### SC-22 B 端知识问答引用守护与人工角色门（RAG 消费面，API-W-27）

```gherkin
Feature: BA-BR-23 问答引用守护
Scenario: SC-22 命中即引用、未命中声明无先例、非人工拒绝
    Given 已发布知识条目（含案例分析）
    When 人工角色经 /api/kb/ask 问同类问题
    Then 回答 grounded=true 且 citations 含 doc_id（不虚构引用）
    When 问无关联问题
    Then grounded=false 且显式声明“无先例”
    When agent 或未识别调用方调用
    Then 403 E-FORBIDDEN-ROLE（问答可追责到人）且问答留痕 kb.ask
```

映射：BA-BR-23，DA-KB-01，app/api/kb.py /api/kb/ask + knowledge.py ask_kb × AA-AG-06（test_scenario_matrix SC-22）。

### SC-23 企业资质外部源五维扩维（双轨集成，API-M-16）

```gherkin
Feature: BA-BR-24 企业资质外部源治理
Scenario: SC-23 无特征案件保守全查纳入企业五维且仅线索不裁决
    Given 无特征案件（无信号特征组合命中，走保守全查路径）
    When 调查执行（AG-01 规划-执行）
    Then enterprise 源五维齐备入 findings 与证据链（工商状态/经营异常/行政处罚/司法涉诉/关联集中度 + 合成 risk_flag）
    And 评分与状态迁移不受 risk_flag 影响（仅线索不裁决，同 BR-16/INV-07 精神）
    And query_reason 缺失拒绝 E-REASON-REQUIRED（BA-BR-10 查询事由门）
    And 同主体确定性回放一致（无厂商 Key 默认 mock 轨；厂商 Key 在则真实轨，异常降级 mock 且 degraded 留痕）
```

映射：BA-BR-24，BA-BR-10，mcp-external-mock/server.py query_enterprise × planner.py 四源白名单（test_planner 分派单测 / test_scenario_matrix SC-23 走活栈 AA-MCP-02）。

### SC-24 统计异常检测建议线第五源（可选源接线，API-M-17~19）

```gherkin
Feature: BA-BR-25 统计异常检测建议线治理
Scenario: SC-24 无特征案件保守全查纳入 stat 可选源且仅建议不裁决
    Given 无特征案件（无信号特征组合命中，走保守全查路径）
    When 调查执行（AG-01 规划-执行）
    Then stat 源入计划与 findings：检测可用时 advisory 参谋分留痕（仅线索）；依赖缺失/样本不足时跳过留痕（E-TOOL-UNAVAILABLE）不阻断主链
    And 评分与处置不受 stat 输出驱动（建议不裁决，同 BR-16/BR-24 精神）
Scenario: SC-24b 特征命中路径豁免 stat 源
    Given 特征命中案件（高频小额/同设备/大额任一命中）
    When 调查规划（规则版）
    Then 计划不含 stat 源，豁免留痕「为什么没查」可回放
```

映射：BA-BR-25，BA-BR-10，mcp-external-mock/server.py pyod_iforest/lof/ecod × planner.py stat 可选源（test_planner 分派/降级单测 / test_scenario_matrix SC-24 走活栈 AA-MCP-02）。

---

## 3. TDD 测试金字塔

| 层级 | 范围 | 关键技术 | 数量级目标 |
| --- | --- | --- | --- |
| 单元测试 | 降噪合并算法、风险评分加权、velocity 频次统计（BA-BR-14）、状态机迁移（DA-INV-01）、幂等键判定（DA-INV-03）、证据链校验（DA-INV-04） | Pytest；纯函数优先，无外部依赖 | ≥60% 行覆盖（核心域模块） |
| 契约测试 | AA-MCP-01/02 工具 Schema 校验、错误码表、重试与降级行为；领域事件 Schema（03 §9.2） | Pytest + JSON Schema 校验 | 每个 MCP 工具 ≥2 用例（成功/失败） |
| 集成测试 | 事件发布-订阅端到端（进程内总线必达 + RocketMQ 尽力而为，03 §9.2）、PolarDB 权限矩阵（03 §6，DA-INV-05）、RAG 检索匹配与引用对齐 | docker compose 活栈测试环境（先起栈后 pytest） | 覆盖全部 26 事件名的发布侧语义（含 E-WORKER-DLQ 驻车告知） |
| 场景测试（E2E） | SC-01～SC-24 全量，经 compose 活栈真实链路执行（Sprint 8 实现修订：pytest 场景矩阵 `tests/test_scenario_matrix.py` 承载断言，demo_playbook 以真实 HTTP + MCP 复现同源场景，未采用 Matrix 房间断言） | pytest + 活栈 HTTP/MCP 探针 | 23/23 业务场景 + SC-18 角色门控通过为验收线 |
| 评估测试 | BA-KPI-01~05 离线评估（响应时效/召回率/误报率/人工介入率/留痕完整率） | `scripts/kpi_report.py` 双范围（全量/演示）分列判定，落盘 docs/reports/ | 固化于仓库，可复现 |

**先测后码原则**：Skill 与状态机代码必须先有失败测试（红）再实现（绿）；LLM 相关断言采用"行为断言"（输出结构、引用 doc_id 存在性、审批准入触发）而非文本精确匹配，规避 LLM 不确定性。

---

## 4. 场景-规则-测试追溯矩阵

| BDD 场景 | BA 规则 | DA 不变量 | 测试层 | 闭环要素 |
| --- | --- | --- | --- | --- |
| SC-01 | BR-01/08/09 | INV-01 | 场景+集成 | CL-04/05/06 |
| SC-02 | BR-02/03 | INV-02 | 场景+单元 | CL-07 |
| SC-03 | BR-07 | INV-01 | 场景 | CL-07 |
| SC-04 | BR-04/02 | — | 场景 | CL-01/04 |
| SC-05 | BR-11 | INV-06 | 场景+集成 | CL-08 |
| SC-06 | BR-01（配置面） | — | 集成 | — |
| SC-07 | — | INV-03 | 单元+契约 | CL-04 |
| SC-08 | BR-09/10 | INV-05 | 场景+集成 | CL-06 |
| SC-09 | BR-13 | — | 集成 | CL-07 |
| SC-10 | BR-01（中风险） | INV-01 | 场景+单元 | CL-04 |
| SC-11 | BR-14 | — | 单元+场景 | CL-02 |
| SC-12 | BR-15 | INV-01 | 单元+场景 | CL-02 |
| SC-13 | BR-16 | INV-07 | 契约+场景 | CL-02 |
| SC-14 | BR-17 | INV-01 | 单元+场景 | CL-02 |
| SC-15 | BR-18 | INV-01 | 集成 | CL-06 |
| SC-16 | BR-19 | INV-09 | 集成+场景 | CL-06/07 |
| SC-17 | BR-20/21 | INV-06/08 | 集成+场景 | CL-08 |
| SC-18 | 03 §6 × BR-09 | —（A0） | 契约（流程 E） | CL-07 |
| SC-19 | BR-22 | INV-06/08（人工门语义延伸） | 集成+场景 | CL-07 |
| SC-20 | BR-22 | INV-05/09（只增语义延伸） | 集成+场景 | CL-06/08 |
| SC-21 | BR-23 | INV-06（发布人工门延伸） | 单元+场景 | CL-06/08 |
| SC-22 | BR-23 | —（引用守护为端点约束） | 场景 | CL-06/07 |
| SC-23 | BR-24/BR-10 | —（仅线索不裁决，同 INV-07 精神） | 单元+场景（活栈 MCP） | CL-02/06 |
| SC-24 | BR-25/BR-10 | —（仅建议不裁决，同 INV-07 精神） | 单元+场景（活栈 MCP） | CL-02/06 |

**完备性声明**：25 条 BA 规则中，阈值类规则 BR-06 由评分单元测试覆盖，BR-05 高频异常规则由专项测试（tests/test_br05_high_freq.py）覆盖触发/未触发/阈值可配置三个分支，BR-12 数据保留为运维策略不在测试范围；其余规则均有场景或单元级测试承载（BR-22 环治理由 test_loop_engine 14 例 + SC-19/20 承载，BR-23 问答治理由 SC-21/22 + test_verification 结构化复盘单测承载，BR-24 企业资质外部源由 SC-23 + test_planner 分派单测承载，BR-25 统计建议线由 SC-24 + test_planner 分派/降级单测承载）；9 条 DA 不变量 100% 有测试映射。

---

## 5. TDD 实施纪律与分阶段 DoD

| 阶段 | DoD（完成定义） |
| --- | --- |
| 设计阶段 | BDD 场景目录（本文档 §2）作为方案行为规格；不要求代码 |
| 实现 M1 | 最小闭环：Manager 分派→Worker 调 MCP→状态流转；契约测试 + SC-01 通过 |
| 实现 M2 | 审批链路：SC-02/SC-03 通过；状态机与幂等单元测试全绿 |
| 实现 M3 | 知识与审计：SC-05/SC-08 通过；集成测试全绿 |
| 验收 | 21/21 业务场景 + SC-18 角色门控通过 + 评估脚本输出 KPI 报告 + Demo 场景与场景测试同源（演示即测试复现） |

**纪律**：任何场景测试失败不得发布；演示 Demo 的场景数据与场景测试夹具同源，保证"现场演示 = 已验证行为"，杜绝演示专用旁路代码。

---

## 6. 与其他文档的回接

- 场景新增/修改 → 同步更新 §4 追溯矩阵与 [05 追溯矩阵](./05-追溯矩阵与整体评审报告.md)；
- 新增业务规则必须同时产出 BA-BR 条目与至少一个 SC 场景（无场景的规则视为未定义）；
- 不变量变更须先改 [03 §9.3](./03-数据架构DA.md#93-聚合不变量invariants代码与测试必须守护) 再改本文档映射。
