# MCP 服务设计（AA-MCP-01 / AA-MCP-02）

> **这是什么 / 给谁看**：两个 MCP Server（业务库 mcp-core + 外部源 mock）——Agent 读写数据的「唯一手脚」通道。
> 面向**后端开发者 / Agent 编排者**。零基础请先读[根 README](../../README.md)，再读 [docs/02 §5](../../docs/02-应用架构AA.md) 了解 MCP 工具契约。
> 启动：随 compose 全栈起（:8101/:8102）；代码入口 `services/mcp-core/server.py` 与 `services/mcp-external-mock/server.py`。

两个 MCP Server 均走 streamable-http（mcp SDK ≥1.9），工具全集对齐 07 §5.2 API-M-01~15。
Agent（AgentTeams Worker）经 Higress 网关发现并调用本层，是"Agent 手脚"的唯一数据通道
（当前演示环境网关未路由，旁路直连 :8101/:8102，见 04 §5 落地实况）。

## mcp-core（AA-MCP-01，:8101）业务库 MCP · 在码 12 工具

| 工具 | 契约编号 | 语义 | 关键守护 |
| --- | --- | --- | --- |
| query_transactions | API-M-01 | 主体流水回查 | 只读 |
| query_related_graph | API-M-02 | 关联图谱 | 2 跳上限（AA-SK-02 安全边界），UnifiedModel 退化路径 fn_related_graph |
| execute_disposition | API-M-03 | 处置执行 | 审批把关 approval_ref 验真（case 匹配 + requested_action 匹配或逆动作对，DA-INV-02/03）；幂等键 E-IDEMPOTENT-CONFLICT；中风险段 E-DISP-SCOPE；release 豁免仅 ROLLBACK 态；70 分线经 sys_config 同源 |
| query_disposition_result | API-M-04 | 处置结果回查 | 只读（AA-SK-04 核验依据），未匹配返回 E-NOT-FOUND |
| submit_kb_application | API-M-05 | 知识入库申请 | 仅写 pending，发布必须人工（DA-INV-06） |
| query_audit_trail | API-M-06 | 审计链追溯 | 只读 |
| record_case_signals | API-M-10 | 信号落库 + 评分回写 | tg_app 写角色，信号只增，同案重复聚合仅追加（DA-INV-05） |
| create_approval_request | API-M-11 | 审批建单 | tg_app 写角色，案件存在性校验 E-NOT-FOUND，requested_action/requested_amount 随单写入（DA-T-07） |
| record_case_evidence | API-M-12 | 证据固化 | tg_app 写角色，同 claim+source_ref 幂等不重复插入（DA-T-05 只增） |
| apply_risk_bonus | API-M-13 | BA-BR-06 关联网络加分 | tg_app 写角色，同案同 basis 仅生效一次，context_json 打标 |
| record_agent_memory | API-M-14 | Agent 阶段执行摘要 | tg_app 写角色，只增（DA-T-12） |
| query_case_signals | API-M-15 | 信号聚合回查 | 只读（AA-SK-02 内部依赖） |

## mcp-external-mock（AA-MCP-02，:8102）外部数据源模拟 · 在码 3 工具

| 工具 | 契约编号 | 语义 |
| --- | --- | --- |
| query_credit | API-M-07 | 征信模拟（credit_score 350-850，high/mid/low 分段） |
| query_sentiment | API-M-08 | 舆情模拟（触发概率 0.3，confidence 0.4-0.9） |
| query_complaint | API-M-09 | 投诉模拟（0-2 条） |

设计要点：

- **防腐层（ACL）**：外部源异构字段统一翻译为信号聚合可消费的结构，
  生产替换真实源时只改本服务，Agent 与信号表结构不动；
- **确定性模拟**：`_seed(prefix+subject)` = `random.Random(int(sha256(prefix+subject)[:8],16))`，
  同一主体多次查询结果一致（演示可复现，场景主体经本地探针预选即复刻此播种）；
- **查询事由强制**：query_reason 为空即拒（BA-BR-10，E-REASON-REQUIRED），
  与 risk_signal.query_reason NOT NULL 形成链路留痕；
- **降级标记**：响应恒含 degraded 字段，为 TA 层熔断降级预留语义（04 §6）。

## 已交付（原 TODO）

- ✅ execute_disposition 同事务内置状态回写 executed + receipt（含 approval_ref 关联，SC-02）；
- ✅ 审批建单/证据固化/信号落库/关联加分/Agent 记忆五类写工具补齐（Sprint 3-8）；
- ⚠️ Higress 路由注册与限流策略：网关已部署但演示环境未路由（旁路直连），见 04 §5 落地实况。
