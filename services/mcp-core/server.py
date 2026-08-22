"""AA-MCP-01 交易风控业务库 MCP Server（02 §5，streamable-http :8101）
工具全集对齐 07 §5.2 API-M-01~06；只读工具真实查 PolarDB-PG，
图查询走 UnifiedModel 退化路径 fn_related_graph（03 §3）。
处置执行工具 execute_disposition 含审批门控（DA-INV-02/03，E-DISP-AUTH）。
"""
import asyncio
import json
import os
import uuid
from typing import Any

import asyncpg
from mcp.server.fastmcp import FastMCP

PG_DSN = os.getenv("PG_DSN", "postgresql://tg_app:tg_app_dev@localhost:5432/tradeguard")

mcp = FastMCP("tradeguard-core", host="0.0.0.0", port=int(os.getenv("MCP_PORT", "8101")))  # nosec B104 —— 容器内必须绑全部接口，宿主暴露面由 compose 端口映射控制

HIGH_RISK_SCORE = 70   # BA-BR-01 高风险线缺省值；正式值读 sys_config br-01-auto-block-score（SC-06 双端同源）
MID_REVIEW_SCORE = 40  # BA-BR-01 中风险线缺省值；正式值读 sys_config br-01-mid-review-score

# 逆动作对（与 web-api verification.INVERSE_ACTION 一致）：人批准了冻结，即包含对
# 冻结出错的纠正（解冻）授权——回滚是审批生命周期的一部分，不是新处置（02 §7 人机边界）
INVERSE_ACTION = {"freeze": "release", "block": "release",
                  "reduce": "release", "release": "block"}

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


class _PooledConnection:
    """池化连接包装：close() 归还连接而非物理断开，工具代码保持 _conn/close 模式不变"""

    def __init__(self, pool: asyncpg.Pool, conn) -> None:
        self._pool = pool
        self._inner = conn

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def close(self) -> None:
        await self._pool.release(self._inner)


async def _conn():
    """从 lazy 初始化的 asyncpg 连接池取连接（双重检查 + 锁，避免并发重复建池）"""
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                _pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=8)
    return _PooledConnection(_pool, await _pool.acquire())


async def _cfg_int(conn, key: str, default: int) -> int:
    """从 sys_config 读整型阈值（Nacos 镜像降级源，SC-06 双端同源）；缺键/非法回退常量"""
    try:
        val = await conn.fetchval("SELECT value FROM sys_config WHERE key=$1", key)
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default


async def _approval_valid(conn, approval_ref: str, case_id: str, action: str) -> bool:
    """C1 凭证验真：已批准 + 同案 + 动作匹配（含逆动作对——批准原动作即含纠正授权）

    R-37 失败关闭：requested_action 为 NULL 的批准单不再视为"通配凭证"——
    历史 NULL 行只能说明建单时未携带动作，不能推定其授权了任意动作。"""
    rec = await conn.fetchrow(
        "SELECT case_id, decision, requested_action FROM approval_record WHERE approval_id=$1",
        approval_ref)
    if not rec or rec["decision"] != "approved" or rec["case_id"] != case_id:
        return False
    req = rec["requested_action"]
    if req is None:
        return False
    if req == action:
        return True
    return INVERSE_ACTION.get(req) == action


def _allowed_requested_actions(action: str) -> list[str]:
    """可作为凭证的 requested_action 集合：动作本身 + 逆动作对的源动作"""
    return sorted({action} | {k for k, v in INVERSE_ACTION.items() if v == action})


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


def _topology_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """B1 拓扑统计（BA-BR-16 / DA-INV-07，docs/14 US-E9）：星型密度/三角形环/
    SAME_DEVICE 二部集中度/嫌疑分。与 web-api investigation.topology_stats 同源算法；
    仅作调查线索，不得驱动处置裁决（DA-INV-07）。"""
    if not rows:
        return {"nodes": 1, "edges": 0, "star_density": 0.0, "cycle_count": 0,
                "bipartite_concentration": 0.0, "suspicion": 0.0, "degraded": False}
    adj: dict[str, set[str]] = {}
    deg: dict[str, int] = {}
    for e in rows:
        s, d = str(e.get("src_node", "")).strip(), str(e.get("dst_node", "")).strip()
        if not s or not d:
            continue
        adj.setdefault(s, set()).add(d)
        adj.setdefault(d, set())
        deg[s] = deg.get(s, 0) + 1
        deg[d] = deg.get(d, 0) + 1
    n = len(adj) or 1
    m = sum(len(v) for v in adj.values())
    star_density = round((max(deg.values()) / m) if m else 0.0, 3)
    cycle_count = 0
    seen: set[tuple[str, ...]] = set()
    for a, outs in adj.items():
        for b in outs:
            for c in adj.get(b, ()):
                if (c in adj.get(a, ()) or a in adj.get(c, ())) \
                        and c != a and c != b:
                    key = tuple(sorted((a, b, c)))
                    if key not in seen:
                        seen.add(key)
                        cycle_count += 1
    bip = 0.0
    dv: dict[str, int] = {}
    for e in rows:
        if e.get("edge_type") == "SAME_DEVICE":
            k = str(e.get("dst_node", "")).strip()
            dv[k] = dv.get(k, 0) + 1
    if dv:
        bip = round(max(dv.values()) / max(1, len(dv)), 3)
    suspicion = round(min(1.0, 0.4 * star_density + 0.3 * min(cycle_count, 3) / 3
                          + 0.3 * bip), 3)
    return {"nodes": n, "edges": m, "star_density": star_density,
            "cycle_count": cycle_count, "bipartite_concentration": bip,
            "suspicion": suspicion, "degraded": False}


