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
        case = await conn.fetchrow("SELECT risk_score, trace_id FROM risk_case WHERE case_id=$1", case_id)
        if not case:
            return json.dumps({"code": "E-NOT-FOUND", "message": "case 不存在"})
        # DA-INV-04：冻结必须附证据链，缺一即拒（BA-BR-03，US-E4-03；先于审批门控：
        # 证据链是受理前提，缺证据的冻结不应进入建单通道）
        if action == "freeze":
            has_ev = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM case_evidence WHERE case_id=$1)", case_id)
            if not has_ev:
                await conn.execute(
                    """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
                       VALUES ($1, 'AA-AG-04', 'disposition.refused_evidence', $2, $3, $4)""",
                    uuid.uuid4().hex, case_id,
                    f"action=freeze 证据链为空拒绝写入（DA-INV-04，BA-BR-03）", case["trace_id"])
                return json.dumps({"code": "E-EVIDENCE-MISSING",
                                   "message": "冻结必须附书面理由与证据链（BA-BR-03）"})
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
        receipt = json.dumps({"approval_ref": approval_ref, "action": action,
                              "amount": amount}, ensure_ascii=False, default=str)
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO disposition_record (exec_id, case_id, action, amount, idempotency_key, approval_ref, status)
                   VALUES ($1,$2,$3,$4,$5,$6,'submitted')""",
                exec_id, case_id, action, amount, idempotency_key, approval_ref)
            # 执行成功置 executed + 执行凭证（SC-02：审批记录与执行凭证关联落库）
            await conn.execute(
                "UPDATE disposition_record SET status='executed', receipt=$2::jsonb WHERE exec_id=$1",
                exec_id, receipt)
            await conn.execute(
                """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
                   VALUES ($1, 'AA-AG-04', 'disposition.submit', $2, $3, $4)""",
                uuid.uuid4().hex, case_id, f"action={action},approval_ref={approval_ref}",
                case["trace_id"])
        return json.dumps({"exec_id": exec_id, "status": "executed"}, ensure_ascii=False)
    finally:
        await conn.close()


@mcp.tool()
async def create_approval_request(case_id: str, action: str, amount: float | None, reason: str) -> str:
    """API-M-11：创建处置审批工单（AA-AG-04，DA-T-07，tg_app 写角色 DA-INV-05）
    处置门控 E-DISP-AUTH 触发时建单（SC-02）：decision=pending，携带请求动作/金额，
    批准后 AA-SK-03 据此执行；人类经 API-W-09 回填决策（tg_web UPDATE）。"""
    if action not in ("block", "freeze", "reduce", "release"):
        return json.dumps({"code": "E-BAD-ACTION", "message": f"非法处置动作 {action}"})
    approval_id = uuid.uuid4().hex
    conn = await _conn()
    try:
        trace = await conn.fetchval(
            "SELECT trace_id FROM risk_case WHERE case_id=$1", case_id)
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO approval_record (approval_id, case_id, decision, opinion,
                                                requested_action, requested_amount)
                   VALUES ($1, $2, 'pending', $3, $4, $5)""",
                approval_id, case_id, reason[:500], action, amount)
            await conn.execute(
                """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
                   VALUES ($1, 'AA-AG-04', 'approval.create', $2, $3, $4)""",
                uuid.uuid4().hex, case_id, f"approval={approval_id},action={action}", trace)
        return json.dumps({"approval_id": approval_id, "status": "pending"}, ensure_ascii=False)
    finally:
        await conn.close()


@mcp.tool()
async def record_case_evidence(case_id: str, claims: list[dict]) -> str:
    """API-M-12：证据链固化（DA-T-05 只增，BA-BR-03，US-E4-03，tg_app 写角色 DA-INV-05）
    claims 元素含 claim/source_ref/confidence；同 claim+source_ref 幂等不重复插入。
    签名用 list[dict]：FastMCP 对形似 JSON 的字符串参数会预解析（同 record_case_signals）。"""
    conn = await _conn()
    try:
        trace = await conn.fetchval(
            "SELECT trace_id FROM risk_case WHERE case_id=$1", case_id)
        recorded = 0
        async with conn.transaction():
            for c in claims:
                dup = await conn.fetchval(
                    """SELECT EXISTS(SELECT 1 FROM case_evidence
                                     WHERE case_id=$1 AND claim=$2 AND source_ref=$3)""",
                    case_id, c["claim"][:500], c["source_ref"][:200])
                if dup:
                    continue
                await conn.execute(
                    """INSERT INTO case_evidence (evidence_id, case_id, claim, source_ref, confidence)
                       VALUES ($1, $2, $3, $4, $5)""",
                    uuid.uuid4().hex, case_id, c["claim"][:500], c["source_ref"][:200],
                    float(c["confidence"]))
                recorded += 1
            if recorded:
                await conn.execute(
                    """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
                       VALUES ($1, 'AA-AG-03', 'evidence.fix', $2, $3, $4)""",
                    uuid.uuid4().hex, case_id, f"claims={recorded}（DA-T-05 只增）", trace)
        return json.dumps({"ok": True, "recorded": recorded})
    finally:
        await conn.close()


