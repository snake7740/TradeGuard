"""审批门户路由（API-W-08/09，02 §7 状态机人类触发入口，SC-02/03/09）"""
from fastapi import APIRouter, Header, HTTPException, Request

from ..core.state_machine import InvalidTransition
from ..repositories import OptimisticLockError
from ..schemas import DecideIn
from ..skills.disposition import DispositionStateError
from .common import operator_from_header

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


@router.get("")
async def list_approvals(request: Request, decision: str = "pending"):
    """API-W-08：待审批队列（BA-BR-13 超时标红：escalated_at 非空即标红，SC-09）"""
    return {"items": await request.app.state.approvals.list(decision)}


@router.post("/{approval_id}/decide")
async def decide_approval(request: Request, approval_id: str, body: DecideIn,
                          x_operator: str | None = Header(None)):
    """API-W-09：批准/驳回 → 委托 AA-SK-03 内核闭环编排（US-E5-03，SC-02/03）：
    approve→ApprovalApproved→自动执行处置至 DISPOSED；reject→ApprovalRejected→
    RollbackToReview 回退人工复核并禁用自动通道（BA-BR-07）。
    审批人从 X-Operator 头解码，缺省 human:approver。"""
    svc = request.app.state.disposition
    approver = operator_from_header(x_operator, "human:approver")
    try:
        if body.decision == "approve":
            return await svc.approve(approval_id, approver, body.opinion)
        return await svc.reject(approval_id, approver, body.opinion)
    except LookupError:
        raise HTTPException(404, detail={"code": "E-NOT-FOUND", "message": "未找到该审批单，请刷新队列重试"})
    except DispositionStateError as e:
        raise HTTPException(409, detail={"code": "E-ALREADY-DECIDED", "message": str(e)})
    except InvalidTransition as e:
        raise HTTPException(409, detail={"code": e.code, "message": e.message})
    except OptimisticLockError as e:
        raise HTTPException(409, detail={"code": e.code, "message": str(e)})
