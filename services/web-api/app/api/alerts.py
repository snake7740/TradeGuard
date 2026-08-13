"""告警受理路由（API-W-01，AA-CL-01 入口，SC-01~04 起点）"""
from fastapi import APIRouter, Request

from ..schemas import AlertIn

router = APIRouter(tags=["alerts"])


@router.post("/api/alerts", status_code=201)
async def create_alert(request: Request, body: AlertIn):
    """API-W-01：演示触发入口 → 立案（DA-T-03）+ 审计（DA-T-08）+ 发布 CaseRegistered"""
    return await request.app.state.cases.register(body.subject_ref, body.severity, body.source_type)
