# AA-SK-04 compliance-audit · 处置核验与合规审计

> 承载：AA-AG-05（合规审计 Agent）｜ 确定性内核：services/web-api/app/skills/verification.py

## 九属性契约（02 §4）

| 属性 | 内容 |
|---|---|
| 用途 | 核验处置结果与预期一致、检查留痕完整性、生成审计报告（BA-BP-01 K） |
| 输入 | `{case_id, execution_id}` |
| 输出 | `{audit_report, consistency_check, trace_complete}` |
| 调用条件 | 处置执行完成后 10 分钟内（BA-BR-08） |
| 依赖工具 | AA-MCP-01 `query_disposition_result/query_audit_trail`；DA-KB-02 监管规范检索 |
| 失败处理 | 核验不一致→升级告警 P0 并暂停该主体后续自动处置 |
| 安全边界 | 审计记录 append-only，Agent 无删除/修改权限 |
| 复用价值 | 通用合规核验框架 |
| 协同关系 | AA-AG-05 调用；报告为结案归档前置条件 |

## 确定性执行步骤

1. **结果核验**：`query_disposition_result(execution_id)` 实际状态 == disposition_record 预期；
2. **留痕完整性**：`query_audit_trail(case_id)` 覆盖 register→transition→disposition 全链，缺口即 trace_complete=false；
3. **分支**：一致 → `VerificationPassed` → VERIFIED → `CaseArchived`；
   不一致 → `VerificationFailed` → ROLLBACK → 反向处置（幂等键加 `:rollback` 后缀）→ `RollbackExecuted` → MANUAL_REVIEW + P0 告警；
4. **超时守护**：10 分钟未核验触发提醒（BA-BR-08 计时器）；
5. **报告**：audit_report 落 DA-T-05。

## 验收锚点

SC-08（审计回放）、SC-04（核验不一致反向处置）、BA-BR-08 计时。
