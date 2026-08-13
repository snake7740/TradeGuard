"""AA-MCP-01 交易风控业务库 MCP Server（02 §5，streamable-http :8101）
工具全集对齐 07 §5.2 API-M-01~06；只读工具真实查 PolarDB-PG，
图查询走 UnifiedModel 退化路径 fn_related_graph（03 §3）。
处置执行工具 execute_disposition 含审批门控（DA-INV-02/03，E-DISP-AUTH）。
"""
import json
import os
import uuid

import asyncpg
from mcp.server.fastmcp import FastMCP

PG_DSN = os.getenv("PG_DSN", "postgresql://tg_app:tg_app_dev@localhost:5432/tradeguard")

mcp = FastMCP("tradeguard-core", host="0.0.0.0", port=int(os.getenv("MCP_PORT", "8101")))

HIGH_RISK_SCORE = 70  # BA-BR-01；正式值经 Nacos 下发（SC-06）


async def _conn():
    return await asyncpg.connect(PG_DSN)


@mcp.tool()
async def query_transactions(account_hash: str, hours: int = 24, limit: int = 100) -> str:
    """API-M-01：主体流水回查（近 N 小时，按时间倒序）"""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """SELECT tx_id, amount, mcc, channel, geo, ts FROM transaction
               WHERE account_hash=$1 AND ts >= now() - make_interval(hours=>$2)
               ORDER BY ts DESC LIMIT $3""", account_hash, hours, limit)
        return json.dumps([dict(r) | {"ts": r["ts"].isoformat(), "amount": str(r["amount"])} for r in rows],
                          ensure_ascii=False, default=str)
    finally:
        await conn.close()


@mcp.tool()
async def query_related_graph(account_hash: str, hops: int = 2) -> str:
    """API-M-02：关联图谱查询（UnifiedModel 退化路径，2 跳上限 AA-SK-02 安全边界）"""
    hops = min(max(hops, 1), 2)
    conn = await _conn()
    try:
        rows = await conn.fetch("SELECT * FROM fn_related_graph($1, $2)", account_hash, hops)
        return json.dumps([dict(r) for r in rows], ensure_ascii=False, default=str)
    finally:
        await conn.close()


@mcp.tool()
async def query_case_signals(case_id: str) -> str:
    """辅助只读：事件信号聚合读（含 velocity_json，BA-BR-14；非 API-M 契约项，AA-SK-02 内部依赖）"""
    conn = await _conn()
    try:
        rows = await conn.fetch("SELECT * FROM risk_signal WHERE case_id=$1 ORDER BY ts", case_id)
        return json.dumps([dict(r) for r in rows], ensure_ascii=False, default=str)
    finally:
        await conn.close()


@mcp.tool()
async def query_disposition_result(exec_id: str) -> str:
    """API-M-04：处置结果回查（AA-SK-04 核验依据）"""
    conn = await _conn()
    try:
        r = await conn.fetchrow("SELECT * FROM disposition_record WHERE exec_id=$1", exec_id)
        return json.dumps(dict(r), ensure_ascii=False, default=str) if r else '{"error":"E-NOT-FOUND"}'
    finally:
        await conn.close()


@mcp.tool()
async def execute_disposition(case_id: str, action: str, amount: float | None,
                              idempotency_key: str, approval_ref: str | None = None) -> str:
    """API-M-03：处置执行（审批门控 DA-INV-02 + 幂等 DA-INV-03）"""
    if action not in ("block", "freeze", "reduce", "release"):
        return json.dumps({"code": "E-BAD-ACTION", "message": f"非法处置动作 {action}"})
    conn = await _conn()
    try:
        case = await conn.fetchrow("SELECT risk_score FROM risk_case WHERE case_id=$1", case_id)
        if not case:
            return json.dumps({"code": "E-NOT-FOUND", "message": "case 不存在"})
        # 高风险处置必须携带审批凭证（SC-02）
        if case["risk_score"] >= HIGH_RISK_SCORE and action != "release" and not approval_ref:
            approved = await conn.fetchrow(
                "SELECT approval_id FROM approval_record WHERE case_id=$1 AND decision='approved'", case_id)
            if not approved:
                return json.dumps({"code": "E-DISP-AUTH",
                                   "message": "高风险处置缺审批凭证，已拒绝并转审批（BA-BR-02）"})
            approval_ref = approved["approval_id"]
        # 幂等：冲突即返回首次结果（E-IDEMPOTENT-CONFLICT）
        existed = await conn.fetchrow(
            "SELECT exec_id, status FROM disposition_record WHERE idempotency_key=$1", idempotency_key)
        if existed:
            return json.dumps({"code": "E-IDEMPOTENT-CONFLICT", "first_result": dict(existed)}, default=str)
        exec_id = uuid.uuid4().hex
        await conn.execute(
            """INSERT INTO disposition_record (exec_id, case_id, action, amount, idempotency_key, approval_ref, status)
               VALUES ($1,$2,$3,$4,$5,$6,'submitted')""",
            exec_id, case_id, action, amount, idempotency_key, approval_ref)
        await conn.execute(
            """INSERT INTO audit_log (log_id, actor, action, target, basis)
               VALUES ($1, 'AA-AG-04', 'disposition.submit', $2, $3)""",
            uuid.uuid4().hex, case_id, f"action={action},approval_ref={approval_ref}")
        return json.dumps({"exec_id": exec_id, "status": "submitted"}, ensure_ascii=False)
    finally:
        await conn.close()


@mcp.tool()
async def submit_kb_application(case_id: str, category: str, title: str, content: str) -> str:
    """API-M-05：知识入库申请（AA-SK-05；仅写 pending 申请单，发布由人类经 API-W-12 确认，DA-INV-06）"""
    if category not in ("case", "regulation", "runbook"):
        return json.dumps({"code": "E-BAD-CATEGORY", "message": f"非法类目 {category}"})
    doc_id = uuid.uuid4().hex
    conn = await _conn()
    try:
        await conn.execute(
            """INSERT INTO kb_document (doc_id, category, title, content, status, applicant)
               VALUES ($1, $2, $3, $4, 'pending', 'AA-AG-05')""",
            doc_id, category, title, content)
        await conn.execute(
            """INSERT INTO audit_log (log_id, actor, action, target, basis)
               VALUES ($1, 'AA-AG-05', 'kb.apply', $2, $3)""",
            uuid.uuid4().hex, doc_id, f"case={case_id},category={category}")
        return json.dumps({"doc_id": doc_id, "status": "pending"}, ensure_ascii=False)
    finally:
        await conn.close()


@mcp.tool()
async def query_audit_trail(case_id: str) -> str:
    """API-M-06：审计链回放（DA-T-08 append-only 只读，SC-08）"""
    conn = await _conn()
    try:
        rows = await conn.fetch("SELECT * FROM audit_log WHERE target=$1 ORDER BY ts", case_id)
        return json.dumps([dict(r) for r in rows], ensure_ascii=False, default=str)
    finally:
        await conn.close()


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
