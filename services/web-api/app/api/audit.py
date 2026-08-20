"""审计回放路由（API-W-10，SC-08，audit_log 只读）+ precheck 专家清单预检
（D1，US-E10：只读扩展，不触碰状态机）"""
import json

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/{case_id}")
async def get_audit_trail(request: Request, case_id: str):
    """API-W-10：审计链回放（DA-T-08 append-only，按时间序）"""
    return {"items": await request.app.state.cases.audit_trail(case_id)}


@router.get("/{case_id}/precheck")
async def precheck(request: Request, case_id: str):
    """D1 专家清单预检（US-E10）：以只读查询对案件处置要件逐项体检，
    供审批官/审计员在裁决前快速定位缺口；不写库不推进状态（只读扩展）。

    检查项（确定性，与既有不变量同源）：
      signals_present    信号链存在（DA-T-04）
      evidence_chain     证据链 ≥2 条（DA-INV-04 冻结类要求同源，BA-BR-03）
      hypothesis_fixed   调查定性非「待定」（02 §3.3 人机边界）
      cross_review_done  AG-01 合规互审已留痕（R-47）
      debate_recorded    控辩辩论记录已落审批单（C1，BA-BR-19/DA-INV-09）
      disposition_anchored 已处置案件有执行记录（闭环体检）
    """
    pool = request.app.state.pool
    case = await request.app.state.cases.get(case_id)
    if not case:
        return {"code": "E-NOT-FOUND", "message": f"案件 {case_id} 不存在"}

    sig_n = await pool.fetchval(
        "SELECT COUNT(*) FROM risk_signal WHERE case_id=$1", case_id)
    ev_n = await pool.fetchval(
        "SELECT COUNT(*) FROM case_evidence WHERE case_id=$1", case_id)
    mem = await pool.fetchval(
        "SELECT summary FROM agent_memory WHERE case_id=$1 AND agent_id='AA-AG-03'"
        " ORDER BY ts DESC LIMIT 1", case_id)
    pattern = ""
    if mem:
        try:
            pattern = str(json.loads(mem).get("pattern", ""))
        except (TypeError, ValueError):
            pattern = ""
    review_n = await pool.fetchval(
        "SELECT COUNT(*) FROM audit_log WHERE target=$1"
        " AND action='disposition.reviewed'", case_id)
    debate_n = await pool.fetchval(
        "SELECT COUNT(*) FROM approval_record WHERE case_id=$1"
        " AND debate_json IS NOT NULL", case_id)
    disp_n = await pool.fetchval(
        "SELECT COUNT(*) FROM disposition_record WHERE case_id=$1", case_id)

    items = [
        {"id": "signals_present", "name": "信号链存在",
         "status": "ok" if sig_n else "fail",
         "basis": f"risk_signal {sig_n} 条"},
        {"id": "evidence_chain", "name": "证据链充分（≥2 条）",
         "status": "ok" if ev_n >= 2 else ("warn" if ev_n == 1 else "fail"),
         "basis": f"case_evidence {ev_n} 条（DA-INV-04 冻结类要求同源）"},
        {"id": "hypothesis_fixed", "name": "调查定性非待定",
         "status": "ok" if pattern and pattern != "待定" else "warn",
         "basis": f"定性[{pattern or '无调查记忆'}]（待定则需人工补强）"},
        {"id": "cross_review_done", "name": "AG-01 合规互审留痕",
         "status": "ok" if review_n else "warn",
         "basis": f"disposition.reviewed 审计 {review_n} 条"},
        {"id": "debate_recorded", "name": "控辩辩论记录在案",
         "status": "ok" if debate_n else "warn",
         "basis": f"debate_json 审批单 {debate_n} 张（BA-BR-19）"},
        {"id": "disposition_anchored", "name": "处置执行记录闭环",
         "status": ("ok" if disp_n else "warn") if case["status"] == "DISPOSED"
         else "ok",
         "basis": f"disposition_record {disp_n} 条，案件状态 {case['status']}"},
    ]
    return {
        "case_id": case_id,
        "status": case["status"],
        "items": items,
        "passed": all(i["status"] != "fail" for i in items),
    }
