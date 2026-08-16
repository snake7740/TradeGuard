# -*- coding: utf-8 -*-
"""AA-SK-03 处置执行确定性内核（E5 处置执行与审批回滚，US-E5-01~05）

与 aggregation.py 同构的可测内核（06 §3 TDD 纪律）：
  submit              —— 处置提交编排（边界守卫 + 门控建单 + 幂等 + 重试，SC-02/07/10）
  approve / reject    —— 审批门户写路径闭环（API-W-09 的领域编排，SC-02/03）
  scan_pending_escalations —— BA-BR-13 审批时效升级扫描（SC-09，lifespan 定时驱动）

边界规则（BA-BR-01/02，与 mcp-core execute_disposition 门控双层守护）：
  风险分 40-69 中风险：禁止任何无凭证自动处置（E-DISP-SCOPE，仅审计留痕，SC-10）；
  风险分 ≥70 高风险：处置必须携带审批凭证，缺凭证由 mcp-core 返回 E-DISP-AUTH，
  本层据此建审批工单并转 PENDING_APPROVAL（SC-02）。

执行顺序与失败兜底（闭环修复 v1.4.4，B2）：
  APPROVED 分支先转 DISPOSING 再执行；执行成功→DISPOSED；
  重试耗尽或门控拒绝（E-DISP-AUTH/E-DISP-SCOPE/E-EVIDENCE-MISSING）→
  DispositionFailed→MANUAL_REVIEW，案件永不卡死在 DISPOSING/APPROVED。
  E-IDEMPOTENT-CONFLICT 在 DISPOSING 态按成功处理（首执成功但响应丢失的重投）。
权限矩阵（DA-INV-05）：disposition_record/approval_record 写入一律经 mcp-core
（tg_app），web-api（tg_web）仅承担审批决策回填与状态机写路径。
"""
from __future__ import annotations

import asyncio
import uuid

from ..core.state_machine import CaseEvent, status_zh
from ..core.tracing import skill_span
from .mcp_adapters import remember

AUTO_SCORE_MAX = 40      # BA-BR-01 自动通道风险分上限（与 aggregation 同源常量语义）
HIGH_RISK_SCORE = 70     # BA-BR-02 高风险强制审批线
ESCALATION_MINUTES = 30  # BA-BR-13 审批时效（Nacos 可下发，SC-06 后接入）

ACTOR_DISP = "agent:AA-AG-04"     # 状态机 actor（Agent 前缀约定，02 §3）
ACTOR_DISP_AUDIT = "AA-AG-04"     # 审计 actor（与 mcp-core 落库一致，SC-01 沿用）
ACTOR_ESCALATION = "system:timer-BA-BR-13"

# 重试归类（B2）：确定性门控/幂等错误码不重试；其余错误码与网络异常重试，退避 0.3s/1s
_NON_RETRYABLE = {"E-IDEMPOTENT-CONFLICT", "E-DISP-AUTH", "E-EVIDENCE-MISSING", "E-DISP-SCOPE"}
_BACKOFFS = (0.3, 1.0)


class DispositionStateError(Exception):
    """处置编排的非法状态进入（E-BAD-STATE）"""

    code = "E-BAD-STATE"


