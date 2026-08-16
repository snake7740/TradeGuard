# 06 · BDD / TDD 工程验证体系

> 本文档是 TradeGuard 4A 文档集的**工程验证视角**：[01 BA](./01-业务架构BA.md) 的规则与 [03 DA](./03-数据架构DA.md) 的不变量，在本文档转译为可执行的 BDD 验收场景与 TDD 测试策略。三者关系：**DDD 定义结构（01 §10 / 03 §9），BDD 定义行为验收，TDD 定义实现纪律**。

---

## 1. 方法论定位与三件套分工

| 方法 | 在本体系中的角色 | 产出物 | 所在文档 |
| --- | --- | --- | --- |
| DDD 战略设计 | 划定限界上下文与上下文映射，防 Agent/模块职责越界 | 7 个上下文 + 5 种映射关系 | [01 §10](./01-业务架构BA.md#10-ddd-战略设计限界上下文与上下文映射) |
| DDD 战术设计 | 定义聚合、领域事件、不变量，作为代码模块与测试的设计依据 | 5 聚合 + 8 组领域事件（10 种事件类型） + 6 不变量 | [03 §9](./03-数据架构DA.md#9-ddd-战术设计聚合领域事件与不变量) |
| BDD | 把业务规则写成 Given-When-Then 可执行验收场景，作为设计方案的行为规格、实现代码的验收标准 | SC-01～SC-11 场景目录 | 本文档 §2 |
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

---

## 3. TDD 测试金字塔

| 层级 | 范围 | 关键技术 | 数量级目标 |
| --- | --- | --- | --- |
| 单元测试 | 降噪合并算法、风险评分加权、velocity 频次统计（BA-BR-14）、状态机迁移（DA-INV-01）、幂等键判定（DA-INV-03）、证据链校验（DA-INV-04） | Pytest；纯函数优先，无外部依赖 | ≥60% 行覆盖（核心域模块） |
| 契约测试 | AA-MCP-01/02 工具 Schema 校验、错误码表、重试与降级行为；领域事件 Schema（03 §9.2） | Pytest + JSON Schema 校验 | 每个 MCP 工具 ≥2 用例（成功/失败） |
| 集成测试 | 事件发布-订阅端到端（进程内总线必达 + RocketMQ 尽力而为，03 §9.2）、PolarDB 权限矩阵（03 §6，DA-INV-05）、RAG 检索匹配与引用对齐 | docker compose 活栈测试环境（先起栈后 pytest） | 覆盖全部 21 事件名的发布侧语义 |
| 场景测试（E2E） | SC-01～SC-11 全量，经 compose 活栈真实链路执行（Sprint 8 实现修订：pytest 场景矩阵 `tests/test_scenario_matrix.py` 承载断言，demo_playbook 以真实 HTTP + MCP 复现同源场景，未采用 Matrix 房间断言） | pytest + 活栈 HTTP/MCP 探针 | 11/11 通过为验收线 |
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

**完备性声明**：14 条 BA 规则中，阈值类规则 BR-06 由评分单元测试覆盖，BR-05 高频异常规则由专项测试（tests/test_br05_high_freq.py）覆盖触发/未触发/阈值可配置三个分支，BR-12 数据保留为运维策略不在测试范围；其余规则均有场景或单元级测试承载；6 条 DA 不变量 100% 有测试映射。

---

## 5. TDD 实施纪律与分阶段 DoD

| 阶段 | DoD（完成定义） |
| --- | --- |
| 设计阶段 | BDD 场景目录（本文档 §2）作为方案行为规格；不要求代码 |
| 实现 M1 | 最小闭环：Manager 分派→Worker 调 MCP→状态流转；契约测试 + SC-01 通过 |
| 实现 M2 | 审批链路：SC-02/SC-03 通过；状态机与幂等单元测试全绿 |
| 实现 M3 | 知识与审计：SC-05/SC-08 通过；集成测试全绿 |
| 验收 | 11/11 场景通过 + 评估脚本输出 KPI 报告 + Demo 场景与场景测试同源（演示即测试复现） |

**纪律**：任何场景测试失败不得发布；演示 Demo 的场景数据与场景测试夹具同源，保证"现场演示 = 已验证行为"，杜绝演示专用旁路代码。

---

## 6. 与其他文档的回接

- 场景新增/修改 → 同步更新 §4 追溯矩阵与 [05 追溯矩阵](./05-追溯矩阵与整体评审报告.md)；
- 新增业务规则必须同时产出 BA-BR 条目与至少一个 SC 场景（无场景的规则视为未定义）；
- 不变量变更须先改 [03 §9.3](./03-数据架构DA.md#93-聚合不变量invariants代码与测试必须守护) 再改本文档映射。