@mcp.tool()
async def query_related_graph(account_hash: str, hops: int = 2) -> str:
    """API-M-02：关联图谱查询（UnifiedModel 退化路径，2 跳上限 AA-SK-02 安全边界）。
    返回 edges + topology_stats（B1 拓扑线索，仅研判不裁决，DA-INV-07，US-E9）；
    拓扑计算异常降级空统计（degraded=true），图查询本体不阻断。"""
    hops = min(max(hops, 1), 2)
    conn = await _conn()
    try:
        rows = await conn.fetch("SELECT * FROM fn_related_graph($1, $2)", account_hash, hops)
        edges = [dict(r) for r in rows]
        try:
            stats = await asyncio.wait_for(
                asyncio.to_thread(_topology_stats, edges), timeout=2.0)
        except Exception:  # noqa: BLE001 —— 拓扑统计降级空统计，图查询不阻断
            stats = {"nodes": 0, "edges": 0, "star_density": 0.0, "cycle_count": 0,
                     "bipartite_concentration": 0.0, "suspicion": 0.0, "degraded": True}
        return json.dumps({"edges": edges, "topology_stats": stats},
                          ensure_ascii=False, default=str)
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
        if not r:
            return json.dumps({"code": "E-NOT-FOUND", "message": f"处置凭证 {exec_id} 不存在"},
                              ensure_ascii=False)
        return json.dumps(dict(r), ensure_ascii=False, default=str)
    finally:
        await conn.close()


