# AA-SK-03 disposition-execution · 处置建议生成与执行

> 承载：AA-AG-04（处置执行 Agent）｜ 确定性内核：services/mcp-core/server.py `execute_disposition`/`create_approval_request`（执行与建单，tg_app）+ services/web-api/app/skills/disposition.py `DispositionService`（编排与审批闭环，Sprint 3-4 落地）

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

1. **门控**：decision_type ∈ {冻结,拦截,降额} 且无 approval_ref → 直接 `E-DISP-AUTH`（SC-02 前半段）；中风险段（40-69）无凭证处置在编排层即拒 `E-DISP-SCOPE`，仅审计留痕（BA-BR-01，SC-10）；
2. **建单**：编排层收 E-DISP-AUTH → API-M-11 `create_approval_request` 建审批工单（DA-T-07）+ 案转 PENDING_APPROVAL；
3. **幂等**：idempotency_key 命中 DA-T-06 唯一约束 → 返回首次凭证不重复执行（DA-INV-03，SC-07）；批准执行的幂等键 = `case_id:action:approval_id`；
4. **执行**：同事务写 disposition_record（submitted→executed，receipt 含 approval_ref/action/amount，SC-02 审批与凭证关联落库）；
5. **审批闭环**：批准→ApprovalApproved→DispositionSubmitted→DISPOSING→DispositionExecuted→DISPOSED；驳回→ApprovalRejected→RollbackToReview 回退人工复核 + `context_json.auto_channel=disabled` 禁用自动通道（BA-BR-07，聚合裁决层持久守卫，SC-03）；
6. **时效**：BA-BR-13 升级扫描（web-api 后台任务 30s 轮询）——超 30 分钟未决工单 escalated_at 打标 + 审计 + ApprovalEscalated，门户标红（SC-09）；
7. **移交**：exec_id 交 AA-SK-04 启动 10 分钟核验计时（BA-BR-08）。

## 验收锚点

SC-02（审批门控）、SC-03（驳回回滚）、SC-07（幂等）、SC-09（时效升级）、SC-10（中风险拒自动处置）。测试载体：services/web-api/tests/test_disposition.py（95/95 绿，disposition.py 覆盖率 92%）。
