"""告警受理路由（API-W-01，AA-CL-01 入口，SC-01~04 起点）"""
from fastapi import APIRouter, Header, Request

from ..schemas import SEVERITY_SCORES, AlertIn
from .common import operator_from_header

router = APIRouter(tags=["alerts"])


@router.post("/api/alerts", status_code=202)
async def create_alert(request: Request, body: AlertIn,
                       x_operator: str | None = Header(None)):
    """API-W-01：演示触发入口 → 立案（DA-T-03）+ 审计（DA-T-08）+ 发布 CaseRegistered
    severity 枚举映射初始风险种子；受理即返回 202（后续聚合/裁决异步推进）。"""
    actor = operator_from_header(x_operator, "human:operator")
    return await request.app.state.cases.register(
        body.subject_ref, SEVERITY_SCORES[body.severity], body.source_type, actor=actor)
