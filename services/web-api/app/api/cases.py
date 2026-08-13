"""事件工作台路由（API-W-02~07，案件工作台 SC-01/04/10 载体）"""
from fastapi import APIRouter, HTTPException, Request

from ..core.state_machine import CaseEvent, InvalidTransition
from ..repositories import OptimisticLockError
from ..schemas import ReviewIn

router = APIRouter(prefix="/api/cases", tags=["cases"])


@router.get("")
async def list_cases(request: Request, status: str | None = None, limit: int = 50):
    """API-W-02：事件列表（真实读库）"""
    return {"items": await request.app.state.cases.list(status, limit)}


@router.get("/{case_id}")
async def get_case(request: Request, case_id: str):
    """API-W-03：事件详情（含共享状态 context_json 与乐观锁 version）"""
    case = await request.app.state.cases.get(case_id)
    if not case:
        raise HTTPException(404, detail={"code": "E-NOT-FOUND", "message": "case not found"})
    return case


@router.get("/{case_id}/signals")
async def list_signals(request: Request, case_id: str):
    """API-W-04：信号清单（DA-T-04，含 velocity_json BA-BR-14）"""
    return {"items": await request.app.state.cases.signals(case_id)}


@router.get("/{case_id}/graph")
async def get_graph(request: Request, case_id: str, hops: int = 2):
    """API-W-05：关联网络图谱（BA-BR-06；UnifiedModel 退化路径 fn_related_graph，03 §3）"""
    case = await request.app.state.cases.get(case_id)
    if not case:
        raise HTTPException(404, detail={"code": "E-NOT-FOUND", "message": "case not found"})
    hops = min(max(hops, 1), 2)  # 2 跳上限（AA-SK-02 安全边界）
    rows = await request.app.state.pool.fetch(
        "SELECT * FROM fn_related_graph($1, $2)", case["subject_ref"], hops)
    return {"subject_ref": case["subject_ref"], "edges": [dict(r) for r in rows]}


@router.get("/{case_id}/evidence")
async def list_evidence(request: Request, case_id: str):
    """API-W-06：证据链（DA-T-05 只增表，BA-BR-03）"""
    return {"items": await request.app.state.cases.evidence(case_id)}


@router.post("/{case_id}/review")
async def submit_review(request: Request, case_id: str, body: ReviewIn):
    """API-W-07：中风险人工复核（SC-10，BA-BP-05）
    状态机人类触发入口：confirm→PENDING_APPROVAL（冻结需审批 BA-BR-01）；
    dismiss→ARCHIVED（排除欺诈归档）。actor 守卫 human_only（02 §7）。"""
    event = CaseEvent.REVIEW_CONFIRMED if body.decision == "confirm" else CaseEvent.REVIEW_DISMISSED
    case = await request.app.state.cases.get(case_id)
    if not case:
        raise HTTPException(404, detail={"code": "E-NOT-FOUND", "message": "case not found"})
    try:
        return await request.app.state.cases.transition(
            case_id, event, body.operator, case["version"], basis=body.comment)
    except InvalidTransition as e:
        raise HTTPException(409, detail={"code": e.code, "message": e.message})
    except OptimisticLockError as e:
        raise HTTPException(409, detail={"code": e.code, "message": str(e)})


# TODO(US-E4-05)：API-W-07 复核确认后自动创建处置审批工单（DA-T-07）——
# 当前仅完成状态迁移与事件发布，工单创建随 E4 处置闭环落地。
