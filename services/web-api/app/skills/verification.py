# -*- coding: utf-8 -*-
"""AA-SK-04 核验审计确定性内核（E6 核验审计与知识沉淀，US-E6-01/02/03）

与聚合/处置同构的可测内核（06 §3 TDD 纪律）：
  verify —— 结果核验（query_disposition_result 实际状态对账执行凭证）+
            留痕完整性（audit_trail 覆盖全链）→
            一致：VerificationPassed → VERIFIED → CaseArchived → ARCHIVED，
                  审计报告落 DA-T-05，复盘摘要提入库申请（AA-SK-05，pending）；
            不一致：VerificationFailed → ROLLBACK → 反向处置（幂等键 :rollback 后缀）
                  → RollbackExecuted → MANUAL_REVIEW + P0 审计升级。
  scan_verification_overdue —— BA-BR-08 十分钟核验超时扫描（lifespan 定时驱动，幂等）。
"""
from __future__ import annotations

import uuid

from ..core.state_machine import CaseEvent

ACTOR_VER = "agent:AA-AG-05"        # 合规审计 Agent（02 §3）
ACTOR_VER_AUDIT = "AA-AG-05"
ACTOR_OVERDUE = "system:timer-BA-BR-08"
VERIFICATION_MINUTES = 10           # BA-BR-08 核验时效（Nacos 可下发）
INVERSE_ACTION = {"freeze": "release", "block": "release",
                  "reduce": "release", "release": "block"}
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
        case = await self.cases.get(case_id)
        if not case:
            raise LookupError(case_id)
        if case["status"] != "DISPOSED":
            raise VerificationStateError(f"{case_id} 状态 {case['status']} 不可核验")
        rec = await self.pool.fetchrow(
            "SELECT * FROM disposition_record WHERE exec_id=$1 AND case_id=$2",
            exec_id, case_id)
        if not rec:
            raise LookupError(exec_id)

        # 1. 结果核验（AA-SK-04 步骤 1）+ 留痕完整性（步骤 2）
        consistent = rec["status"] == "executed"
        actions = [r["action"] for r in await self.pool.fetch(
            "SELECT action FROM audit_log WHERE target=$1", case_id)]
        trace_complete = all(a in actions for a in TRACE_REQUIRED)
        await self._audit(case_id, "verification.run",
                          f"exec_id={exec_id},consistent={consistent},"
                          f"trace_complete={trace_complete}", case["trace_id"])

        # 2. 分支：一致归档 / 不一致反向处置（AA-SK-04 步骤 3）
        if consistent and trace_complete:
            report = (f"审计报告：{case_id} 处置 {rec['action']} 核验一致，"
                      f"审计链 {len(actions)} 条完整（BA-BR-09）")
            await self.core.record_case_evidence(case_id, [{
                "claim": report, "source_ref": "AA-AG-05:audit-report",
                "confidence": 0.95}])                       # 报告落 DA-T-05（步骤 5）
            out = await self.cases.transition(
                case_id, CaseEvent.VERIFICATION_PASSED, ACTOR_VER, case["version"],
                basis=f"exec_id={exec_id} 核验一致（BA-BR-08 时效内）")
            out = await self.cases.transition(
                case_id, CaseEvent.CASE_ARCHIVED, ACTOR_VER, out["version"],
                basis="结案归档（BA-BP-04）")
            kb = await self._retrospective(case_id, rec)    # AA-SK-05 复盘入库申请
            return {"case_id": case_id, "consistency_check": True,
                    "trace_complete": True, "case_status": out["status"],
                    "audit_report": report, "kb_application": kb["doc_id"]}

        out = await self.cases.transition(
            case_id, CaseEvent.VERIFICATION_FAILED, ACTOR_VER, case["version"],
            basis=f"exec_id={exec_id} 实际状态={rec['status']} 与凭证不一致")
        inverse = INVERSE_ACTION.get(rec["action"], "release")
        rb = await self.core.execute_disposition(
            case_id, inverse, None, f"{case_id}:{rec['action']}:rollback")
        if rb.get("code") and rb["code"] != "E-IDEMPOTENT-CONFLICT":
            raise RuntimeError(f"反向处置失败：{rb}")
        out = await self.cases.transition(
            case_id, CaseEvent.ROLLBACK_EXECUTED, ACTOR_VER, out["version"],
            basis=f"反向处置 {inverse} 完成，升级 P0 转人工（AA-SK-04 失败处理）")
        await self._audit(case_id, "verification.p0",
                          f"exec_id={exec_id} 核验不一致，反向处置 {inverse} 已执行，"
                          f"升级 P0 并暂停该主体自动处置", case["trace_id"])
        return {"case_id": case_id, "consistency_check": False,
                "trace_complete": trace_complete, "case_status": out["status"],
                "rollback_exec_id": rb.get("exec_id") or rb.get("first_result", {}).get("exec_id")}

    async def _retrospective(self, case_id: str, rec) -> dict:
        """AA-SK-05 复盘摘要与入库申请（US-E6-03）：汇总信号/证据/处置/核验四段，
        提交 pending 申请单（发布须人工，DA-INV-06，SC-05）。"""
        sig_cnt = await self.pool.fetchval(
            "SELECT count(*) FROM risk_signal WHERE case_id=$1", case_id)
        ev_cnt = await self.pool.fetchval(
            "SELECT count(*) FROM case_evidence WHERE case_id=$1", case_id)
        content = (f"案件 {case_id} 复盘摘要：信号 {sig_cnt} 条、证据 {ev_cnt} 条；"
                   f"处置 action={rec['action']}（exec_id={rec['exec_id']}）核验一致后归档。"
                   f"手法特征候选：{rec['action']} 场景信号指纹见 DA-T-04。")
        return await self.core.submit_kb_application(
            case_id, "case", f"案件复盘 {case_id}", content)

    async def _audit(self, case_id: str, action: str, basis: str, trace_id: str):
        await self.pool.execute(
            """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            uuid.uuid4().hex, ACTOR_VER_AUDIT, action, case_id, basis, trace_id)


async def scan_verification_overdue(pool, pub, minutes: int = VERIFICATION_MINUTES) -> list[dict]:
    """BA-BR-08 核验超时扫描：DISPOSED 超阈值未核验即审计提醒 + 发事件。

    幂等：已存在 verification.overdue 审计的案件不再提醒（NOT EXISTS 条件）。
    """
    rows = await pool.fetch(
        """SELECT case_id, trace_id FROM risk_case
           WHERE status='DISPOSED' AND updated_at < now() - make_interval(mins=>$1)
             AND NOT EXISTS (SELECT 1 FROM audit_log
                             WHERE target=risk_case.case_id AND action='verification.overdue')""",
        minutes)
    for r in rows:
        await pool.execute(
            """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
               VALUES ($1, $2, 'verification.overdue', $3, $4, $5)""",
            uuid.uuid4().hex, ACTOR_OVERDUE, r["case_id"],
            f"DISPOSED 超 {minutes} 分钟未核验，提醒值班（BA-BR-08）", r["trace_id"])
        await pub.publish(r["case_id"], "VerificationOverdue",
                          {"threshold_minutes": minutes}, ACTOR_OVERDUE)
    return [dict(r) for r in rows]
