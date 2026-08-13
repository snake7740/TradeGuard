# AA-SK-03 disposition-execution · 处置建议生成与执行

> 承载：AA-AG-04（处置执行 Agent）｜ 确定性内核：services/mcp-core/server.py `execute_disposition`

## 九属性契约（02 §4）

| 属性 | 内容 |
|---|---|
| 用途 | 生成拦截/冻结/降额/放行建议并按边界执行或提交审批（BA-BP-01 D/G/H） |
| 输入 | `{case_id, decision_type, amount, approval_ref?}` |
| 输出 | `{execution_id, status, receipt}` |
| 调用条件 | 低风险自动执行；高风险须携带 approval_ref |
| 依赖工具 | AA-MCP-01 `execute_disposition`（幂等键=case_id+decision_type） |
| 失败处理 | 执行失败重试 2 次→回退"待处置"转人工；幂等键防重复扣冻 |
| 安全边界 | 无 approval_ref 且触及 BA-BR-01/02 边界→拒绝并返回 E-DISP-AUTH |
| 复用价值 | 授信类处置场景通用执行器 |
| 协同关系 | AA-AG-04 调用；执行凭证交 AA-AG-05 核验 |

## 确定性执行步骤

1. **门控**：decision_type ∈ {冻结,拦截,降额} 且无 approval_ref → 直接 `E-DISP-AUTH`（SC-02 前半段）；
2. **幂等**：idempotency_key = `case_id:decision_type`，命中 DA-T-06 唯一约束 → 返回原凭证不重复执行（DA-INV-03，SC-07）；
3. **执行**：写 disposition_record（status=EXECUTED，receipt 含执行时间/操作方/参数快照）；
4. **迁移**：`DispositionExecuted` → DISPOSED，审计留痕；
5. **移交**：execution_id 交 AA-SK-04 启动 10 分钟核验计时（BA-BR-08）。

## 验收锚点

SC-02（审批门控）、SC-07（幂等）、SC-03（驳回回滚）。