@mcp.tool()
async def execute_disposition(case_id: str, action: str, amount: float | None,
                              idempotency_key: str, approval_ref: str | None = None) -> str:
    """API-M-03：处置执行（审批门控 DA-INV-02 + 幂等 DA-INV-03）"""
    if action not in ("block", "freeze", "reduce", "release"):
        return json.dumps({"code": "E-BAD-ACTION", "message": f"非法处置动作 {action}"})
    # R-37：金额入口校验——允许 None（非 reduce 动作不携金额），
    # 有值必须为 [0, 1e7] 内有限数（拒绝负数/NaN/Inf/超额，防脏值落 disposition_record）
    if amount is not None:
        try:
            amt = float(amount)
        except (TypeError, ValueError):
            return json.dumps({"code": "E-BAD-AMOUNT", "message": f"金额不可解析为数值：{amount!r}"})
        if amt != amt or amt in (float("inf"), float("-inf")) or not (0 <= amt <= 10_000_000):
            return json.dumps({"code": "E-BAD-AMOUNT",
                               "message": f"金额超出 [0, 1e7] 有效区间：{amount!r}"})
    # R-37 复审收口：标识符长度闸门（对齐 disposition_record 列宽）——超长值先以
    # 干净错误信封拒绝，防审计 basis / approval_ref varchar(40) / 幂等键 varchar(60)
    # 截断把 E-DISP-AUTH 退化为传输层错误（处置仍绝不执行，失败关闭不变）
    if idempotency_key and len(idempotency_key) > 60:
        return json.dumps({"code": "E-BAD-KEY",
                           "message": "幂等键超过 60 字符（idempotency_key 列宽上限）"})
    if approval_ref and len(approval_ref) > 40:
        return json.dumps({"code": "E-DISP-AUTH",
                           "message": "审批凭证形态非法（approval_ref 超 40 字符）"})
    conn = await _conn()
    try:
        case = await conn.fetchrow(
            "SELECT risk_score, trace_id, status FROM risk_case WHERE case_id=$1", case_id)
        if not case:
            return json.dumps({"code": "E-NOT-FOUND", "message": "case 不存在"})
        score = case["risk_score"]
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
        # 阈值双端同源（D2）：sys_config 为 Nacos 镜像降级源，与 web 侧 ConfigService 同键
        high_line = await _cfg_int(conn, "br-01-auto-block-score", HIGH_RISK_SCORE)
        mid_line = await _cfg_int(conn, "br-01-mid-review-score", MID_REVIEW_SCORE)
        # 审批门控（C1/C2 加固，SC-02；与 web disposition 双层守护）
        if approval_ref:
            # C1：凭证严格验真——伪造/张冠李戴/未批准/动作不匹配一律拒绝
            if not await _approval_valid(conn, approval_ref, case_id, action):
                await conn.execute(
                    """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
                       VALUES ($1, 'AA-AG-04', 'disposition.refused_auth', $2, $3, $4)""",
                    uuid.uuid4().hex, case_id,
                    f"approval_ref={approval_ref} action={action} 凭证验真未通过"
                    f"（不存在/未批准/案件或动作不匹配，C1）", case["trace_id"])
                return json.dumps({"code": "E-DISP-AUTH",
                                   "message": "审批凭证验真未通过（C1：须已批准、同案且动作/逆对匹配）"})
        elif score >= high_line:
            # C2：高风险无凭证——唯一豁免是 ROLLBACK 态的反向 release（核验回滚上下文）
            if not (action == "release" and case["status"] == "ROLLBACK"):
                # 兜底：按同一谓词查已批准凭证（本动作优先、逆对次之、decided_at DESC）
                # R-37：兜底查询不再接纳 requested_action IS NULL 的历史行（失败关闭，
                # 与 _approval_valid 同源）——NULL 行无法证明授权了当前动作
                approved = await conn.fetchrow(
                    """SELECT approval_id FROM approval_record
                       WHERE case_id=$1 AND decision='approved'
                         AND requested_action = ANY($2)
                       ORDER BY (requested_action = $3) DESC,
                                decided_at DESC NULLS LAST LIMIT 1""",
                    case_id, _allowed_requested_actions(action), action)
                if not approved:
                    await conn.execute(
                        """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
                           VALUES ($1, 'AA-AG-04', 'disposition.refused_auth', $2, $3, $4)""",
                        uuid.uuid4().hex, case_id,
                        f"risk_score={score} action={action} 高风险缺审批凭证拒绝（BA-BR-02，SC-02）",
                        case["trace_id"])
                    return json.dumps({"code": "E-DISP-AUTH",
                                       "message": "高风险处置缺审批凭证，已拒绝并转审批（BA-BR-02）"})
                approval_ref = approved["approval_id"]
        elif score >= mid_line:
            # C2：中风险段 40-69 无凭证一律拒（含 release，与 web 层守卫逐字对齐：
            # BA-BR-01「一律转人工复核，不得自动处置」）——堵纵深缺口：
            # AgentTeams worker 经 mcporter 直调本工具不得绕过 web 层放行中风险 release；
            # 核验回滚的反向 release 携凭证走 C1 逆动作对豁免，不受影响
            await conn.execute(
                """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
                   VALUES ($1, 'AA-AG-04', 'disposition.refused_scope', $2, $3, $4)""",
                uuid.uuid4().hex, case_id,
                f"risk_score={score} action={action} 中风险禁止任何无凭证自动处置（BA-BR-01 分段，C2）",
                case["trace_id"])
            return json.dumps({"code": "E-DISP-SCOPE",
                               "message": "中风险处置须经人工复核/审批通道（BA-BR-01 分段，SC-10）"})
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
        exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM risk_case WHERE case_id=$1)", case_id)
        if not exists:
            return json.dumps({"code": "E-NOT-FOUND", "message": f"case {case_id} 不存在"},
                              ensure_ascii=False)
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
    # R-37：只允许正向加分（1~100）——负数/零加分实为降分后门，
    # 可被用于静默压低风险分绕过处置门控，一律拒绝
    if not isinstance(points, int) or not (0 < points <= 100):
        return json.dumps({"code": "E-BAD-POINTS",
                           "message": f"加分值必须为 1~100 的整数（拒绝降分/越界）：{points!r}"})
    import hashlib
    mark = "br06_" + hashlib.md5(basis.encode(),
                              usedforsecurity=False).hexdigest()[:8]  # 幂等键非安全哈希
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
                # R-37 复审收口：审计展示截断对齐 varchar(300)；幂等 mark 仍按完整
                # basis 计算（md5 不受截断影响），加分语义不变
                f"+{points} -> {new_score}（{basis[:250]}）", case["trace_id"])
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
    # R-37：入口校验——risk_score 必须落在 [0,100]（越界值会污染处置门控分段），
    # 信号元素必须携带契约字段（缺字段此前在事务内抛 KeyError 造成整批回滚且报错含糊）
    try:
        score_val = int(risk_score)
    except (TypeError, ValueError):
        return json.dumps({"code": "E-BAD-SCORE",
                           "message": f"risk_score 不可解析为整数：{risk_score!r}"})
    if not (0 <= score_val <= 100):
        return json.dumps({"code": "E-BAD-SCORE",
                           "message": f"risk_score 超出 [0,100]：{risk_score!r}"})
    if not isinstance(signals, list):
        return json.dumps({"code": "E-BAD-SIGNALS", "message": "signals 必须为数组"})
    required = ("source", "type", "confidence", "query_reason")
    for i, s in enumerate(signals):
        if not isinstance(s, dict):
            return json.dumps({"code": "E-BAD-SIGNALS",
                               "message": f"signals[{i}] 必须为对象，实际为 {type(s).__name__}"})
        missing = [k for k in required if k not in s]
        if missing:
            return json.dumps({"code": "E-BAD-SIGNALS",
                               "message": f"signals[{i}] 缺少契约字段：{','.join(missing)}"})
    sigs = signals
    conn = await _conn()
    try:
        async with conn.transaction():
            for s in sigs:
                await conn.execute(
                    """INSERT INTO risk_signal (signal_id, case_id, source, type, confidence,
                                                raw_ref, query_reason, degraded, velocity_json)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                       ON CONFLICT (signal_id) DO NOTHING""",  # 阶段2 R-41：幂等防重
                    s.get("signal_id") or uuid.uuid4().hex, case_id, s["source"], s["type"],
                    s["confidence"], s.get("raw_ref"), s["query_reason"],
                    s.get("degraded", False),
                    json.dumps(s["velocity_json"]) if s.get("velocity_json") else None)
            await conn.execute(
                "UPDATE risk_case SET risk_score=$2, updated_at=now() WHERE case_id=$1",
                case_id, score_val)
            await conn.execute(
                """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
                   VALUES ($1, 'AA-AG-02', 'signals.record', $2, $3, $4)""",
                uuid.uuid4().hex, case_id, f"signals={len(sigs)},risk_score={score_val}",
                await conn.fetchval(
                    "SELECT trace_id FROM risk_case WHERE case_id=$1", case_id))
        return json.dumps({"ok": True, "recorded": len(sigs), "risk_score": score_val})
    finally:
        await conn.close()


