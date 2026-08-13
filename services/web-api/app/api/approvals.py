"""审批门户路由（API-W-08/09，02 §7 状态机人类触发入口，SC-02/03/09）"""
from fastapi import APIRouter, HTTPException, Request

from ..core.state_machine import InvalidTransition
from ..repositories import OptimisticLockError
from ..schemas import DecideIn
from ..skills.disposition import DispositionStateError

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


@router.get("")
async def list_approvals(request: Request, decision: str = "pending"):
    """API-W-08：待审批队列（BA-BR-13 超时标红：escalated_at 非空即标红，SC-09）"""
    return {"items": await request.app.state.approvals.list(decision)}


@router.post("/{approval_id}/decide")
async def decide_approval(request: Request, approval_id: str, body: DecideIn):
    """API-W-09：批准/驳回 → 委托 AA-SK-03 内核闭环编排（US-E5-03，SC-02/03）：
    批准→ApprovalApproved→自动执行处置至 DISPOSED；驳回→ApprovalRejected→
    RollbackToReview 回退人工复核并禁用自动通道（BA-BR-07）。"""
    svc = request.app.state.disposition
    try:
        if body.decision == "approved":
            return await svc.approve(approval_id, body.approver, body.comment or "")
        return await svc.reject(approval_id, body.approver, body.comment or "")
    except LookupError:
        raise HTTPException(404, detail={"code": "E-NOT-FOUND", "message": "approval not found"})
    except DispositionStateError as e:
        raise HTTPException(409, detail={"code": "E-ALREADY-DECIDED", "message": str(e)})
    except InvalidTransition as e:
        raise HTTPException(409, detail={"code": e.code, "message": e.message})
    except OptimisticLockError as e:
        raise HTTPException(409, detail={"code": e.code, "message": str(e)})
