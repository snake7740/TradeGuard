# -*- coding: utf-8 -*-
"""AA-SK-02 欺诈调查确定性内核（E4 欺诈调查与关联分析，US-E4-01~03）

与 aggregation.py / disposition.py 同构的可测内核（06 §3 TDD 纪律）：
  run —— 假设匹配（规则兜底 + DA-KB-01 检索引用 doc_id，未命中显式声明）→
         图谱扩展（fn_related_graph 2 跳上限）→ BA-BR-06 黑名单加分（幂等）→
         影响面统计 → 证据固化（DA-T-05 只增）→ InvestigationCompleted 移交审批。

安全边界：只读调查，仅经状态机写路径推进状态；图扩展深度上限 2 跳（防组合爆炸）。
"""
from __future__ import annotations

import json
import uuid

from ..core.state_machine import CaseEvent, status_zh
from ..core.tracing import skill_span
from .knowledge import search_kb
from .mcp_adapters import remember

ACTOR_INV = "agent:AA-AG-03"      # 调查取证 Agent（02 §3）
ACTOR_INV_AUDIT = "AA-AG-03"
BR06_BONUS = 30                   # BA-BR-06：2 跳内命中已确认欺诈主体 +30
VELOCITY_1H_COUNT = 10            # 跑分假设阈值（与 BA-BR-14 同源）
LARGE_AMOUNT = 5000               # 盗卡假设阈值（单点大额，BA-BR-01 金额线同源）


class InvestigationStateError(Exception):
    """调查编排的非法状态进入（E-BAD-STATE）"""

    code = "E-BAD-STATE"


def match_hypothesis(signals: list[dict], graph_edge_types: set[str]) -> tuple[str, str]:
    """规则兜底假设匹配（AA-SK-02 步骤 1；KB 不可用时的确定性下限）

    返回 (pattern, rule_basis)；无命中返回 ("待定", "")，由人工复核定性（02 §3.3 人机边界）。
    """
    for s in signals:
        vj = s.get("velocity_json")
        if isinstance(vj, str):
            vj = json.loads(vj or "{}")
        v1 = (vj or {}).get("velocity_1h") or {}
        if v1.get("count", 0) >= VELOCITY_1H_COUNT and v1.get("amount", 0) < LARGE_AMOUNT:
            return "跑分", f"velocity_1h={v1['count']} 笔小额高频（BA-BR-14 特征）"
        if s.get("type") == "large_amount_burst":
            return "盗卡", "单卡突发大额（≥5000 元线，BA-BR-01 同源阈值）"
    if "SAME_DEVICE" in graph_edge_types:
        return "团伙盗刷", "同设备指纹关联多账户（图谱 SAME_DEVICE 边）"
    return "待定", ""


class InvestigationService:
    """调查编排服务：依赖注入 pool（tg_web 读+状态机）、cases（CaseRepository）、
    core（AA-MCP-01 CoreClient）、pub（事件发布端口）。"""

    def __init__(self, pool, cases, core, pub):
        self.pool = pool
        self.cases = cases
        self.core = core
        self.pub = pub

    async def run(self, case_id: str) -> dict:
        async with skill_span("AA-SK-02", "AA-AG-03", case_id):
            result = await self._run(case_id)
        await remember(self.core, case_id, "AA-AG-03", "investigation", {
            "pattern": result["hypothesis"]["pattern"], "impact": result["impact"],
            "case_status": result["case_status"]})
        return result

    async def _run(self, case_id: str) -> dict:
        case = await self.cases.get(case_id)
        if not case:
            raise LookupError(case_id)
        if case["status"] != "INVESTIGATING":
            raise InvestigationStateError(
                f"案件当前处于「{status_zh(case['status'])}」状态，不支持启动调查，请刷新页面查看最新进展")

        signals = await self.pool.fetch(
            "SELECT * FROM risk_signal WHERE case_id=$1 ORDER BY ts", case_id)
        sigs = [dict(r) for r in signals]

        # 1. 图谱扩展（2 跳上限，AA-SK-02 安全边界）
        edges = await self.pool.fetch(
            "SELECT * FROM fn_related_graph($1, 2)", case["subject_ref"])
        nodes = {case["subject_ref"]}
        edge_types: set[str] = set()
        for e in edges:
            nodes.update((e["src_node"].strip(), e["dst_node"].strip()))
            edge_types.add(e["edge_type"])
        nodes.discard("")

        # 2. 假设匹配（规则兜底）+ DA-KB-01 检索引用（SC-05 联动，doc_id 引用对齐）
        pattern, rule_basis = match_hypothesis(sigs, edge_types)
        citations, kb_note = [], ""
        if pattern != "待定":
            hits = await search_kb(self.pool, f"{pattern} {rule_basis} 手法特征")
            citations = [{"doc_id": h["doc_id"], "title": h["title"],
                          "similarity": round(h["similarity"], 3)} for h in hits]
            if not citations:
                kb_note = "无库内匹配"                    # 未命中显式声明（AA-SK-02 步骤 1）

        # 3. BA-BR-06：2 跳内命中已确认欺诈主体（黑名单）→ +30 分（幂等，API-M-13）
        black_hit = []
        if len(nodes) > 1:
            rows = await self.pool.fetch(
                "SELECT account_hash FROM account WHERE list_flag='black' "
                "AND account_hash = ANY($1::char(64)[])",
                [n.ljust(64) for n in nodes])
            black_hit = [r["account_hash"].strip() for r in rows]
        if black_hit:
            await self.core.apply_risk_bonus(
                case_id, BR06_BONUS, "BA-BR-06 关联网络命中黑名单主体")

        # 4. 影响面（图内账户数 + 近 24h 涉险金额，AA-SK-02 步骤 3）
        amount = await self.pool.fetchval(
            """SELECT COALESCE(SUM(amount), 0) FROM transaction
               WHERE account_hash = ANY($1::char(64)[])
                 AND ts >= now() - interval '24 hours'""",
            [n.ljust(64) for n in nodes])

        # 5. 证据固化（DA-T-05 只增，BA-BR-03，经 API-M-12 tg_app 写角色）
        await self.core.record_case_evidence(case_id, [{
            "claim": (f"调查结论：假设[{pattern}]；影响面 {len(nodes)} 账户，"
                      f"近24h涉险 {float(amount):.2f} 元；黑名单命中 {len(black_hit)} 主体"),
            "source_ref": "AA-AG-03:investigation", "confidence": 0.85}])
        await self.pool.execute(
            """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
               VALUES ($1, $2, 'investigation.complete', $3, $4, $5)""",
            uuid.uuid4().hex, ACTOR_INV_AUDIT, case_id,
            f"hypothesis={pattern},citations={len(citations)},impact_accounts={len(nodes)}"
            f"（AA-SK-02，US-E4-01/02/03）", case["trace_id"])

        # 6. 移交：InvestigationCompleted → PENDING_APPROVAL（定性仍须人工审批，02 §3.3）
        out = await self.cases.transition(
            case_id, CaseEvent.INVESTIGATION_COMPLETED, ACTOR_INV, case["version"],
            basis=f"hypothesis={pattern} 转审批（BA-BP-03）")

        return {"case_id": case_id, "case_status": out["status"],
                "hypothesis": {"pattern": pattern, "rule_basis": rule_basis,
                               "citations": citations, "kb_note": kb_note},
                "graph": {"nodes": len(nodes), "edges": len(edges),
                          "edge_types": sorted(edge_types), "black_hits": black_hit},
                "impact": {"accounts": len(nodes), "amount_24h": float(amount)},
                "evidence_fixed": True}
