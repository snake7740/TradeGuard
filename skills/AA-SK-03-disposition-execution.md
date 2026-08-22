---
name: AA-SK-03-disposition-execution
version: 1.5.0
description: 处置建议生成与执行（含 AG-01 合规互审 R-47，人机边界不变）
agent: AA-AG-04
entrypoint: services/web-api/app/skills/disposition.py
depends-mcp: execute_disposition, create_approval_request, record_case_evidence
depends-tables: disposition_record, approval_record, case_evidence, audit_log
tests: services/web-api/tests/test_disposition.py, services/web-api/tests/test_cross_review.py, services/web-api/tests/test_dynamic_dispatch.py
test-cases: 23
degradation-paths: 重试耗尽 DispositionFailed 转人工无死胡同, LLM 互审降级规则分档, 幂等重投按首次凭证处理
depth-limit: 重试退避 0.3s/1s 共 2 次，确定性错误码不重试
---

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
| 失败处理 | 提交先转 DISPOSING 再执行；失败按错误分类重试（网络/未分类错误退避 0.3s/1s 共 2 次；E-IDEMPOTENT-CONFLICT 按成功处理返回首次凭证 route=executed；E-DISP-AUTH/E-DISP-SCOPE/E-EVIDENCE-MISSING 不重试）；重试耗尽→DispositionFailed→MANUAL_REVIEW（无死胡同）；幂等键防重复扣冻 |
| 安全边界 | 无 approval_ref 且触及 BA-BR-01/02 边界→拒绝并返回 E-DISP-AUTH；mcp-core 侧 approval_ref 验真（decision/case 匹配 + requested_action 匹配或逆动作对）；高风险 release 豁免仅限 ROLLBACK 态；建单前案件存在性校验 E-NOT-FOUND |
| 复用价值 | 授信类处置场景通用执行器 |
| 协同关系 | AA-AG-04 调用；建单前经 AG-01 合规互审（R-47，只建议不决策）；委托通道动作经 AG-01 动态分派协商（R-49：AG-03 图谱影响面 + KB 佐证 → block/freeze/reduce，规则档下限 + LLM 白名单降级，替换硬编码 freeze）；执行凭证交 AA-AG-05 核验 |

## 确定性执行步骤

1. **门控**：decision_type ∈ {冻结,拦截,降额} 且无 approval_ref → 直接 `E-DISP-AUTH`（SC-02 前半段）；中风险段（40≤score<70）无凭证**任何处置动作（含 release）**编排层与 mcp-core 双层拒绝 `E-DISP-SCOPE`，仅审计留痕（BA-BR-01「一律转人工复核，不得自动处置」，SC-10；核验回滚反向 release 携凭证走逆动作对豁免）；70 分线经 sys_config 双端同源（SC-06）；
2. **建单**：编排层收 E-DISP-AUTH → **AG-01 合规互审（R-47）**：读案件证据链，对 AG-04 处置建议做证据充分性/处置恰当性/过度处置风险审查（LLM 优先，降级规则分档：空证据→escalate、单薄+重处置→concerns、≥2 条→pass），verdict 并入审批单 opinion + case_evidence（source_ref=`AA-AG-01:cross-review`，claim+source_ref 幂等）+ audit `disposition.reviewed`，只建议不决策、不阻断建单（02 §3.3 人机边界）→ API-M-11 `create_approval_request` 建审批工单（DA-T-07，requested_action/requested_amount 随单写入，案件不存在→E-NOT-FOUND）+ 案转 PENDING_APPROVAL；
3. **幂等**：idempotency_key 命中 DA-T-06 唯一约束 → 返回首次凭证不重复执行（DA-INV-03，SC-07）；批准执行的幂等键 = `case_id:action:approval_id`；重试遇 E-IDEMPOTENT-CONFLICT 按成功处理（route=executed，exec_id 取首次结果）；
4. **执行**：先转 DISPOSING → mcp-core 同事务写 disposition_record（submitted→executed，receipt 含 approval_ref/action/amount，SC-02 审批与凭证关联落库）→ 成功 DISPOSED；approval_ref 验真含逆动作对（批准冻结即含解冻纠错授权，DA-INV-03 延伸，供 AA-SK-04 反向处置复用）；
5. **审批闭环**：批准→ApprovalApproved→DispositionSubmitted→DISPOSING→DispositionExecuted→DISPOSED；驳回→ApprovalRejected→RollbackToReview 回退人工复核 + `context_json.auto_channel=disabled` 禁用自动通道（BA-BR-07，聚合裁决层持久守卫，SC-03）；
6. **时效**：BA-BR-13 升级扫描（web-api 后台任务 30s 轮询）——超 30 分钟未决工单 escalated_at 打标 + 审计 + ApprovalEscalated，门户标红（SC-09）；
7. **移交**：exec_id 交 AA-SK-04 启动 10 分钟核验计时（BA-BR-08）。

## 验收锚点

SC-02（审批门控）、SC-03（驳回回滚）、SC-07（幂等）、SC-09（时效升级）、SC-10（中风险拒自动处置）。测试载体：services/web-api/tests/test_disposition.py + test_cross_review.py（R-47 互审：规则分档/LLM 白名单/降级保底/端到端建单嵌入，14 例全绿）。
