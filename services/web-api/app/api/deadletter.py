"""DLQ 死信清单与人工复位路由（LoopEngine 失败归宿可见性入口）

语义（app/core/loop_engine.py）：驻车案件停止自动重试、不改案件状态；
复位放行必须人工（X-Operator → resolved_by），复位只清零 attempts 不删行。
"""
from fastapi import APIRouter, HTTPException, Request

from ..core.loop_engine import deadletter_list, deadletter_retry
from .common import operator_from_header

router = APIRouter(prefix="/api/deadletter", tags=["loop-engine"])


@router.get("")
async def list_deadletter(request: Request, parked_only: bool = True):
    """DLQ 清单（默认仅驻车行）：人工可见性入口，环的失败不再只留日志"""
    return {"items": await deadletter_list(
        request.app.state.pool, parked_only=parked_only)}


@router.post("/{case_id}/retry")
async def retry_deadletter(request: Request, case_id: str):
    """人工复位放行：解除驻车、清零累计，案件重新进入 worker 轮询候选。
    复位人与时间落 resolved_by/resolved_at（审计可回放谁放行了哪辆车）。
    人工门（LoopEngine 纪律）：agent: 自声明一律 409 E-HUMAN-ONLY，
    环不得自清自己的失败归宿。"""
    actor = operator_from_header(
        request.headers.get("X-Operator"), "human:risk_oncall")
    if not actor.startswith("human:"):
        raise HTTPException(409, detail={
            "code": "E-HUMAN-ONLY",
            "message": "DLQ 复位为人工门，仅 human:* 可放行（环不得自清失败归宿）"})
    res = await deadletter_retry(request.app.state.pool, case_id, actor)
    if not res["ok"]:
        raise HTTPException(404, detail={
            "code": "E-NOT-FOUND",
            "message": "DLQ 无该案件记录，无需复位（可能已被其他途径处理）"})
    return res
