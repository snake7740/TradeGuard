"""审批门户路由（API-W-08/09，02 §7 状态机人类触发入口，SC-02/03/09）"""
import uuid

from fastapi import APIRouter, HTTPException, Request

from ..core.state_machine import CaseEvent, InvalidTransition
from ..repositories import OptimisticLockError
from ..schemas import DecideIn

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


@router.get("")
async def list_approvals(request: Request, decision: str = "pending"):
    """API-W-08：待审批队列（BA-BR-13 超时标红由前端按 created_at 计算）"""
    return {"items": await request.app.state.approvals.list(decision)}


@router.post("/{approval_id}/decide")
async def decide_approval(request: Request, approval_id: str, body: DecideIn):
    """API-W-09：批准/驳回 → 回填 DA-T-07 + 审计留痕 + 状态机迁移 + 发布
    ApprovalApproved/Rejected 事件（web-api 代人类发布，actor 记录真实操作人，03 §9.2）"""
    pool = request.app.state.pool
    rec = await pool.fetchrow("SELECT * FROM approval_record WHERE approval_id=$1", approval_id)
    if not rec:
        raise HTTPException(404, detail={"code": "E-NOT-FOUND", "message": "approval not found"})
    if rec["decision"] != "pending":
        raise HTTPException(409, detail={"code": "E-ALREADY-DECIDED",
                                         "message": f"工单已决（{rec['decision']}），禁止重复回填"})
    case = await request.app.state.cases.get(rec["case_id"])
    event = CaseEvent.APPROVAL_APPROVED if body.decision == "approved" else CaseEvent.APPROVAL_REJECTED
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "UPDATE approval_record SET decision=$1, approver=$2, opinion=$3, decided_at=now() WHERE approval_id=$4",
            body.decision, body.approver, body.comment, approval_id)
        await conn.execute(
            """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
               VALUES ($1, $2, 'approval.decide', $3, $4, $5)""",
            uuid.uuid4().hex, body.approver, approval_id,
            f"case={rec['case_id']},decision={body.decision}", case["trace_id"])
    try:
        result = await request.app.state.cases.transition(
            rec["case_id"], event, body.approver, case["version"], basis=f"approval={approval_id}")
    except InvalidTransition as e:
        raise HTTPException(409, detail={"code": e.code, "message": e.message})
    except OptimisticLockError as e:
        raise HTTPException(409, detail={"code": e.code, "message": str(e)})
    # TODO(US-E5-04)：驳回后自动发布回滚任务（RollbackToReview，BA-BR-07）由 AA-AG-04 订阅
    # ApprovalRejected 事件触发，随 E5 处置回滚闭环落地。
    return {"approval_id": approval_id, "decision": body.decision, "case": result}
