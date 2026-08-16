"""AA-SK-04 核验审计确定性内核（E6 核验审计与知识沉淀，US-E6-01/02/03）

与聚合/处置同构的可测内核（06 §3 TDD 纪律）：
  verify —— 结果核验（执行凭证实际状态对账）+ 留痕完整性（audit_trail 覆盖全链），
            三分支裁决（闭环修复 v1.4.4，B3）：
            分支 1（一致，**无论 trace 是否完整**）：VerificationPassed → VERIFIED →
                  CaseArchived → ARCHIVED，审计报告落 DA-T-05，复盘入库申请（AA-SK-05）；
                  trace 缺口仅审计告警——**绝不回滚一致执行**；
            分支 2（不一致 + 反向处置完成）：VerificationFailed → ROLLBACK → 反向处置
                  （携带原动作审批凭证，逆动作对授权，C1）→ RollbackExecuted →
                  MANUAL_REVIEW + verification.p0 审计；
            分支 3（不一致 + 反向被拒/失败）：RollbackEscalated → MANUAL_REVIEW +
                  verification.escalated 审计——**绝不抛异常卡死案件**。
  scan_verification_overdue —— BA-BR-08 十分钟核验超时扫描（lifespan 定时驱动，幂等；
            基准取 updated_at：DISPOSED 迁入时间即核验时钟起点）。
"""

from __future__ import annotations

import uuid
from typing import Any

from ..core.state_machine import CaseEvent, status_zh
from ..core.tracing import skill_span
from .mcp_adapters import remember

ACTOR_VER = "agent:AA-AG-05"  # 合规审计 Agent（02 §3）
ACTOR_VER_AUDIT = "AA-AG-05"
ACTOR_OVERDUE = "system:timer-BA-BR-08"
VERIFICATION_MINUTES = 10  # BA-BR-08 核验时效（Nacos 可下发）
INVERSE_ACTION = {
    "freeze": "release",
    "block": "release",
    "reduce": "release",
    "release": "block",
}
# 留痕完整性最小动作集（SC-08 全链的回放断言基线）
TRACE_REQUIRED = ("case.register", "disposition.submit")


class VerificationStateError(Exception):
    """核验编排的非法状态进入（E-BAD-STATE）"""

    code = "E-BAD-STATE"