class DispositionService:
    """处置编排服务：依赖注入 pool（tg_web 状态机/审计）、cases（CaseRepository）、
    core（AA-MCP-01 CoreClient）、pub（事件发布端口）。"""

    def __init__(self, pool, cases, core, pub, config=None):
        self.pool = pool
        self.cases = cases
        self.core = core
        self.pub = pub
        self.config = config   # ConfigService（SC-06 阈值热加载 D1，可缺省用常量）

    def _cfg_int(self, key: str, default: int) -> int:
        """从 ConfigService 读整型阈值，未配置/非法值回退代码常量"""
        try:
            return int(self.config.values[key])
        except (AttributeError, KeyError, TypeError, ValueError):
            return default

    async def submit(self, case_id: str, action: str, amount: float | None,
                     idempotency_key: str, approval_ref: str | None = None) -> dict:
        """处置提交编排（SC-02/07/10 载体）

        路由：refused_mid_risk（BA-BR-01 中风险段）/ approval_required（E-DISP-AUTH
        建单转待审批）/ idempotent_hit（DA-INV-03 幂等重放）/ executed（执行成功）。
        """
        async with skill_span("AA-SK-03", "AA-AG-04", case_id,
                              action=action, approval_ref=approval_ref or ""):
            result = await self._submit(case_id, action, amount, idempotency_key, approval_ref)
        await remember(self.core, case_id, "AA-AG-04", "disposition", {
            "route": result["route"], "action": action,
            "exec_id": result.get("exec_id"), "approval_id": result.get("approval_id")})
        return result

    async def _submit(self, case_id: str, action: str, amount: float | None,
                      idempotency_key: str, approval_ref: str | None = None) -> dict:
        case = await self.cases.get(case_id)
        if not case:
            raise LookupError(case_id)
        score = case["risk_score"]
        trace_id = case["trace_id"]

        # BA-BR-01 中风险分段：40-69 禁止无凭证自动处置（SC-10），仅审计留痕
        # SC-06 热值（D1）：两条分段线实时读取，与 mcp-core 门控同源（br-01-*）
        mid_lo = self._cfg_int("br-01-mid-review-score", AUTO_SCORE_MAX)
        mid_hi = self._cfg_int("br-01-auto-block-score", HIGH_RISK_SCORE)
        if approval_ref is None and mid_lo <= score < mid_hi:
            await self._audit(case_id, "disposition.refused",
                              f"risk_score={score} action={action} 中风险禁止自动处置"
                              f"（BA-BR-01 分段，SC-10）", trace_id)
            return {"case_id": case_id, "route": "refused_mid_risk",
                    "code": "E-DISP-SCOPE", "risk_score": score}

        version = case["version"]
        status = case["status"]

        # 顺序重排（B2）：APPROVED 先转 DISPOSING 再执行，失败时才有 DispositionFailed 出口
        if status == "APPROVED":
            out = await self.cases.transition(
                case_id, CaseEvent.DISPOSITION_SUBMITTED, ACTOR_DISP, version,
                basis=f"approval={approval_ref} action={action}（SC-02 批准后执行）")
            version = out["version"]
            status = out["status"]

        result = await self._execute_with_retry(
            case_id, action, amount, idempotency_key, approval_ref)
        code = result.get("code")

        if code == "E-EVIDENCE-MISSING":
            # DA-INV-04：冻结类处置缺证据链拒写（BA-BR-03，US-E4-03；mcp-core 已审计 refused）
            if status == "DISPOSING":
                status = await self._fail_to_manual(
                    case_id, version, trace_id, "E-EVIDENCE-MISSING 证据链缺失（DA-INV-04）")
            return {"case_id": case_id, "route": "evidence_missing",
                    "code": "E-EVIDENCE-MISSING", "case_status": status}

        if code in ("E-DISP-AUTH", "E-DISP-SCOPE"):
            if status == "DISPOSING":
                # 严格验真失败（凭证无效，已 APPROVED→DISPOSING）→ 转人工，不得悬空
                status = await self._fail_to_manual(
                    case_id, version, trace_id, f"{code} 凭证验真未通过")
                return {"case_id": case_id, "route": "failed_manual", "code": code,
                        "case_status": status}
            # SC-02：拒执行 + 建审批工单（tg_app，API-M-11）+ 转待审批
            appr = await self.core.create_approval_request(
                case_id, action, amount,
                reason=f"risk_score={score} 高风险处置需人工审批（BA-BR-02，SC-02）")
            if status == "INVESTIGATING":
                out = await self.cases.transition(
                    case_id, CaseEvent.INVESTIGATION_COMPLETED, ACTOR_DISP, version,
                    basis=f"处置门控 E-DISP-AUTH 建单 {appr['approval_id']}（DA-INV-02）")
                status = out["status"]
                version = out["version"]
            elif status != "PENDING_APPROVAL":
                raise DispositionStateError(
                    f"案件当前处于「{status_zh(status)}」状态，不支持创建审批单，请刷新页面查看最新进展")
            return {"case_id": case_id, "route": "approval_required", "code": "E-DISP-AUTH",
                    "approval_id": appr["approval_id"], "case_status": status}

        if code == "E-IDEMPOTENT-CONFLICT":
            first = result["first_result"]
            if status == "DISPOSING":
                # 幂等键重投：首执实际成功但响应丢失 → 按成功处理推进 DISPOSED（DA-INV-03）
                out = await self.cases.transition(
                    case_id, CaseEvent.DISPOSITION_EXECUTED, ACTOR_DISP, version,
                    basis=f"exec_id={first['exec_id']} 幂等重投按成功处理（DA-INV-03）")
                return {"case_id": case_id, "route": "executed",
                        "exec_id": first["exec_id"], "case_status": out["status"],
                        "idempotent_replay": True}
            # 外部重放（SC-07）：案件不在执行中 → 仅返回首次凭证，不推进状态
            return {"case_id": case_id, "route": "idempotent_hit",
                    "exec_id": first["exec_id"], "first_result": first}

        if code:
            if status == "DISPOSING":
                status = await self._fail_to_manual(
                    case_id, version, trace_id, f"{code} 重试耗尽")
                return {"case_id": case_id, "route": "failed_manual", "code": code,
                        "case_status": status}
            raise RuntimeError(f"处置执行失败：{result}")

        # 执行成功：推进状态机至 DISPOSED
        if status == "DISPOSING":
            out = await self.cases.transition(
                case_id, CaseEvent.DISPOSITION_EXECUTED, ACTOR_DISP, version,
                basis=f"exec_id={result['exec_id']} action={action} 执行凭证关联审批"
                      f" approval_ref={approval_ref}")
            status = out["status"]
        return {"case_id": case_id, "route": "executed", "exec_id": result["exec_id"],
                "case_status": status}

    async def _execute_with_retry(self, case_id: str, action: str, amount: float | None,
                                  idempotency_key: str, approval_ref: str | None) -> dict:
        """带重试的执行（B2）：确定性门控/幂等错误码（_NON_RETRYABLE）不重试；
        其余错误码与网络/会话异常重试，退避 0.3s/1s；耗尽返回最后一次结果。"""
        result = {"code": "E-MCP-UNAVAILABLE", "message": "mcp-core 不可达"}
        for attempt in range(len(_BACKOFFS) + 1):
            try:
                result = await self.core.execute_disposition(
                    case_id, action, amount, idempotency_key, approval_ref)
            except Exception as e:  # noqa: BLE001 —— 网络/会话异常 → 重试
                result = {"code": "E-MCP-UNAVAILABLE", "message": str(e)}
            else:
                if not result.get("code") or result["code"] in _NON_RETRYABLE:
                    return result
            if attempt < len(_BACKOFFS):
                await asyncio.sleep(_BACKOFFS[attempt])
        return result

    async def _fail_to_manual(self, case_id: str, version: int, trace_id: str,
                              reason: str) -> str:
        """处置失败 → DispositionFailed→MANUAL_REVIEW + 审计（消除 DISPOSING 死胡同，B1/B2）"""
        await self._audit(case_id, "disposition.failed",
                          f"{reason}，升级人工复核（BA-BR-01 失败兜底）", trace_id)
        out = await self.cases.transition(
            case_id, CaseEvent.DISPOSITION_FAILED, ACTOR_DISP, version,
            basis=f"处置失败转人工复核：{reason}")
        return out["status"]

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

    async def review_confirm(self, case_id: str, operator: str, comment: str = "",
                             escalated: bool = False) -> dict:
        """复核确认自动建单（US-E5-04，API-W-07 block/escalate 分支）：
        ReviewConfirmed（human_only）→ 建处置审批工单（API-M-11）→ 返回工单号。
        escalate 升级建单：审计 basis 与 context_json 额外标记 escalated=true。
        定性仍须人工审批（02 §3.3 人机边界），本方法只建工单不执行处置。"""
        case = await self.cases.get(case_id)
        if not case:
            raise LookupError(case_id)
        basis = f"人工复核确认：{comment}" if comment else "人工复核确认欺诈（BA-BP-05）"
        if escalated:
            basis += "（escalated=true 升级建单）"
        out = await self.cases.transition(
            case_id, CaseEvent.REVIEW_CONFIRMED, operator, case["version"], basis=basis)
        appr = await self.core.create_approval_request(
            case_id, "freeze", None,
            reason=f"人工复核确认欺诈（{operator}）：{comment}（US-E5-04，BA-BR-01）")
        await self.pool.execute(
            "UPDATE approval_record SET opinion=$2 WHERE approval_id=$1",
            appr["approval_id"], comment or "复核确认转审批")
        if escalated:  # 升级标记入共享状态，供后续环节与审计回放识别
            await self.pool.execute(
                """UPDATE risk_case
                   SET context_json=COALESCE(context_json,'{}'::jsonb) || '{"escalated":true}'::jsonb
                   WHERE case_id=$1""", case_id)
        return {"case_id": case_id, "status": out["status"], "version": out["version"],
                "approval_id": appr["approval_id"]}

    async def _load(self, approval_id: str):
        rec = await self.pool.fetchrow(
            "SELECT * FROM approval_record WHERE approval_id=$1", approval_id)
        if not rec:
            raise LookupError(approval_id)
        if rec["decision"] != "pending":
            zh = {"approved": "已批准", "rejected": "已驳回"}.get(rec["decision"], rec["decision"])
            raise DispositionStateError(f"该审批单已有决策结论（{zh}），请勿重复操作")
        case = await self.cases.get(rec["case_id"])
        return rec, case

    async def _decide(self, approval_id: str, decision: str, approver: str,
                      opinion: str, case_id: str, trace_id: str):
        """决策回填 DA-T-07 + 审计（tg_web UPDATE 权限，02-roles.sql）

        B4：条件 UPDATE（decision='pending' 谓词）为终审，消除 _load 预检与
        UPDATE 之间的 TOCTOU 竞态；0 行影响 → 审批单已决（事务连同审计一并回滚）。
        """
        async with self.pool.acquire() as conn, conn.transaction():
            updated = await conn.execute(
                "UPDATE approval_record SET decision=$1, approver=$2, opinion=$3, "
                "decided_at=now() WHERE approval_id=$4 AND decision='pending'",
                decision, approver, opinion, approval_id)
            if updated == "UPDATE 0":
                raise DispositionStateError("该审批单已有决策结论，请勿重复操作")
            await conn.execute(
                """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
                   VALUES ($1, $2, 'approval.decide', $3, $4, $5)""",
                uuid.uuid4().hex, approver, approval_id,
                # R-37 复审收口：截断对齐 audit_log.basis varchar(300)；完整 opinion
                # 已由 approval_record.opinion varchar(500) 同事务留存，审计不丢要件
                f"case={case_id},decision={decision},opinion={opinion}"[:300], trace_id)

    async def _audit(self, case_id: str, action: str, basis: str, trace_id: str):
        await self.pool.execute(
            """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            # R-37 复审收口：basis 截断对齐 varchar(300)，防超长值击穿事务
            uuid.uuid4().hex, ACTOR_DISP_AUDIT, action, case_id, basis[:300], trace_id)


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
