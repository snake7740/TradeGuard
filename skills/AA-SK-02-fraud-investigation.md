---
name: AA-SK-02-fraud-investigation
version: 1.5.0
description: 欺诈根因与影响面分析（AG-01 规划-反思 + KB 记忆反哺 R-48）
agent: AA-AG-03
entrypoint: services/web-api/app/skills/investigation.py
depends-mcp: query_related_graph, record_case_evidence, apply_risk_bonus, record_case_signals
depends-tables: risk_signal, case_evidence, account, transaction, kb_document, kb_embedding
tests: services/web-api/tests/test_investigation.py, services/web-api/tests/test_memory_kpi.py, services/web-api/tests/test_planner.py
test-cases: 27
degradation-paths: KB 不可用仅规则假设并声明证据受限, LLM 规划/反思失败降级规则版, 图查询降级 1 跳
depth-limit: 图扩展 2 跳上限（防组合爆炸）
---

# AA-SK-02 fraud-investigation · 欺诈根因与影响面分析

> 承载：AA-AG-03（欺诈调查 Agent）｜ 确定性内核：services/web-api/app/skills/investigation.py

## 九属性契约（02 §4）

| 属性 | 内容 |
| --- | --- |
| 用途 | 匹配欺诈手法假设、扩展关联网络、输出影响面（BA-BP-03） |
| 输入 | `{case_id, signals[], risk_score}` |
| 输出 | `{hypothesis, graph{nodes,links}, impact, evidence[]}` |
| 调用条件 | 风险分 ≥40 的事件 |
| 依赖工具 | AA-MCP-01 `query_related_graph`（UnifiedModel 拓扑）；DA-KB-01 向量检索；DA-T-12 历史记忆（可缺省） |
| 失败处理 | 图查询超时→降级 1 跳；知识库不可用→仅规则假设并注明证据受限 |
| 安全边界 | 只读；图扩展深度上限 2 跳（防组合爆炸） |
| 复用价值 | 团伙识别（商户欺诈、营销反作弊） |
| 协同关系 | AA-AG-03 调用；结论供 AA-AG-04 生成处置建议 |

## 确定性执行步骤

1. **假设匹配**：按信号模式匹配手法库（DA-KB-01 检索，引用必须附 doc_id；未命中显式声明"无库内匹配"）；
   规则兜底（match_hypothesis 纯函数）：同设备多账户→团伙盗刷；velocity_1h≥10 笔且总额<5000→跑分；单卡突发大额（≥5000，BA-BR-01 同源阈值）→盗卡；无命中→待定转人工复核定性；
   **R-48 记忆反哺**：规则无法定性（待定）时以信号特征词检索 KB，命中则用库内
   手法文档升级定性（kb_note 标注反哺、citations 引 doc_id、审计 hypothesis 回放）
   ——知识沉淀→调查定性效率闭环，增益由 KPI-06 量化（A/B 实证：同源信号
   待定率 1.0→0.0，测试 test_memory_kpi.py）；未命中仍交人工（人机边界不变）；
2. **图谱扩展**：`fn_related_graph(subject_ref, 2)`（v_graph_edge 视图派生 SAME_PAYEE/SAME_DEVICE/SAME_IPSEG），
   命中黑名单主体（account.list_flag='black'）按 BA-BR-06 加分；
3. **加分**：API-M-13 `apply_risk_bonus(case_id, 30, basis)`——context_json `br06_<md5前8位>` 打标幂等不叠加，风险分封顶 100；
4. **影响面**：统计图内账户数/近 24h 涉险金额（transaction SUM），生成 impact；
5. **证据固化**：调查结论经 API-M-12 `record_case_evidence` insert DA-T-05（只增，同 claim+source_ref 幂等）；审计 investigation.complete（actor=AA-AG-03，含 trace_id）；
6. **移交**：`InvestigationCompleted` → PENDING_APPROVAL（定性仍须人工审批，02 §3.3）。

落地入口：API-W-18 `/api/cases/{case_id}/investigate`（web-api main.py 装配 InvestigationService）。

## 验收锚点

SC-05（知识引用 doc_id）、US-E4-02（BA-BR-06 加分生效且幂等）、DA-INV-04 测试。测试载体：services/web-api/tests/test_investigation.py（6 例，investigation.py 覆盖率 95%，110/110 全绿）+ test_memory_kpi.py（KPI-06 A/B 对照，R-48 反哺实证）。
