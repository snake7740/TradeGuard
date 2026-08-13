"""审计回放路由（API-W-10，SC-08，audit_log 只读）"""
from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/{case_id}")
async def get_audit_trail(request: Request, case_id: str):
    """API-W-10：审计链回放（DA-T-08 append-only，按时间序）"""
    return {"items": await request.app.state.cases.audit_trail(case_id)}
