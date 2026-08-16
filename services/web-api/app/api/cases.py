"""案件工作台路由（API-W-02~07/17~19/22，SC-01/04/10 载体）"""
import json

from fastapi import APIRouter, Header, HTTPException, Query, Request

from ..core.state_machine import CaseEvent, InvalidTransition
from ..repositories import OptimisticLockError
from ..schemas import ReviewIn, VerifyIn
from ..skills.aggregation import AggregationStateError
from ..skills.investigation import InvestigationStateError
from ..skills.verification import VerificationStateError
from .common import operator_from_header

router = APIRouter(prefix="/api/cases", tags=["cases"])


@router.get("")
async def list_cases(request: Request, status: str | None = None,
                     risk_min: int | None = Query(None, ge=0, le=100),
                     page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100)):
    """API-W-02：案件列表（真实读库，分页 + status/risk_min 过滤）"""
    total, items = await request.app.state.cases.list(status, risk_min, page, size)
    return {"total": total, "items": items}


@router.get("/{case_id}")
async def get_case(request: Request, case_id: str):
    """API-W-03：案件详情（含共享状态 context_json 与乐观锁 version）"""
    case = await request.app.state.cases.get(case_id)
    if not case:
        raise HTTPException(404, detail={"code": "E-NOT-FOUND", "message": "未找到该案件，请核对案件编号"})
    return case


@router.get("/{case_id}/signals")
async def list_signals(request: Request, case_id: str):
    """API-W-04：信号清单（DA-T-04，含 velocity_json BA-BR-14）"""
    return {"items": await request.app.state.cases.signals(case_id)}


@router.post("/{case_id}/aggregate")
async def aggregate_case(request: Request, case_id: str):
    """API-W-17：触发信号聚合（AA-SK-01 确定性内核，US-E3-03/04，SC-01/SC-11 载体）
    裁决路由：noise→ARCHIVED；auto_release→DISPOSED（BA-CAP-05）；investigate→INVESTIGATING；
    all_fail→保持 AGGREGATING 转人工（E-AGG-ALL-FAIL）。
    与 EventWorker 共用每 case_id 单飞锁（core/event_worker.py），防并发重复聚合。"""
    try:
        async with request.app.state.flight.lock(case_id):
            return await request.app.state.aggregation.run(case_id)
    except LookupError:
        raise HTTPException(404, detail={"code": "E-NOT-FOUND", "message": "未找到该案件，请核对案件编号"})
    except AggregationStateError as e:
        raise HTTPException(409, detail={"code": e.code, "message": str(e)})


@router.get("/{case_id}/graph")
async def get_graph(request: Request, case_id: str, hops: int = 2):
    """API-W-05：关联网络图谱（BA-BR-06；UnifiedModel 退化路径 fn_related_graph，03 §3）
    契约 GraphResponse：{start, hops, nodes[{id,type,risk_flag}], links[{source,target,relation}]}"""
    case = await request.app.state.cases.get(case_id)
    if not case:
        raise HTTPException(404, detail={"code": "E-NOT-FOUND", "message": "未找到该案件，请核对案件编号"})
    hops = min(max(hops, 1), 2)  # 2 跳上限（AA-SK-02 安全边界）
    start = case["subject_ref"]
    rows = await request.app.state.pool.fetch(
        "SELECT * FROM fn_related_graph($1, $2)", start, hops)
    node_ids: list[str] = [start]
    links = []
    for r in rows:
        src, dst = r["src_node"].strip(), r["dst_node"].strip()
        for n in (src, dst):
            if n not in node_ids:
                node_ids.append(n)
        links.append({"source": src, "target": dst, "relation": r["edge_type"]})
    flags = await request.app.state.pool.fetch(
        "SELECT account_hash, list_flag FROM account WHERE account_hash = ANY($1)", node_ids)
    flag_map = {f["account_hash"].strip(): f["list_flag"] for f in flags}
    nodes = [{"id": n, "type": "Account", "risk_flag": flag_map.get(n) or "none"}
             for n in node_ids]
    return {"start": start, "hops": hops, "nodes": nodes, "links": links}