@mcp.tool()
async def apply_risk_bonus(case_id: str, points: int, basis: str) -> str:
    """API-M-13：关联网络命中加分（BA-BR-06，US-E4-02，tg_app 写角色）
    幂等：同案同 basis 仅生效一次（context_json.risk_bonus_<md5前8位> 打标），
    风险分封顶 100；加分与依据落审计（BA-BR-09）。"""
    import hashlib
    mark = "br06_" + hashlib.md5(basis.encode()).hexdigest()[:8]
    conn = await _conn()
    try:
        case = await conn.fetchrow(
            "SELECT risk_score, context_json, trace_id FROM risk_case WHERE case_id=$1", case_id)
        if not case:
            return json.dumps({"code": "E-NOT-FOUND", "message": "case 不存在"})
        ctx = json.loads(case["context_json"] or "{}")
        if ctx.get(mark):
            return json.dumps({"applied": False, "risk_score": case["risk_score"],
                               "reason": "同案同依据已加分（幂等）"})
        new_score = min(case["risk_score"] + points, 100)
        ctx[mark] = True
        async with conn.transaction():
            await conn.execute(
                """UPDATE risk_case SET risk_score=$2, context_json=$3::jsonb, updated_at=now()
                   WHERE case_id=$1""", case_id, new_score, json.dumps(ctx, ensure_ascii=False))
            await conn.execute(
                """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
                   VALUES ($1, 'AA-AG-03', 'risk.bonus', $2, $3, $4)""",
                uuid.uuid4().hex, case_id,
                f"+{points} -> {new_score}（{basis}）", case["trace_id"])
        return json.dumps({"applied": True, "risk_score": new_score})
    finally:
        await conn.close()


@mcp.tool()
async def record_case_signals(case_id: str, risk_score: int, signals: list[dict]) -> str:
    """API-M-10：聚合结果落库（AA-SK-01 步骤 6，US-E3-03）
    信号 insert DA-T-04（只增）+ risk_score 回写 DA-T-03；tg_app 写角色（DA-INV-05 权限矩阵）。
    signals 为数组（元素含 source/type/confidence/raw_ref/query_reason/degraded/velocity_json）；
    签名用 list[dict] 而非 JSON 字符串：FastMCP 会对形似 JSON 的字符串参数预解析，
    str 注解收到数组字符串会被转成 list 导致校验失败，故直收数组（兼容字符串入参）。"""
    sigs = signals
    conn = await _conn()
    try:
        async with conn.transaction():
            for s in sigs:
                await conn.execute(
                    """INSERT INTO risk_signal (signal_id, case_id, source, type, confidence,
                                                raw_ref, query_reason, degraded, velocity_json)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
                    s.get("signal_id") or uuid.uuid4().hex, case_id, s["source"], s["type"],
                    s["confidence"], s.get("raw_ref"), s["query_reason"],
                    s.get("degraded", False),
                    json.dumps(s["velocity_json"]) if s.get("velocity_json") else None)
            await conn.execute(
                "UPDATE risk_case SET risk_score=$2, updated_at=now() WHERE case_id=$1",
                case_id, risk_score)
            await conn.execute(
                """INSERT INTO audit_log (log_id, actor, action, target, basis)
                   VALUES ($1, 'AA-AG-02', 'signals.record', $2, $3)""",
                uuid.uuid4().hex, case_id, f"signals={len(sigs)},risk_score={risk_score}")
        return json.dumps({"ok": True, "recorded": len(sigs), "risk_score": risk_score})
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
