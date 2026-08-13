# -*- coding: utf-8 -*-
"""AA-SK-03 处置执行确定性内核（E5 处置执行与审批回滚，US-E5-01~05）

与 aggregation.py 同构的可测内核（06 §3 TDD 纪律）：
  submit              —— 处置提交编排（边界守卫 + 门控建单 + 幂等，SC-02/07/10）
  approve / reject    —— 审批门户写路径闭环（API-W-09 的领域编排，SC-02/03）
  scan_pending_escalations —— BA-BR-13 审批时效升级扫描（SC-09，lifespan 定时驱动）

边界规则（BA-BR-01/02，与 mcp-core execute_disposition 门控双层守护）：
  风险分 40-69 中风险：禁止任何无凭证自动处置（E-DISP-SCOPE，仅审计留痕，SC-10）；
  风险分 ≥70 高风险：处置必须携带审批凭证，缺凭证由 mcp-core 返回 E-DISP-AUTH，
  本层据此建审批工单并转 PENDING_APPROVAL（SC-02）。
权限矩阵（DA-INV-05）：disposition_record/approval_record 写入一律经 mcp-core
（tg_app），web-api（tg_web）仅承担审批决策回填与状态机写路径。
"""
from __future__ import annotations

import uuid

from ..core.state_machine import CaseEvent
from ..core.tracing import skill_span

AUTO_SCORE_MAX = 40      # BA-BR-01 自动通道风险分上限（与 aggregation 同源常量语义）
HIGH_RISK_SCORE = 70     # BA-BR-02 高风险强制审批线
ESCALATION_MINUTES = 30  # BA-BR-13 审批时效（Nacos 可下发，SC-06 后接入）

ACTOR_DISP = "agent:AA-AG-04"     # 状态机 actor（Agent 前缀约定，02 §3）
ACTOR_DISP_AUDIT = "AA-AG-04"     # 审计 actor（与 mcp-core 落库一致，SC-01 沿用）
ACTOR_ESCALATION = "system:timer-BA-BR-13"


class DispositionStateError(Exception):
    """处置编排的非法状态进入（E-BAD-STATE）"""

    code = "E-BAD-STATE"