@router.get("/{case_id}/evidence")
async def list_evidence(request: Request, case_id: str):
    """API-W-06：证据链（DA-T-05 只增表，BA-BR-03）"""
    return {"items": await request.app.state.cases.evidence(case_id)}


@router.get("/{case_id}/dispositions")
async def list_dispositions(request: Request, case_id: str):
    """API-W-22：处置执行记录（DA-T-06，按执行时序）；前端核验入口取 exec_id"""
    case = await request.app.state.cases.get(case_id)
    if not case:
        raise HTTPException(404, detail={"code": "E-NOT-FOUND", "message": "未找到该案件，请核对案件编号"})
    rows = await request.app.state.pool.fetch(
        "SELECT * FROM disposition_record WHERE case_id=$1 ORDER BY ts", case_id)
    items = [dict(r) for r in rows]
    for d in items:  # asyncpg 对 jsonb 可能返回 JSON 文本，端点层统一反序列化
        if isinstance(d.get("receipt"), str):
            d["receipt"] = json.loads(d["receipt"])
    return {"items": items}


@router.post("/{case_id}/investigate")
async def investigate_case(request: Request, case_id: str):
    """API-W-18：触发欺诈调查（AA-SK-02 确定性内核，US-E4-01~03 载体）
    假设匹配（规则兜底 + KB 引用 doc_id）→ 图谱 2 跳扩展（BA-BR-06 加分）→
    影响面统计 → 证据固化（DA-T-05）→ InvestigationCompleted 移交审批。"""
    try:
        return await request.app.state.investigation.run(case_id)
    except LookupError:
        raise HTTPException(404, detail={"code": "E-NOT-FOUND", "message": "未找到该案件，请核对案件编号"})
    except InvestigationStateError as e:
        raise HTTPException(409, detail={"code": e.code, "message": str(e)})


@router.post("/{case_id}/verify")
async def verify_case(request: Request, case_id: str, body: VerifyIn):
    """API-W-19：触发结果核验（AA-SK-04 确定性内核，US-E6-01/02 载体）
    一致→VERIFIED→ARCHIVED（复盘入库申请）；不一致→反向处置→MANUAL_REVIEW+P0。"""
    try:
        return await request.app.state.verification.verify(case_id, body.exec_id)
    except LookupError:
        raise HTTPException(404, detail={"code": "E-NOT-FOUND", "message": "未找到该案件或对应的处置记录，请核对编号"})
    except VerificationStateError as e:
        raise HTTPException(409, detail={"code": e.code, "message": str(e)})


@router.post("/{case_id}/review")
async def submit_review(request: Request, case_id: str, body: ReviewIn,
                        x_operator: str | None = Header(None)):
    """API-W-07：中风险人工复核（SC-10，BA-BP-05）
    状态机人类触发入口：block/escalate→委托 DispositionService.review_confirm（US-E5-04
    自动创建处置审批单，escalate 额外审计标记）；release→ARCHIVED（排除欺诈归档）。
    actor 从 X-Operator 头解码，守卫 human_only（02 §7）。"""
    case = await request.app.state.cases.get(case_id)
    if not case:
        raise HTTPException(404, detail={"code": "E-NOT-FOUND", "message": "未找到该案件，请核对案件编号"})
    operator = operator_from_header(x_operator, "human:operator")
    if body.conclusion in ("block", "escalate"):
        try:
            return await request.app.state.disposition.review_confirm(
                case_id, operator, body.opinion, escalated=(body.conclusion == "escalate"))
        except InvalidTransition as e:
            raise HTTPException(409, detail={"code": e.code, "message": e.message})
        except OptimisticLockError as e:
            raise HTTPException(409, detail={"code": e.code, "message": str(e)})
    try:
        return await request.app.state.cases.transition(
            case_id, CaseEvent.REVIEW_DISMISSED, operator, case["version"],
            basis=body.opinion)
    except InvalidTransition as e:
        raise HTTPException(409, detail={"code": e.code, "message": e.message})
    except OptimisticLockError as e:
        raise HTTPException(409, detail={"code": e.code, "message": str(e)})
