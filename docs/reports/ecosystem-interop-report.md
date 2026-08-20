# TradeGuard MCP 生态注册与互操作报告（F2/F3，docs/14 US-E12 E12-03）

> 版本：v1.0 ｜ 2026-08-20 ｜ 上游：[docs/14 增强路线图](../14-增强路线图多层分拆-4A到敏捷排期.md) §1.4 / §5 US-E12
> 纪律：生态项与代码解耦——本报告为注册元数据与互操作契约的入档凭证，不引入新中间件、不改状态机。

## 1. Registry 注册元数据

### 1.1 tradeguard-core（AA-MCP-01，端口 8101）

| 项 | 值 |
| --- | --- |
| 服务名 | tradeguard-core |
| 传输 | streamable-http（`/mcp/`） |
| 职责 | 编排层工具：图谱/黑名单/落库/知识申请（tg_app 写角色，02-roles.sql 权限矩阵） |
| 本轮契约变更 | `query_related_graph` 返回由边列表升级为 `{"edges":[...], "topology_stats":{...}}`（B1，US-E9） |
| 降级 | topology_stats 进程内计算 2s 超时返回空统计 `degraded=true`，调查不阻断（14 §1.4） |
| 白名单扩展 | `submit_kb_application` 类目增 `rule_proposal`（E2，DA-INV-08 人审门触发器守护） |

**topology_stats Schema（契约测试守护，test_mcp_gate）：**

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| nodes / edges | int | 子图规模 |
| star_density | float | 最大度/边数（星型团伙特征） |
| cycle_count | int | 三角形环路计数（资金闭环线索） |
| bipartite_concentration | float | SAME_DEVICE 二部集中度（同设备多账户） |
| suspicion | float | 0.4*star + 0.3*min(cycles,3)/3 + 0.3*bipartite（线索分） |
| degraded | bool | 超时/异常降级标记 |

**DA-INV-07 约束**：suspicion 与 LLM 建议分不得直接驱动状态迁移——裁决仅规则内核 + 人工审批（BA-BR-16）。

### 1.2 tradeguard-external-mock（AA-MCP-02，端口 8102）

| 项 | 值 |
| --- | --- |
| 服务名 | tradeguard-external-mock |
| 传输 | streamable-http（`/mcp/`） |
| 职责 | 外部源防腐层模拟：征信/舆情/投诉（确定性种子回放）+ F1 统计检测工具族 |
| 强制契约 | 全部查询工具必须携带 `query_reason`（BA-BR-10，违者 E-REASON-REQUIRED） |

**F1 pyod 工具族注册（US-E12，optional extras）：**

| 工具 | 检测器 | 适用 | 依赖 |
| --- | --- | --- | --- |
| pyod_iforest | IForest 隔离森林 | 金额序列全局离群点 | pyod+numpy |
| pyod_lof | LOF 局部离群因子 | 小额高频簇识别 | pyod+numpy |
| pyod_ecod | ECOD 经验累积分布 | 大额单笔尾部异常 | pyod+numpy |

统一行为契约：
- 样本 <5 → `E-BAD-INPUT`；contamination clamp [0.01, 0.5]；
- 依赖未安装 → `E-TOOL-UNAVAILABLE` 白名单拒绝（主链路无感，14 §1.4）；
- 检测异常 → `E-TOOL-ERROR` 降级，不抛裸错；
- 输出恒带 `advisory=true`：仅建议分，不进入裁决（与 DA-INV-07 同精神）。

## 2. 领域事件互操作（+4，OpenAPI SseEvent 枚举逐字同步）

| 事件 | 发布者 | 触发时机 | 消费方 |
| --- | --- | --- | --- |
| E-INV-HYPOTHESIS | AA-SK-02 调查 | ≥2 假设并行深查启动 | SSE 门户/可观测 |
| E-REVIEW-DEBATE | AA-SK-03 处置 | 控辩互审记录落库 | SSE 门户/审批工作台 |
| E-KB-DECAY | AA-SK-05 知识代谢 | 零引用超窗自动降级 | SSE 门户/策略工作台 |
| E-OUTCOME-FOLLOW | AA-SK-04 定时任务 | T+7/T+30 效果回填 | SSE 门户/可观测 |

互操作路径：web-api publisher（RocketMQ 优先，InMemory fan-out 降级）→ SSE `/api/events/stream` → 门户按角色订阅。事件名为自由字符串，契约锚点在 `docs/openapi/tradeguard-openapi.yaml` SseEvent 枚举（契约测试守护逐字一致）。

## 3. 互操作验证结论

| 验证项 | 结论 |
| --- | --- |
| 主链路零依赖 | pyod extras 未装、rocketmq 未装均降级明示，端到端不阻断 |
| 权限矩阵 | baseline/outcome 写权授 tg_web（web-api 定时任务），tg_app 只读（08-enhancements.sql） |
| 裁决权不变 | topology/LLM/pyod 三路建议分均不入状态机迁移（DA-INV-07） |
| 人审门 | rule_proposal 经 DA-INV-08 触发器 + DA-INV-06 发布门双守护（BA-BR-21） |
| KPI 新口径 | KPI-07 处置后 30 天再犯率（scripts/kpi_report.py，观测型） |

> 复现：`docker compose up -d` 后 `python scripts/kpi_report.py` 产出 docs/reports/kpi-report.md（含 KPI-07）。
