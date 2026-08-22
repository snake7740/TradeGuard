"""案件工作台路由（API-W-02~07/17~19/22/28~30，SC-01/04/10/25~27 载体）"""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Query, Request

from ..api_guards import ALL_HUMAN_ROLES
from ..core.state_machine import CaseEvent, InvalidTransition
from ..repositories import OptimisticLockError
from ..schemas import DispositionIn, ReopenIn, ReviewIn, VerifyIn
from ..skills.aggregation import AggregationStateError
from ..skills.case_governance import (AGING_HOURS_DEFAULT, aging_breach,
                                      aging_hours, priority_tier)
from ..skills.disposition import DispositionStateError
from ..skills.investigation import InvestigationStateError
from ..skills.narrative import build_case_narrative
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


@router.get("/queue")
async def case_queue(request: Request, size: int = Query(50, ge=1, le=200)):
    """API-W-28：案件优先级队列（BA-BR-26，SC-25，US-E17）
    告警积压治理：队列按风险分级（high/mid/low，边界复用 BA-BR-02/01）而非
    立案时序排布，并富化 aging 滞留小时与超期标记（阈值经 br-26-aging-hours
    热配置，Nacos 优先 sys_config 降级）——主管看板主动管理而非事后追责。"""
    threshold = AGING_HOURS_DEFAULT
    cfg = getattr(request.app.state, "config", None)
    try:
        threshold = int(cfg.values.get("br-26-aging-hours", AGING_HOURS_DEFAULT))
    except (AttributeError, TypeError, ValueError):
        pass
    now = datetime.now(timezone.utc)
    items = []
    for c in await request.app.state.cases.queue(size):
        hours = aging_hours(
            datetime.fromisoformat(c["updated_at"]), now) if c["updated_at"] else 0.0
        items.append(c | {"priority_tier": priority_tier(c["risk_score"]),
                          "aging_hours": hours,
                          "aging_breach": aging_breach(hours, threshold)})
    return {"threshold_hours": threshold, "items": items}


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


@router.post("/{case_id}/disposition")
async def submit_disposition(request: Request, case_id: str, body: DispositionIn):
    """API-W-23：处置提交（AA-SK-03 确定性内核，US-E5-02，SC-02/07/10 载体）
    调查完成（PENDING_APPROVAL/INVESTIGATING）后的人工提交入口，补齐 UI 全链
    「调查→审批」交接：高风险无凭证 → mcp-core 拒 E-DISP-AUTH → 建审批工单转待审批
    （SC-02）；中风险无凭证 → refused_mid_risk（E-DISP-SCOPE，SC-10）；已批凭证 →
    自动执行至 DISPOSED。同案已存在待决工单时幂等返回既有工单（一案一单）。"""
    # 一案一单：mcp-core create_approval_request 无 pending 去重，端点层守护
    pending = await request.app.state.pool.fetchrow(
        """SELECT a.approval_id, c.status FROM approval_record a
           JOIN risk_case c ON c.case_id = a.case_id
           WHERE a.case_id=$1 AND a.decision='pending'""", case_id)
    if pending:
        return {"case_id": case_id, "route": "approval_required",
                "code": "E-DISP-AUTH", "approval_id": pending["approval_id"],
                "case_status": pending["status"], "duplicate": True}
    key = body.idempotency_key or f"{case_id}:{body.action}:manual"
    try:
        return await request.app.state.disposition.submit(
            case_id, body.action, body.amount, key)
    except LookupError:
        raise HTTPException(404, detail={"code": "E-NOT-FOUND", "message": "未找到该案件，请核对案件编号"})
    except DispositionStateError as e:
        raise HTTPException(409, detail={"code": e.code, "message": str(e)})
    except InvalidTransition as e:
        raise HTTPException(409, detail={"code": e.code, "message": e.message})
    except OptimisticLockError as e:
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


@router.post("/{case_id}/reopen")
async def reopen_case(request: Request, case_id: str, body: ReopenIn,
                      x_operator: str | None = Header(None)):
    """API-W-29：归档复位（BA-BR-28，SC-27，US-E19）
    自动关闭/归档案件的人工复位通道：ARCHIVED→MANUAL_REVIEW（human_only，
    标准修订/误关补救），事由必填入审计；非归档案件复位 → 409 E-BAD-TRANSITION，
    agent:/未授权角色 → 403/409 语义分层（api_guards + 状态机双防线）。"""
    case = await request.app.state.cases.get(case_id)
    if not case:
        raise HTTPException(404, detail={"code": "E-NOT-FOUND", "message": "未找到该案件，请核对案件编号"})
    operator = operator_from_header(x_operator, "human:operator")
    try:
        return await request.app.state.cases.transition(
            case_id, CaseEvent.CASE_REOPENED, operator, case["version"],
            basis=f"归档复位：{body.basis}（BA-BR-28 人工复位通道）")
    except InvalidTransition as e:
        raise HTTPException(409, detail={"code": e.code, "message": e.message})
    except OptimisticLockError as e:
        raise HTTPException(409, detail={"code": e.code, "message": str(e)})


# 叙事生成仅对已识别人类角色开放（同 kb/ask BA-BR-23 模式）：
# DRAFT 待人工审校，生成可追责到人；agent:/未识别调用方一律 403。
# 角色集合与中央 RBAC（api_guards.PATH_ROLE_RULES）同源，防双源漂移
NARRATIVE_ALLOWED_ROLES = ALL_HUMAN_ROLES


@router.post("/{case_id}/narrative")
async def generate_narrative(request: Request, case_id: str,
                             x_operator: str | None = Header(None)):
    """API-W-30：案件叙事草稿（BA-BR-27，SC-26，US-E18，docs/13 D2 闭合）
    以证据链为唯一素材装配五段叙事（引用 token 对齐防幻觉），产物 DRAFT
    待人工审校定稿；生成行为留痕 audit narrative.generated。"""
    operator = operator_from_header(x_operator, "")
    role = operator[len("human:"):] if operator.startswith("human:") else operator
    if role not in NARRATIVE_ALLOWED_ROLES:
        raise HTTPException(403, detail={"code": "E-FORBIDDEN-ROLE",
                                         "message": "叙事生成仅对人工角色开放（BA-BR-27，DRAFT 待人工审校）"})
    try:
        return await build_case_narrative(
            request.app.state.pool, case_id, f"human:{role}")
    except LookupError:
        raise HTTPException(404, detail={"code": "E-NOT-FOUND", "message": "未找到该案件，请核对案件编号"})
