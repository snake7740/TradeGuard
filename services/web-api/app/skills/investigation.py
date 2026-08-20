"""AA-SK-02 欺诈调查确定性内核（E4 欺诈调查与关联分析，US-E4-01~03）

与 aggregation.py / disposition.py 同构的可测内核（06 §3 TDD 纪律）：
  run —— 假设匹配（规则兜底 + DA-KB-01 检索引用 doc_id，未命中显式声明）→
         图谱扩展（fn_related_graph 2 跳上限）→ BA-BR-06 黑名单加分（幂等）→
         影响面统计 → 证据固化（DA-T-05 只增）→ InvestigationCompleted 移交审批。

安全边界：只读调查，仅经状态机写路径推进状态；图扩展深度上限 2 跳（防组合爆炸）。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from ..core.state_machine import CaseEvent, status_zh
from ..core.tracing import skill_span
from . import planner as planner_mod
from .knowledge import mark_kb_feedback, search_kb
from .mcp_adapters import remember

ACTOR_INV = "agent:AA-AG-03"  # 调查取证 Agent（02 §3）
ACTOR_INV_AUDIT = "AA-AG-03"
BR06_BONUS = (
    30  # BA-BR-06：2 跳内命中已确认欺诈主体 +30（缺省值，热键 br-06-fraud-link-bonus）
)
VELOCITY_1H_COUNT = 10  # 跑分假设阈值（与 BA-BR-14 同源）
LARGE_AMOUNT = 5000  # 盗卡假设阈值（单点大额，BA-BR-01 金额线同源）
TOPO_TIMEOUT = 2.0  # 拓扑统计进程内计算超时上限（超时返回空统计，调查不阻断，14 §1.4）


# ---------- B1 图拓扑统计（BA-BR-16 / DA-INV-07，docs/14 US-E9；与 mcp-core 同源算法） ----------

def topology_stats(edges: list[dict[str, Any]], root: str) -> dict[str, Any]:
    """星型/环型密度 + 二部集中度 + 嫌疑分（进程内纯函数，子图 ≤ 百节点）。

    仅调查线索：输出不进入评分与状态迁移入参（DA-INV-07，BA-BR-16），
    只随证据链与 graph 摘要留痕供人工研判。
    """
    if not edges:
        return {"nodes": 1, "edges": 0, "star_density": 0.0, "cycle_count": 0,
                "bipartite_concentration": 0.0, "suspicion": 0.0, "degraded": False}
    adj: dict[str, set[str]] = {}
    deg: dict[str, int] = {}
    for e in edges:
        s, d = str(e.get("src_node", "")).strip(), str(e.get("dst_node", "")).strip()
        if not s or not d:
            continue
        adj.setdefault(s, set()).add(d)
        adj.setdefault(d, set())
        deg[s] = deg.get(s, 0) + 1
        deg[d] = deg.get(d, 0) + 1
    nodes = set(adj)
    n = len(nodes) or 1
    m = sum(len(v) for v in adj.values())
    star_density = round((max(deg.values()) / m) if m else 0.0, 3)  # 枢纽集中度
    # 环型计数：三角形（A→B→C→A，资金回路同构特征；无向三角同构 a→b→c 且 a-c
    # 有边亦计入；sorted key 去重）
    cycle_count = 0
    seen: set[tuple[str, ...]] = set()
    for a, outs in adj.items():
        for b in outs:
            for c in adj.get(b, ()):  # noqa: SIM110 —— 三角形枚举，规模 ≤ 百节点可控
                if (c in adj.get(a, ()) or a in adj.get(c, ())) \
                        and c != a and c != b:
                    key = tuple(sorted((a, b, c)))
                    if key not in seen:
                        seen.add(key)
                        cycle_count += 1
    # SAME_DEVICE 二部集中度：账户侧最大同设备关联数 / 设备侧最大关联账户数
    bip = 0.0
    dv: dict[str, int] = {}
    for e in edges:
        if e.get("edge_type") == "SAME_DEVICE":
            dv[str(e.get("dst_node", "")).strip()] = (
                dv.get(str(e.get("dst_node", "")).strip(), 0) + 1)
    if dv:
        bip = round(max(dv.values()) / max(1, len(dv)), 3)
    suspicion = round(min(1.0, 0.4 * star_density + 0.3 * min(cycle_count, 3) / 3
                          + 0.3 * bip), 3)
    return {"nodes": n, "edges": m, "star_density": star_density,
            "cycle_count": cycle_count, "bipartite_concentration": bip,
            "suspicion": suspicion, "degraded": False}


async def compute_topology(edges: list[dict[str, Any]], root: str) -> dict[str, Any]:
    """带超时的拓扑统计：计算超时返回空统计（degraded=True），调查链路不阻断"""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(topology_stats, edges, root), timeout=TOPO_TIMEOUT)
    except Exception:  # noqa: BLE001 —— 超时/异常均降级空统计（含 asyncio.TimeoutError）
        return {"nodes": 0, "edges": 0, "star_density": 0.0, "cycle_count": 0,
                "bipartite_concentration": 0.0, "suspicion": 0.0, "degraded": True}


class InvestigationStateError(Exception):
    """调查编排的非法状态进入（E-BAD-STATE）"""

    code = "E-BAD-STATE"


def match_hypothesis(
    signals: list[dict], graph_edge_types: set[str]
) -> tuple[str, str]:
    """规则兜底假设匹配（AA-SK-02 步骤 1；KB 不可用时的确定性下限）

    返回 (pattern, rule_basis)；无命中返回 ("待定", "")，由人工复核定性（02 §3.3 人机边界）。
    """
    for s in signals:
        vj = s.get("velocity_json")
        if isinstance(vj, str):
            vj = json.loads(vj or "{}")
        v1 = (vj or {}).get("velocity_1h") or {}
        if (
            v1.get("count", 0) >= VELOCITY_1H_COUNT
            and v1.get("amount", 0) < LARGE_AMOUNT
        ):
            return "跑分", f"velocity_1h={v1['count']} 笔小额高频（BA-BR-14 特征）"
        if s.get("type") == "large_amount_burst":
            return "盗卡", "单卡突发大额（≥5000 元线，BA-BR-01 同源阈值）"
    if "SAME_DEVICE" in graph_edge_types:
        return "团伙盗刷", "同设备指纹关联多账户（图谱 SAME_DEVICE 边）"
    return "待定", ""


class InvestigationService:
    """调查编排服务：依赖注入 pool（tg_web 读+状态机）、cases（CaseRepository）、
    core（AA-MCP-01 CoreClient）、pub（事件发布端口）、config（SC-06 热值，可选）。"""

    def __init__(self, pool, cases, core, pub, config=None, ranker=None,
                 external=None, llm_client=None):
        self.pool = pool
        self.cases = cases
        self.core = core
        self.pub = pub
        self.config = config
        self.external = external
        self.llm_client = llm_client  # R-47 规划-反思用（None → 内部惰性 LlmClient，无 Key 降级规则）
        if ranker is None:  # 阶段1 接线（R-40）：规则 baseline，可注入 LLM 版
            from app.core.llm_adapters import RuleHypothesisRanker

            ranker = RuleHypothesisRanker()
        self.ranker = ranker

    def _cfg_int(self, key: str, default: int) -> int:
        """SC-06 热值读取（与 aggregation/disposition 同构）：缺键/无 config 回落缺省常量"""
        if self.config is None:
            return default
        try:
            return int(self.config.values[key])
        except (KeyError, TypeError, ValueError):
            return default

    async def run(self, case_id: str) -> dict:
        async with skill_span("AA-SK-02", "AA-AG-03", case_id):
            result = await self._run(case_id)
        await remember(
            self.core,
            case_id,
            "AA-AG-03",
            "investigation",
            {
                "pattern": result["hypothesis"]["pattern"],
                "impact": result["impact"],
                "case_status": result["case_status"],
            },
        )
        return result

    async def _run(self, case_id: str) -> dict:
        case = await self.cases.get(case_id)
        if not case:
            raise LookupError(case_id)
        if case["status"] != "INVESTIGATING":
            raise InvestigationStateError(
                f"案件当前处于「{status_zh(case['status'])}」状态，不支持启动调查，请刷新页面查看最新进展"
            )

        signals = await self.pool.fetch(
            "SELECT * FROM risk_signal WHERE case_id=$1 ORDER BY ts", case_id
        )
        sigs = [dict(r) for r in signals]

        # 1. 图谱扩展（2 跳上限，AA-SK-02 安全边界）
        edges = await self.pool.fetch(
            "SELECT * FROM fn_related_graph($1, 2)", case["subject_ref"]
        )
        nodes = {case["subject_ref"]}
        edge_types: set[str] = set()
        for e in edges:
            nodes.update((e["src_node"].strip(), e["dst_node"].strip()))
            edge_types.add(e["edge_type"])
        nodes.discard("")

        # 1a. B1 拓扑统计（US-E9）：进程内计算，超时降级空统计不阻断；
        #     输出仅作调查线索留痕，不进入评分/状态迁移入参（DA-INV-07）
        topo = await compute_topology(
            [dict(e) for e in edges], case["subject_ref"])

        # 1b. 团伙发现（阶段3 R-43）：WCC 弱连通分量，ring_size = 团伙规模（补充信息，
        #     不替代 2 跳邻域，只作影响面团伙边界）
        ring_rows = await self.pool.fetch(
            "SELECT * FROM fn_fraud_ring($1)", case["subject_ref"]
        )
        ring_nodes = {r["node"].strip() for r in ring_rows}
        ring_nodes.discard("")
        ring_size = len(ring_nodes) if ring_nodes else 1

        # 0. AG-01 规划（R-47）：依据信号/图谱/先验生成调查计划并选择性深查外部源
        #    （与 AG-02 全量三源互补：豁免源留痕可审计；LLM 不可用降级规则版）
        #    规划前 KB 预检：以信号特征词先检索一次，命中摘要喂 kb_hints——规划与
        #    假设排序提示词的「知识库提示」输入真实接线（此前恒传空串）；
        #    未命中/失败传空串，行为与既有基线一致（不阻断）
        sig_types = sorted({s.get("type", "") for s in sigs if s.get("type")})
        kb_hints = ""
        if sig_types:
            try:
                pre_hits = await search_kb(
                    self.pool, f"{' '.join(sig_types)} 手法特征")
                kb_hints = "; ".join(
                    f"{h['title'][:40]}({h['similarity']:.2f})"
                    for h in pre_hits[:3])
            except Exception:  # noqa: BLE001 —— KB 预检失败不阻断规划（空提示）
                kb_hints = ""
        plan = await planner_mod.make_plan(
            sigs, edge_types, kb_hints=kb_hints, client=self.llm_client)
        findings = await planner_mod.execute_plan(
            plan, self.external, case["subject_ref"])
        # B3 假设覆盖核算（BA-BR-18）：未深查假设留痕「为什么没查 X」，并入豁免留痕
        hyp_skipped = planner_mod.hypothesis_skipped(plan, findings)
        # ≥2 假设 → 并行分支已生成，发 E-INV-HYPOTHESIS 领域事件（docs/14 §2，
        # OpenAPI SseEvent 枚举逐字同步）
        if len(plan.hypotheses) >= 2:
            await self.pub.publish(
                case_id,
                "E-INV-HYPOTHESIS",
                {
                    "hypotheses": [h.get("pattern", "待定") for h in plan.hypotheses],
                    "parallel": True,
                    "plan_source": plan.source,
                },
                ACTOR_INV,
                case["trace_id"],
            )

        # 2. 假设匹配（规则兜底）+ DA-KB-01 检索引用（SC-05 联动，doc_id 引用对齐）
        #    R-48 记忆反哺：规则无法定性（待定）时以信号特征词检索 KB，命中则以
        #    库内手法文档升级定性——知识沉淀→调查定性效率闭环（KPI-06 量化载体）；
        #    未命中仍显式声明交人工（人机边界不变，02 §3.3）
        _hyp = await self.ranker.rank(sigs, edge_types, kb_hints=kb_hints)
        pattern, rule_basis = _hyp["pattern"], _hyp["basis"]
        # 检索词优先取 AG-01 规划产出（LLM/规则间构，空时回退固定拼接词）
        if pattern != "待定":
            kb_terms = plan.kb_queries or [f"{pattern} {rule_basis} 手法特征"]
        else:
            kb_terms = [f"{' '.join(sig_types)} 手法特征", *plan.kb_queries]
        hits = []  # search_kb 返回 list[dict]（首个命中词即停）
        for term in kb_terms[:2]:
            hits = await search_kb(self.pool, term)
            if hits:
                break
        citations = [
            {
                "doc_id": h["doc_id"],
                "title": h["title"],
                "similarity": round(h["similarity"], 3),
            }
            for h in hits
        ]
        kb_note = ""
        if not citations:
            kb_note = "无库内匹配"  # 未命中显式声明（AA-SK-02 步骤 1）
        elif pattern == "待定":
            kb_note = (
                f"KB 反哺定性：规则假设待定，依据库内文档"
                f"「{hits[0]['title'][:50]}」升级定性（R-48）"
            )
            pattern = hits[0]["title"][:50]  # 文档标题即定性描述（审计可回放）
            # E1 命中正确性反哺（US-E11）：KB 引用成功升级定性 → hit_correct 累积
            await mark_kb_feedback(self.pool, [h["doc_id"] for h in hits])

        # 3. BA-BR-06：2 跳内命中已确认欺诈主体（黑名单）→ 加分（幂等，API-M-13）
        #    加分值走热键 br-06-fraud-link-bonus（SC-06，docs/01 §5 配置位置=Nacos 动态配置）
        black_hit = []
        if len(nodes) > 1:
            rows = await self.pool.fetch(
                "SELECT account_hash FROM account WHERE list_flag='black' "
                "AND account_hash = ANY($1::char(64)[])",
                [n.ljust(64) for n in nodes],
            )
            black_hit = [r["account_hash"].strip() for r in rows]
        if black_hit:
            bonus = self._cfg_int("br-06-fraud-link-bonus", BR06_BONUS)
            # basis 串是 API-M-13 幂等标记的 md5 源（mcp-core br06_<md5前8> 打标），
            # 必须逐字稳定，不得随加分值/措辞漂移（测试以同串复投验证不叠加）
            await self.core.apply_risk_bonus(
                case_id, bonus, "BA-BR-06 关联网络命中黑名单主体"
            )

        # 4. 影响面（图内账户数 + 近 24h 涉险金额，AA-SK-02 步骤 3）
        amount = await self.pool.fetchval(
            """SELECT COALESCE(SUM(amount), 0) FROM transaction
               WHERE account_hash = ANY($1::char(64)[])
                 AND ts >= now() - interval '24 hours'""",
            [n.ljust(64) for n in nodes],
        )

        # 4b. AG-01 反思（R-47）：对比计划/执行/结论，缺口落证据链（可回放闭环）
        reflection = await planner_mod.reflect(
            plan, findings, pattern, client=self.llm_client)

        # 5. 证据固化（DA-T-05 只增，BA-BR-03，经 API-M-12 tg_app 写角色）
        plan_summary = (
            f"AG-01 计划[{plan.source}]：查询源 "
            f"{','.join(q.source for q in plan.queries)}（并行分支），豁免 "
            f"{','.join(s['source'] for s in plan.skipped) or '无'}"
            f"{('；假设未深查留痕：' + '；'.join(s['hypothesis'] for s in hyp_skipped)) if hyp_skipped else ''}"
            f"；执行成功 "
            f"{sum(1 for x in findings if x.get('ok'))}/{len(findings)}"
        )
        await self.core.record_case_evidence(
            case_id,
            [
                {
                    "claim": (
                        f"调查结论：假设[{pattern}]；影响面 {len(nodes)} 账户"
                        f"（团伙连通分量 {ring_size} 账户），"
                        f"近24h涉险 {float(amount):.2f} 元；黑名单命中 {len(black_hit)} 主体"
                    ),
                    "source_ref": "AA-AG-03:investigation",
                    "confidence": 0.85,
                },
                {
                    "claim": (
                        f"拓扑线索（仅研判不裁决，DA-INV-07）：星型密度 "
                        f"{topo['star_density']}，三角形环 {topo['cycle_count']} 个，"
                        f"同设备二部集中度 {topo['bipartite_concentration']}，"
                        f"嫌疑分 {topo['suspicion']}"
                        + ("（计算超时降级空统计）" if topo["degraded"] else "")
                    ),
                    "source_ref": "AA-AG-03:topology",
                    "confidence": 0.6,
                },
                {
                    "claim": (
                        f"{plan_summary}；反思[{reflection.source}]{reflection.verdict}："
                        f"{reflection.summary}"
                        + (f"；缺口：{'；'.join(reflection.gaps)}" if reflection.gaps else "")
                    ),
                    "source_ref": "AA-AG-01:plan-reflect",
                    "confidence": 0.7,
                },
            ],
        )
        await self.pool.execute(
            """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
               VALUES ($1, $2, 'investigation.complete', $3, $4, $5)""",
            uuid.uuid4().hex,
            ACTOR_INV_AUDIT,
            case_id,
            f"hypothesis={pattern},citations={len(citations)},impact_accounts={len(nodes)}"
            f",plan={plan.source},reflect={reflection.verdict}"
            f"（AA-SK-02，US-E4-01/02/03，R-47）",
            case["trace_id"],
        )

        # 6. 移交：InvestigationCompleted → PENDING_APPROVAL（定性仍须人工审批，02 §3.3）
        out = await self.cases.transition(
            case_id,
            CaseEvent.INVESTIGATION_COMPLETED,
            ACTOR_INV,
            case["version"],
            basis=f"hypothesis={pattern} 转审批（BA-BP-03）",
        )

        return {
            "case_id": case_id,
            "case_status": out["status"],
            "hypothesis": {
                "pattern": pattern,
                "rule_basis": rule_basis,
                "citations": citations,
                "kb_note": kb_note,
            },
            "plan": {
                "source": plan.source,
                "hypotheses": plan.hypotheses,
                "queries": [
                    {"source": q.source, "reason": q.reason, "priority": q.priority}
                    for q in plan.queries
                ],
                "skipped": [*plan.skipped, *hyp_skipped],
                "rationale": plan.rationale,
                "findings": findings,
                "reflection": {
                    "source": reflection.source,
                    "verdict": reflection.verdict,
                    "gaps": reflection.gaps,
                    "summary": reflection.summary,
                },
            },
            "graph": {
                "nodes": len(nodes),
                "ring_size": ring_size,
                "edges": len(edges),
                "edge_types": sorted(edge_types),
                "black_hits": black_hit,
                "topology": topo,
            },
            "impact": {"accounts": len(nodes), "amount_24h": float(amount)},
            "evidence_fixed": True,
        }