class DispositionService:
    """处置编排服务：依赖注入 pool（tg_web 状态机/审计）、cases（CaseRepository）、
    core（AA-MCP-01 CoreClient）、pub（事件发布端口）。"""

    def __init__(self, pool, cases, core, pub):
        self.pool = pool
        self.cases = cases
        self.core = core
        self.pub = pub

    async def submit(self, case_id: str, action: str, amount: float | None,
                     idempotency_key: str, approval_ref: str | None = None) -> dict:
        """处置提交编排（SC-02/07/10 载体）

        路由：refused_mid_risk（BA-BR-01 中风险段）/ approval_required（E-DISP-AUTH
        建单转待审批）/ idempotent_hit（DA-INV-03 幂等重放）/ executed（执行成功）。
        """
        async with skill_span("AA-SK-03", "AA-AG-04", case_id,
                              action=action, approval_ref=approval_ref or ""):
            return await self._submit(case_id, action, amount, idempotency_key, approval_ref)

    async def _submit(self, case_id: str, action: str, amount: float | None,
                      idempotency_key: str, approval_ref: str | None = None) -> dict:
        case = await self.cases.get(case_id)
        if not case:
            raise LookupError(case_id)
        score = case["risk_score"]

        # BA-BR-01 中风险分段：40-69 禁止无凭证自动处置（SC-10），仅审计留痕
        if approval_ref is None and AUTO_SCORE_MAX <= score < HIGH_RISK_SCORE:
            await self._audit(case_id, "disposition.refused",
                              f"risk_score={score} action={action} 中风险禁止自动处置"
                              f"（BA-BR-01 分段，SC-10）", case["trace_id"])
            return {"case_id": case_id, "route": "refused_mid_risk",
                    "code": "E-DISP-SCOPE", "risk_score": score}

        result = await self.core.execute_disposition(
            case_id, action, amount, idempotency_key, approval_ref)

        if result.get("code") == "E-EVIDENCE-MISSING":
            # DA-INV-04：冻结类处置缺证据链拒写（BA-BR-03，US-E4-03；mcp-core 已审计 refused）
            return {"case_id": case_id, "route": "evidence_missing",
                    "code": "E-EVIDENCE-MISSING"}

        if result.get("code") == "E-DISP-AUTH":
            # SC-02：拒执行 + 建审批工单（tg_app，API-M-11）+ 转待审批
            appr = await self.core.create_approval_request(
                case_id, action, amount,
                reason=f"risk_score={score} 高风险处置需人工审批（BA-BR-02，SC-02）")
            status = case["status"]
            if status == "INVESTIGATING":
                out = await self.cases.transition(
                    case_id, CaseEvent.INVESTIGATION_COMPLETED, ACTOR_DISP, case["version"],
                    basis=f"处置门控 E-DISP-AUTH 建单 {appr['approval_id']}（DA-INV-02）")
                status = out["status"]
            elif status != "PENDING_APPROVAL":
                raise DispositionStateError(f"{case_id} 状态 {status} 不可进入审批门控")
            return {"case_id": case_id, "route": "approval_required", "code": "E-DISP-AUTH",
                    "approval_id": appr["approval_id"], "case_status": status}

        if result.get("code") == "E-IDEMPOTENT-CONFLICT":
            return {"case_id": case_id, "route": "idempotent_hit",
                    "exec_id": result["first_result"]["exec_id"],
                    "first_result": result["first_result"]}
        if result.get("code"):
            raise RuntimeError(f"处置执行失败：{result}")

        # 执行成功：推进状态机至 DISPOSED（APPROVED → DISPOSING → DISPOSED）
        version = case["version"]
        status = case["status"]
        if status == "APPROVED":
            out = await self.cases.transition(
                case_id, CaseEvent.DISPOSITION_SUBMITTED, ACTOR_DISP, version,
                basis=f"approval={approval_ref} action={action}（SC-02 批准后执行）")
            version = out["version"]
            status = out["status"]
        if status == "DISPOSING":
            out = await self.cases.transition(
                case_id, CaseEvent.DISPOSITION_EXECUTED, ACTOR_DISP, version,
                basis=f"exec_id={result['exec_id']} action={action} 执行凭证关联审批"
                      f" approval_ref={approval_ref}")
            status = out["status"]
        return {"case_id": case_id, "route": "executed", "exec_id": result["exec_id"],
                "case_status": status}

    async def approve(self, approval_id: str, approver: str, opinion: str = "") -> dict:
        """批准闭环（API-W-09 编排）：回填决策 → ApprovalApproved → 自动执行处置"""
        rec, case = await self._load(approval_id)
        await self._decide(approval_id, "approved", approver, opinion, rec["case_id"],
                           case["trace_id"])
        out = await self.cases.transition(
            rec["case_id"], CaseEvent.APPROVAL_APPROVED, approver, case["version"],
            basis=f"approval={approval_id}")
        # 批准后自动执行（幂等键含 approval_id，DA-INV-03）
        result = await self.submit(
            rec["case_id"], rec["requested_action"], rec["requested_amount"],
            idempotency_key=f"{rec['case_id']}:{rec['requested_action']}:{approval_id}",
            approval_ref=approval_id)
        return result | {"approval_id": approval_id, "decision": "approved",
                         "approved_version": out["version"]}

    async def reject(self, approval_id: str, approver: str, opinion: str = "") -> dict:
        """驳回回滚（SC-03，BA-BR-07）：ApprovalRejected → REJECTED → 回退人工复核
        且禁用自动通道（context_json.auto_channel=disabled，聚合裁决层持久守卫）。"""
        rec, case = await self._load(approval_id)
        await self._decide(approval_id, "rejected", approver, opinion, rec["case_id"],
                           case["trace_id"])
        out = await self.cases.transition(
            rec["case_id"], CaseEvent.APPROVAL_REJECTED, approver, case["version"],
            basis=f"approval={approval_id} opinion={opinion}")
        out = await self.cases.transition(
            rec["case_id"], CaseEvent.ROLLBACK_TO_REVIEW, ACTOR_DISP, out["version"],
            basis=f"驳回回滚人工复核（BA-BR-07）opinion={opinion}")
        # BA-BR-07：禁止再次进入自动通道（tg_web UPDATE risk_case，权限矩阵内）
        await self.pool.execute(
            """UPDATE risk_case
               SET context_json=COALESCE(context_json,'{}'::jsonb) || '{"auto_channel":"disabled"}'::jsonb
               WHERE case_id=$1""", rec["case_id"])
        return {"case_id": rec["case_id"], "approval_id": approval_id,
                "decision": "rejected", "case_status": out["status"]}

    async def review_confirm(self, case_id: str, operator: str, comment: str = "") -> dict:
        """复核确认自动建单（US-E4-05，API-W-07 confirm 分支）：
        ReviewConfirmed（human_only）→ 建处置审批工单（API-M-11）→ 返回工单号。
        定性仍须人工审批（02 §3.3 人机边界），本方法只建工单不执行处置。"""
        case = await self.cases.get(case_id)
        if not case:
            raise LookupError(case_id)
        out = await self.cases.transition(
            case_id, CaseEvent.REVIEW_CONFIRMED, operator, case["version"],
            basis=f"人工复核确认：{comment}" if comment else "人工复核确认欺诈（BA-BP-05）")
        appr = await self.core.create_approval_request(
            case_id, "freeze", None,
            reason=f"人工复核确认欺诈（{operator}）：{comment}（US-E4-05，BA-BR-01）")
        await self.pool.execute(
            "UPDATE approval_record SET opinion=$2 WHERE approval_id=$1",
            appr["approval_id"], comment or "复核确认转审批")
        return {"case_id": case_id, "status": out["status"], "version": out["version"],
                "approval_id": appr["approval_id"]}

    async def _load(self, approval_id: str):
        rec = await self.pool.fetchrow(
            "SELECT * FROM approval_record WHERE approval_id=$1", approval_id)
        if not rec:
            raise LookupError(approval_id)
        if rec["decision"] != "pending":
            raise DispositionStateError(f"工单已决（{rec['decision']}），禁止重复回填")
        case = await self.cases.get(rec["case_id"])
        return rec, case

    async def _decide(self, approval_id: str, decision: str, approver: str,
                      opinion: str, case_id: str, trace_id: str):
        """决策回填 DA-T-07 + 审计（tg_web UPDATE 权限，02-roles.sql）"""
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE approval_record SET decision=$1, approver=$2, opinion=$3, "
                "decided_at=now() WHERE approval_id=$4",
                decision, approver, opinion, approval_id)
            await conn.execute(
                """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
                   VALUES ($1, $2, 'approval.decide', $3, $4, $5)""",
                uuid.uuid4().hex, approver, approval_id,
                f"case={case_id},decision={decision},opinion={opinion}", trace_id)

    async def _audit(self, case_id: str, action: str, basis: str, trace_id: str):
        await self.pool.execute(
            """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            uuid.uuid4().hex, ACTOR_DISP_AUDIT, action, case_id, basis, trace_id)


async def scan_pending_escalations(pool, pub, minutes: int = ESCALATION_MINUTES) -> list[dict]:
    """BA-BR-13 审批时效升级扫描（SC-09）：滞留超阈值的 pending 工单标记升级。

    升级动作：escalated_at 打标（门户标红依据，API-W-08 返回）+ 审计留痕（BA-BR-09）
    + 发布 ApprovalEscalated 事件（值班提醒的确定性载体；Matrix 推送经 AgentTeams
    订阅消费，04 §10.1）。escalated_at IS NULL 条件保证不重复升级（幂等）。
    """
    rows = await pool.fetch(
        """UPDATE approval_record SET escalated_at=now()
           WHERE decision='pending' AND escalated_at IS NULL
             AND created_at < now() - make_interval(mins=>$1)
           RETURNING approval_id, case_id""", minutes)
    for r in rows:
        trace = await pool.fetchval(
            "SELECT trace_id FROM risk_case WHERE case_id=$1", r["case_id"])
        await pool.execute(
            """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
               VALUES ($1, $2, 'approval.escalate', $3, $4, $5)""",
            uuid.uuid4().hex, ACTOR_ESCALATION, r["case_id"],
            f"approval={r['approval_id']} 超 {minutes} 分钟未决，升级值班（BA-BR-13）", trace)
        await pub.publish(r["case_id"], "ApprovalEscalated",
                          {"approval_id": r["approval_id"], "threshold_minutes": minutes},
                          ACTOR_ESCALATION)
    return [dict(r) for r in rows]