@mcp.tool()
async def submit_kb_application(case_id: str, category: str, title: str, content: str) -> str:
    """API-M-05：知识入库申请（AA-SK-05；仅写 pending 申请单，发布由人类经 API-W-12 确认，DA-INV-06）。
    E2 规则进化（US-E11）：rule_proposal 类目以 pending 申请单进入，置 published 须
    经 human:* 人审（DA-INV-08 DB 触发器守护，BA-BR-21），直接发布一律拒绝。"""
    if category not in ("case", "regulation", "runbook", "rule_proposal"):
        return json.dumps({"code": "E-BAD-CATEGORY", "message": f"非法类目 {category}"})
    doc_id = uuid.uuid4().hex
    conn = await _conn()
    try:
        await conn.execute(
            """INSERT INTO kb_document
                   (doc_id, category, title, content, status, applicant, source_case_id)
               VALUES ($1, $2, $3, $4, 'pending', 'AA-AG-05', $5)""",
            doc_id, category, title, content, case_id)
        await conn.execute(
            """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
               VALUES ($1, 'AA-AG-05', 'kb.apply', $2, $3, $4)""",
            uuid.uuid4().hex, doc_id, f"case={case_id},category={category}",
            await conn.fetchval(
                "SELECT trace_id FROM risk_case WHERE case_id=$1", case_id))
        return json.dumps({"doc_id": doc_id, "status": "pending"}, ensure_ascii=False)
    finally:
        await conn.close()


@mcp.tool()
async def record_agent_memory(case_id: str, agent_id: str, stage: str, summary: dict) -> str:
    """API-M-14：Agent 执行摘要落 DA-T-12（agent_memory 仅 tg_app 可 INSERT，02-roles.sql）
    表无 stage 列，stage 合入 summary JSON 文本；每次技能执行完成写一行。"""
    memory_id = uuid.uuid4().hex
    conn = await _conn()
    try:
        await conn.execute(
            """INSERT INTO agent_memory (memory_id, agent_id, case_id, summary)
               VALUES ($1, $2, $3, $4)""",
            memory_id, agent_id[:16], case_id,
            json.dumps({"stage": stage} | summary, ensure_ascii=False, default=str))
        return json.dumps({"memory_id": memory_id, "ok": True})
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
