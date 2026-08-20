# -*- coding: utf-8 -*-
"""B1 图拓扑统计 + B3 并行假设编排 + C1 控辩内核单元测试（docs/14 §4，US-E9/E10）

覆盖：topology_stats（星/环/二部/嫌疑分）、compute_topology 超时降级、
hypothesis_skipped 覆盖核算（BA-BR-18）、execute_plan 并行与单源降级、
rule_debate/debate_disposition 三段结构与降级保底（BA-BR-19）。
拓扑/控辩输出均仅建议：不进入状态迁移入参（DA-INV-07，裁决权不变）。
"""
import asyncio
import time
from typing import Any

from app.skills import investigation as inv
from app.skills.investigation import compute_topology, topology_stats
from app.skills.planner import (
    DebateRecord, InvestigationPlan, SourceQuery, debate_disposition,
    execute_plan, hypothesis_skipped, rule_debate,
)


def _edge(s: str, d: str, t: str = "TRANSFER") -> dict[str, Any]:
    return {"src_node": s, "dst_node": d, "edge_type": t}


# ---------- B1 topology_stats（星/环/二部，DA-INV-07 线索分） ----------

def test_topology_empty_edges():
    s = topology_stats([], "A")
    assert s == {"nodes": 1, "edges": 0, "star_density": 0.0, "cycle_count": 0,
                 "bipartite_concentration": 0.0, "suspicion": 0.0, "degraded": False}


def test_topology_star_hub():
    # 星型：枢纽 A 出 4 边 → star_density = max_deg / m = 4/4 = 1.0
    edges = [_edge("A", x) for x in ("B", "C", "D", "E")]
    s = topology_stats(edges, "A")
    assert s["star_density"] == 1.0 and s["cycle_count"] == 0
    assert s["suspicion"] >= 0.4                      # 星型特征直接抬升嫌疑分
    assert s["nodes"] == 5 and s["edges"] == 4


def test_topology_triangle_cycle():
    edges = [_edge("A", "B"), _edge("B", "C"), _edge("C", "A")]
    s = topology_stats(edges, "A")
    assert s["cycle_count"] == 1                      # 三角形资金闭环计数 1（去重）
    assert s["suspicion"] > 0


def test_topology_bipartite_same_device():
    # 同设备 D1 关联 3 账户，D2 关联 1 账户 → 集中度 3/2
    edges = [_edge(a, "D1", "SAME_DEVICE") for a in ("A", "B", "C")]
    edges.append(_edge("E", "D2", "SAME_DEVICE"))
    s = topology_stats(edges, "A")
    assert s["bipartite_concentration"] == 1.5
    assert s["suspicion"] <= 1.0                      # 嫌疑分封顶 1.0


async def test_compute_topology_timeout_degrades(monkeypatch):
    def _slow(edges, root):
        time.sleep(0.5)
        return {}
    monkeypatch.setattr(inv, "topology_stats", _slow)
    monkeypatch.setattr(inv, "TOPO_TIMEOUT", 0.05)
    s = await compute_topology([_edge("A", "B")], "A")
    assert s["degraded"] is True and s["suspicion"] == 0.0   # 超时降级空统计，调查不阻断


async def test_compute_topology_normal():
    s = await compute_topology([_edge("A", "B"), _edge("B", "A")], "A")
    assert s["degraded"] is False and s["edges"] == 2


# ---------- B3 hypothesis_skipped 覆盖核算（BA-BR-18） ----------

def _plan(hypotheses: list[str]) -> InvestigationPlan:
    return InvestigationPlan(source="rule",
                             hypotheses=[{"pattern": p} for p in hypotheses])


def test_hypothesis_skipped_uncovered():
    plan = _plan(["跑分", "团伙盗刷"])
    findings = [{"source": "credit", "ok": True}]     # 仅 credit 成功
    out = hypothesis_skipped(plan, findings)
    # 团伙盗刷首选 complaint/sentiment 均未成功 → 留痕；跑分被 credit 覆盖
    assert [h["hypothesis"] for h in out] == ["团伙盗刷"]
    assert "BA-BR-18" in out[0]["reason"]


def test_hypothesis_skipped_covered_and_pending():
    plan = _plan(["跑分", "待定"])
    findings = [{"source": "credit", "ok": True},
                {"source": "complaint", "ok": False}]
    assert hypothesis_skipped(plan, findings) == []   # 已覆盖/待定均不产生豁免项


# ---------- B3 execute_plan 并行编排（asyncio.gather，priority 序稳定） ----------

class _PartialExternal:
    """sentiment 源故障，其余正常——验证单源失败不阻断其余并行分支"""

    async def query_credit_report(self, subject, reason):
        await asyncio.sleep(0.05)                     # 慢源先发起后完成，验证并行
        return {"source": "credit-mock", "risk_band": "low", "degraded": False}

    async def query_sentiment(self, subject, reason):
        raise ConnectionError("sentiment unavailable")

    async def query_complaints(self, subject, reason):
        return {"source": "complaint-mock", "items": [], "degraded": False}


async def test_execute_plan_parallel_and_partial_degrade():
    plan = InvestigationPlan(source="rule", queries=[
        SourceQuery("complaint", "否认交易线索", priority=2),
        SourceQuery("credit", "信用核验", priority=1),
        SourceQuery("sentiment", "团伙舆情", priority=3),
    ])
    t0 = time.monotonic()
    findings = await execute_plan(plan, _PartialExternal(), "subj-x")
    assert time.monotonic() - t0 < 0.2                # 并行：总时长≈最慢单源而非累加
    assert [f["source"] for f in findings] == ["credit", "complaint", "sentiment"]
    by = {f["source"]: f for f in findings}
    assert by["credit"]["ok"] and by["complaint"]["ok"]
    assert by["sentiment"]["ok"] is False and by["sentiment"]["degraded"] is True


async def test_execute_plan_external_none_marks_unexecuted():
    plan = InvestigationPlan(source="rule",
                             queries=[SourceQuery("credit", "r", 1)])
    findings = await execute_plan(plan, None, "subj-x")
    assert findings[0]["ok"] is False and "未执行" in findings[0]["summary"]


# ---------- C1 rule_debate / debate_disposition（BA-BR-19，裁决权不变） ----------

def test_rule_debate_structure_and_verdict_enum():
    rec = rule_debate("freeze", 9800.0, 85,
                      [{"claim": "证据", "source_ref": "AA-AG-03:x", "confidence": 0.9}])
    assert isinstance(rec, DebateRecord) and rec.source == "rule"
    assert rec.prosecution and rec.defense            # 控/辩双方论据非空
    assert rec.verdict in ("pass", "concerns", "escalate")
    assert rec.adjudication                            # 裁判倾向必填
    assert "审批官" in rec.summary                     # 裁决权归属声明


def test_rule_debate_weak_evidence_defense():
    rec = rule_debate("freeze", None, 55, [])         # 证据单薄 + 未达从严线
    assert any("单薄" in d for d in rec.defense)
    assert rec.adjudication != ""


async def test_debate_disposition_llm_unavailable_falls_back_rule():
    class _NoLlm:
        available = False
    rec = await debate_disposition("freeze", 1000.0, 80, [], client=_NoLlm())
    assert rec.source == "rule" and rec.verdict in ("pass", "concerns", "escalate")


async def test_debate_disposition_invalid_llm_output_degrades():
    class _BadLlm:
        available = True

        async def chat(self, messages, temperature=0.2):
            return "非法输出：无 JSON"
    rec = await debate_disposition("freeze", 1000.0, 80, [], client=_BadLlm())
    assert rec.source == "rule"                       # LLM 输出非法 → 降级规则版保底
