---
name: AA-SK-04-compliance-audit
version: 1.5.0
description: 处置核验与合规审计（BA-BP-01K，十分钟核验 BA-BR-08）
agent: AA-AG-05
entrypoint: services/web-api/app/skills/verification.py
depends-mcp: execute_disposition, record_case_evidence, submit_kb_application
depends-tables: disposition_record, audit_log, case_evidence, kb_document, risk_case, risk_signal
tests: services/web-api/tests/test_verification.py
test-cases: 6
degradation-paths: 核验不一致反向处置+P0 升级, 反向处置被拒 ROLLBACK_ESCALATED 转人工不谎报 RollbackExecuted
---

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

1. **结果核验**：`disposition_record.status` 实际状态 == executed 预期（同库直读，tg_web 只读基线）；
2. **留痕完整性**：audit_log 覆盖 case.register→disposition.submit 最小动作集（TRACE_REQUIRED），缺口即 trace_complete=false；
3. **三分支（诚实语义）**：一致 → 审计报告经 API-M-12 落 DA-T-05 → `VerificationPassed` → VERIFIED → `CaseArchived` → ARCHIVED → 复盘提入库申请（AA-SK-05，pending）；
   不一致 → `VerificationFailed` → ROLLBACK → 反向处置（幂等键 `{case_id}:{action}:rollback`，freeze/block/reduce→release）→ `RollbackExecuted` → MANUAL_REVIEW + verification.p0 审计升级；
   无凭证 / 反向处置被拒或失败 → `RollbackEscalated` → MANUAL_REVIEW（**不发 `RollbackExecuted`**，语义不撒谎；与 RollbackExecuted 同状态对，白名单零改动）；
4. **超时守护**：scan_verification_overdue 十分钟未核验审计提醒 + VerificationOverdue 事件（BA-BR-08，web-api lifespan 30s 轮询，NOT EXISTS 幂等）；
5. **报告**：audit_report 落 DA-T-05（claim 前缀"审计报告："）+ 审计 verification.run（含 trace_id）。

落地入口：API-W-19 `/api/cases/{case_id}/verify`（body.exec_id）。

## 验收锚点

SC-08（审计回放）、SC-04（核验不一致反向处置）、BA-BR-08 计时。测试载体：services/web-api/tests/test_verification.py（5 例，verification.py 覆盖率 93%，110/110 全绿）。
