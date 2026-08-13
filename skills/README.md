# TradeGuard Agent Skills（官方技能库，赛题 2.1 必选）

本目录是 [02 §4 Skill 清单](../docs/02-应用架构AA.md#4-skill-清单赛题-21-必选9-属性全量) 的落地产物：
5 个官方 Agent 技能的可执行定义，供 AgentTeams Worker（AA-AG-02~05）加载执行，
元数据经 `scripts/nacos_register.py` 注册到 Nacos Skills Registry（TA-C-05，US-E1-03）。

| 技能 | 文件 | 承载 Agent | 确定性实现 |
|---|---|---|---|
| AA-SK-01 signal-aggregation | [AA-SK-01-signal-aggregation.md](./AA-SK-01-signal-aggregation.md) | AA-AG-02 | services/web-api/app/skills/aggregation.py |
| AA-SK-02 fraud-investigation | [AA-SK-02-fraud-investigation.md](./AA-SK-02-fraud-investigation.md) | AA-AG-03 | services/web-api/app/skills/investigation.py |
| AA-SK-03 disposition-execution | [AA-SK-03-disposition-execution.md](./AA-SK-03-disposition-execution.md) | AA-AG-04 | services/mcp-core/server.py execute_disposition |
| AA-SK-04 compliance-audit | [AA-SK-04-compliance-audit.md](./AA-SK-04-compliance-audit.md) | AA-AG-05 | services/web-api/app/skills/verification.py |
| AA-SK-05 knowledge-sedimentation | [AA-SK-05-knowledge-sedimentation.md](./AA-SK-05-knowledge-sedimentation.md) | AA-AG-05 | services/web-api/app/skills/retrospective.py |

执行纪律：
1. 每个技能先跑**确定性规则内核**（可单测、可回放），LLM 仅做推理增强层——无 Key 时闭环不断；
2. 技能 I/O 契约与 openapi components.schemas 同源，杜绝两套数据结构漂移；
3. 技能调用全部携带事由（reason）与 trace_id，落审计（BA-BR-09/10）。
