# MCP 服务设计（AA-MCP-01 / AA-MCP-02）

两个 MCP Server 均走 streamable-http（mcp SDK ≥1.9），工具全集对齐 07 §5.2 API-M-01~09。
Agent（AgentTeams Worker）经 Higress 网关发现并调用本层，是"Agent 手脚"的唯一数据通道。

## mcp-core（AA-MCP-01，:8101）业务库 MCP

| 工具 | 契约编号 | 语义 | 关键守护 |
|---|---|---|---|
| query_transactions | API-M-01 | 主体流水回查 | 只读 |
| query_related_graph | API-M-02 | 关联图谱 | 2 跳上限（AA-SK-02 安全边界），UnifiedModel 退化路径 fn_related_graph |
| execute_disposition | API-M-03 | 处置执行 | 审批门控（DA-INV-02，E-DISP-AUTH）+ 幂等键（DA-INV-03，E-IDEMPOTENT-CONFLICT） |
| query_disposition_result | API-M-04 | 处置结果回查 | 只读（AA-SK-04 核验依据） |
| submit_kb_application | API-M-05 | 知识入库申请 | 仅写 pending，发布必须人工（DA-INV-06） |
| query_audit_trail | API-M-06 | 审计链回放 | 只读 |
| query_case_signals | 辅助件 | 信号聚合读 | AA-SK-02 内部依赖，非契约项 |

## mcp-external-mock（AA-MCP-02，:8102）外部数据源模拟

| 工具 | 契约编号 | 语义 |
|---|---|---|
| query_credit_report | API-M-07 | 征信模拟 |
| query_sentiment | API-M-08 | 舆情模拟 |
| query_complaints | API-M-09 | 投诉模拟 |

设计要点：
- **防腐层（ACL）**：外部源异构字段统一翻译为信号聚合可消费的结构，
  生产替换真实源时只改本服务，Agent 与信号表结构不动；
- **确定性模拟**：subject_id 哈希播种随机数，同一主体多次查询结果一致（演示可复现）；
- **查询事由强制**：query_reason 为空即拒（BA-BR-10，E-REASON-REQUIRED），
  与 risk_signal.query_reason NOT NULL 形成链路留痕；
- **降级标记**：响应恒含 degraded 字段，为 TA 层熔断降级预留语义（04 §6）。

## TODO（Sprint 1+）

- execute_disposition 调真实处置渠道回执后状态回写 executed（US-E4-04）
- Higress 路由注册与限流策略（US-E1-03）
