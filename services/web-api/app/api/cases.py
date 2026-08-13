"""事件工作台路由（API-W-02~07/17~19，案件工作台 SC-01/04/10 载体）"""
from fastapi import APIRouter, HTTPException, Request

from ..core.state_machine import CaseEvent, InvalidTransition
from ..repositories import OptimisticLockError
from ..schemas import ReviewIn, VerifyIn
from ..skills.aggregation import AggregationStateError
from ..skills.investigation import InvestigationStateError
from ..skills.verification import VerificationStateError

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


@router.post("/{case_id}/aggregate")
async def aggregate_case(request: Request, case_id: str):
    """API-W-17：触发信号聚合（AA-SK-01 确定性内核，US-E3-03/04，SC-01/SC-11 载体）
    裁决路由：noise→ARCHIVED；auto_release→DISPOSED（BA-CAP-05）；investigate→INVESTIGATING；
    all_fail→保持 AGGREGATING 转人工（E-AGG-ALL-FAIL）。"""
    try:
        return await request.app.state.aggregation.run(case_id)
    except LookupError:
        raise HTTPException(404, detail={"code": "E-NOT-FOUND", "message": "case not found"})
    except AggregationStateError as e:
        raise HTTPException(409, detail={"code": e.code, "message": str(e)})


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


@router.post("/{case_id}/investigate")
async def investigate_case(request: Request, case_id: str):
    """API-W-18：触发欺诈调查（AA-SK-02 确定性内核，US-E4-01~03 载体）
    假设匹配（规则兜底 + KB 引用 doc_id）→ 图谱 2 跳扩展（BA-BR-06 加分）→
    影响面统计 → 证据固化（DA-T-05）→ InvestigationCompleted 移交审批。"""
    try:
        return await request.app.state.investigation.run(case_id)
    except LookupError:
        raise HTTPException(404, detail={"code": "E-NOT-FOUND", "message": "case not found"})
    except InvestigationStateError as e:
        raise HTTPException(409, detail={"code": e.code, "message": str(e)})


@router.post("/{case_id}/verify")
async def verify_case(request: Request, case_id: str, body: VerifyIn):
    """API-W-19：触发结果核验（AA-SK-04 确定性内核，US-E6-01/02 载体）
    一致→VERIFIED→ARCHIVED（复盘入库申请）；不一致→反向处置→MANUAL_REVIEW+P0。"""
    try:
        return await request.app.state.verification.verify(case_id, body.exec_id)
    except LookupError:
        raise HTTPException(404, detail={"code": "E-NOT-FOUND", "message": "case/exec not found"})
    except VerificationStateError as e:
        raise HTTPException(409, detail={"code": e.code, "message": str(e)})


@router.post("/{case_id}/review")
async def submit_review(request: Request, case_id: str, body: ReviewIn):
    """API-W-07：中风险人工复核（SC-10，BA-BP-05）
    状态机人类触发入口：confirm→委托 DispositionService.review_confirm（US-E4-05
    自动建处置审批工单）；dismiss→ARCHIVED（排除欺诈归档）。actor 守卫 human_only（02 §7）。"""
    case = await request.app.state.cases.get(case_id)
    if not case:
        raise HTTPException(404, detail={"code": "E-NOT-FOUND", "message": "case not found"})
    if body.decision == "confirm":
        try:
            return await request.app.state.disposition.review_confirm(
                case_id, body.operator, body.comment)
        except InvalidTransition as e:
            raise HTTPException(409, detail={"code": e.code, "message": e.message})
        except OptimisticLockError as e:
            raise HTTPException(409, detail={"code": e.code, "message": str(e)})
    try:
        return await request.app.state.cases.transition(
            case_id, CaseEvent.REVIEW_DISMISSED, body.operator, case["version"],
            basis=body.comment)
    except InvalidTransition as e:
        raise HTTPException(409, detail={"code": e.code, "message": e.message})
    except OptimisticLockError as e:
        raise HTTPException(409, detail={"code": e.code, "message": str(e)})