class VerificationService:
    """核验编排服务：依赖注入 pool（tg_web 状态机/审计）、cases（CaseRepository）、
    core（AA-MCP-01 CoreClient）、pub（事件发布端口）。"""

    def __init__(self, pool, cases, core, pub):
        self.pool = pool
        self.cases = cases
        self.core = core
        self.pub = pub

    async def verify(self, case_id: str, exec_id: str) -> dict:
        async with skill_span("AA-SK-04", "AA-AG-05", case_id, exec_id=exec_id):
            result = await self._verify(case_id, exec_id)
        await remember(
            self.core,
            case_id,
            "AA-AG-05",
            "verification",
            {
                "consistency_check": result["consistency_check"],
                "trace_complete": result["trace_complete"],
                "case_status": result["case_status"],
            },
        )
        return result

    async def _verify(self, case_id: str, exec_id: str) -> dict:
        case = await self.cases.get(case_id)
        if not case:
            raise LookupError(case_id)
        if case["status"] != "DISPOSED":
            raise VerificationStateError(
                f"案件当前处于「{status_zh(case['status'])}」状态，不支持发起核验，请刷新页面查看最新进展"
            )
        rec = await self.pool.fetchrow(
            "SELECT * FROM disposition_record WHERE exec_id=$1 AND case_id=$2",
            exec_id,
            case_id,
        )
        if not rec:
            raise LookupError(exec_id)

        # 1. 结果核验（AA-SK-04 步骤 1）+ 留痕完整性（步骤 2）
        consistent = rec["status"] == "executed"
        actions = [
            r["action"]
            for r in await self.pool.fetch(
                "SELECT action FROM audit_log WHERE target=$1", case_id
            )
        ]
        trace_complete = all(a in actions for a in TRACE_REQUIRED)
        await self._audit(
            case_id,
            "verification.run",
            f"exec_id={exec_id},consistent={consistent},"
            f"trace_complete={trace_complete}",
            case["trace_id"],
        )

        # 2. 分支 1：一致即归档——**绝不回滚一致执行**（B3）；trace 缺口仅审计告警
        if consistent:
            if not trace_complete:
                await self._audit(
                    case_id,
                    "verification.trace_gap",
                    f"exec_id={exec_id} 核验一致但审计链缺 "
                    f"{[a for a in TRACE_REQUIRED if a not in actions]}，"
                    f"仅告警不回滚（B3 一致执行不可逆）",
                    case["trace_id"],
                )
            report = (
                f"审计报告：{case_id} 处置 {rec['action']} 核验一致，"
                f"审计链 {len(actions)} 条（trace_complete={trace_complete}，BA-BR-09）"
            )
            await self.core.record_case_evidence(
                case_id,
                [
                    {
                        "claim": report,
                        "source_ref": "AA-AG-05:audit-report",
                        "confidence": 0.95,
                    }
                ],
            )  # 报告落 DA-T-05（步骤 5）
            out = await self.cases.transition(
                case_id,
                CaseEvent.VERIFICATION_PASSED,
                ACTOR_VER,
                case["version"],
                basis=f"exec_id={exec_id} 核验一致（BA-BR-08 时效内）",
            )
            out = await self.cases.transition(
                case_id,
                CaseEvent.CASE_ARCHIVED,
                ACTOR_VER,
                out["version"],
                basis="结案归档（BA-BP-04）",
            )
            kb = await self._retrospective(case_id, rec)  # AA-SK-05 复盘入库申请
            return {
                "case_id": case_id,
                "consistency_check": True,
                "trace_complete": trace_complete,
                "case_status": out["status"],
                "audit_report": report,
                "kb_application": kb["doc_id"],
            }

        # 3. 分支 2/3：不一致 → ROLLBACK → 反向处置（携原动作审批凭证，逆对授权 C1）
        out = await self.cases.transition(
            case_id,
            CaseEvent.VERIFICATION_FAILED,
            ACTOR_VER,
            case["version"],
            basis=f"exec_id={exec_id} 实际状态={rec['status']} 与凭证不一致",
        )
        inverse = INVERSE_ACTION.get(rec["action"], "release")
        approval_ref = rec["approval_ref"] or await self.pool.fetchval(
            """SELECT approval_id FROM approval_record
               WHERE case_id=$1 AND decision='approved' AND requested_action=$2
               ORDER BY decided_at DESC NULLS LAST LIMIT 1""",
            case_id,
            rec["action"],
        )
        rb: dict[str, Any]
        try:
            rb = await self.core.execute_disposition(
                case_id,
                inverse,
                None,
                f"{case_id}:{rec['action']}:rollback",
                approval_ref,
            )
        except Exception as e:  # noqa: BLE001 —— 反向处置异常不得卡死案件（B3）
            rb = {"code": "E-MCP-UNAVAILABLE", "message": str(e)}

        rb_code = rb.get("code")
        if not rb_code or rb_code == "E-IDEMPOTENT-CONFLICT":
            # 分支 2：反向处置完成（或幂等重放已回滚）→ RollbackExecuted 升级 P0 转人工
            out = await self.cases.transition(
                case_id,
                CaseEvent.ROLLBACK_EXECUTED,
                ACTOR_VER,
                out["version"],
                basis=f"反向处置 {inverse} 完成，升级 P0 转人工（AA-SK-04 失败处理）",
            )
            await self._audit(
                case_id,
                "verification.p0",
                f"exec_id={exec_id} 核验不一致，反向处置 {inverse} 已执行"
                f"（凭证={approval_ref}，逆动作对授权），"
                f"升级 P0 并暂停该主体自动处置",
                case["trace_id"],
            )
            return {
                "case_id": case_id,
                "consistency_check": False,
                "trace_complete": trace_complete,
                "case_status": out["status"],
                "rollback_exec_id": rb.get("exec_id")
                or rb.get("first_result", {}).get("exec_id"),
            }

        # 分支 3：反向处置被拒/失败 → RollbackEscalated 直接升级转人工（不回滚、不抛异常）
        out = await self.cases.transition(
            case_id,
            CaseEvent.ROLLBACK_ESCALATED,
            ACTOR_VER,
            out["version"],
            basis=f"反向处置 {inverse} 被拒（{rb_code}），未回滚直接升级转人工",
        )
        await self._audit(
            case_id,
            "verification.escalated",
            f"exec_id={exec_id} 核验不一致且反向处置 {inverse} 未执行"
            f"（{rb_code}），不自动回滚，升级 P0 转人工处置",
            case["trace_id"],
        )
        return {
            "case_id": case_id,
            "consistency_check": False,
            "trace_complete": trace_complete,
            "case_status": out["status"],
            "rollback_refused": rb_code,
        }

    async def _retrospective(self, case_id: str, rec) -> dict:
        """AA-SK-05 复盘摘要与入库申请（US-E6-03）：汇总信号/证据/处置/核验四段，
        提交 pending 申请单（发布须人工，DA-INV-06，SC-05）。"""
        sig_cnt = await self.pool.fetchval(
            "SELECT count(*) FROM risk_signal WHERE case_id=$1", case_id
        )
        ev_cnt = await self.pool.fetchval(
            "SELECT count(*) FROM case_evidence WHERE case_id=$1", case_id
        )
        content = (
            f"案件 {case_id} 复盘摘要：信号 {sig_cnt} 条、证据 {ev_cnt} 条；"
            f"处置 action={rec['action']}（exec_id={rec['exec_id']}）核验一致后归档。"
            f"手法特征候选：{rec['action']} 场景信号指纹见 DA-T-04。"
        )
        return await self.core.submit_kb_application(
            case_id, "case", f"案件复盘 {case_id}", content
        )

    async def _audit(self, case_id: str, action: str, basis: str, trace_id: str):
        await self.pool.execute(
            """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            uuid.uuid4().hex,
            ACTOR_VER_AUDIT,
            action,
            case_id,
            basis,
            trace_id,
        )


async def scan_verification_overdue(
    pool, pub, minutes: int = VERIFICATION_MINUTES
) -> list[dict]:
    """BA-BR-08 核验超时扫描：DISPOSED 超阈值未核验即审计提醒 + 发事件。

    幂等：已存在 verification.overdue 审计的案件不再提醒（NOT EXISTS 条件）。
    """
    rows = await pool.fetch(
        """SELECT case_id, trace_id FROM risk_case
           WHERE status='DISPOSED' AND updated_at < now() - make_interval(mins=>$1)
             AND NOT EXISTS (SELECT 1 FROM audit_log
                             WHERE target=risk_case.case_id AND action='verification.overdue')""",
        minutes,
    )
    for r in rows:
        await pool.execute(
            """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
               VALUES ($1, $2, 'verification.overdue', $3, $4, $5)""",
            uuid.uuid4().hex,
            ACTOR_OVERDUE,
            r["case_id"],
            f"DISPOSED 超 {minutes} 分钟未核验，提醒值班（BA-BR-08）",
            r["trace_id"],
        )
        await pub.publish(
            r["case_id"],
            "VerificationOverdue",
            {"threshold_minutes": minutes},
            ACTOR_OVERDUE,
        )
    return [dict(r) for r in rows]
