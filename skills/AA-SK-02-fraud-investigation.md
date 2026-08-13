# AA-SK-02 fraud-investigation · 欺诈根因与影响面分析

> 承载：AA-AG-03（欺诈调查 Agent）｜ 确定性内核：services/web-api/app/skills/investigation.py

## 九属性契约（02 §4）

| 属性 | 内容 |
|---|---|
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
   规则兜底：同设备多账户→团伙盗刷；夜间高频小额 transfer→跑分；单卡突发大额→盗卡；
2. **图谱扩展**：`query_related_graph(subject, [SAME_DEVICE,SAME_PAYEE,SAME_IP,SAME_GEO], hops=2)`，
   命中团伙边按 BA-BR-06 加分；
3. **影响面**：统计图内账户数/近 24h 涉险金额，生成 impact；
4. **证据固化**：每步结论 insert DA-T-05（只增，DA-INV-04）；
5. **移交**：`InvestigationCompleted` → PENDING_APPROVAL，附处置建议。

## 验收锚点

SC-05（知识引用 doc_id）、US-E4-02（BA-BR-06 加分生效）、DA-INV-04 测试。
