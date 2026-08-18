# -*- coding: utf-8 -*-
"""R-47 AG-01 规划-反思内核测试（Manager 规划循环）

覆盖：规则规划特征驱动源选择（豁免留痕）、LLM 规划解析与白名单校验、
降级保底（LLM 失败/不可用 → 规则版，行为下限不变）、计划执行单源降级、
反思 sufficient/gaps 判定、investigation.run 端到端嵌入（证据链含计划
执行与反思记录，审计 basis 带 plan/reflect 标记）。
"""
import json
import uuid

from app.core.state_machine import CaseEvent
from app.skills import planner as P


class StubLlm:
    """可控 LLM stub：available=True，chat 返回预制文本或抛异常"""

    def __init__(self, reply: str | None = None, fail: bool = False):
        self.reply = reply
        self.fail = fail
        self.calls = 0

    @property
    def available(self) -> bool:
        return True

    async def chat(self, messages, temperature=0.2) -> str:
        self.calls += 1
        if self.fail:
            raise RuntimeError("stub llm down")
        assert self.reply is not None
        return self.reply


def _sig_velocity():
    return [{"source": "tx", "type": "velocity_anomaly",
             "velocity_json": {"velocity_1h": {"count": 12, "amount": 900.0}}}]


def _sig_large_amount():
    return [{"source": "tx", "type": "large_amount_burst", "confidence": 0.9}]


# ---------- 规则规划：特征驱动源选择 + 豁免留痕 ----------

def test_rule_plan_velocity_selects_credit_complaint():
    plan = P.rule_plan(_sig_velocity(), set())
    srcs = {q.source for q in plan.queries}
    assert srcs == {"credit", "complaint"}          # 跑分特征：流水核验 + 否认线索
    assert {s["source"] for s in plan.skipped} == {"sentiment"}
    assert all(s["reason"] for s in plan.skipped)    # 豁免必须给理由（审计回放）
    assert plan.hypotheses[0]["pattern"] == "跑分"


def test_rule_plan_no_features_queries_all_conservatively():
    plan = P.rule_plan([{"source": "tx", "type": "normal"}], set())
    assert {q.source for q in plan.queries} == {"credit", "sentiment", "complaint"}
    assert plan.skipped == []                        # 无特征不豁免任何源
    assert plan.hypotheses[0]["pattern"] == "待定"


def test_rule_plan_same_device_edge():
    plan = P.rule_plan([], {"SAME_DEVICE"})
    assert {q.source for q in plan.queries} == {"complaint", "sentiment"}
    assert plan.hypotheses[0]["pattern"] == "团伙盗刷"


# ---------- LLM 规划：解析 / 白名单 / 空计划回退 / 降级 ----------

def test_make_plan_llm_parses_and_filters_whitelist():
    raw = json.dumps({
        "hypotheses": [{"pattern": "跑分", "priority": 1, "rationale": "高频小额"}],
        "queries": [
            {"source": "credit", "reason": "流水核验", "priority": 1},
            {"source": "darkweb", "reason": "黑市情报", "priority": 1},  # 白名单外 → 丢弃
        ],
        "kb_queries": ["跑分 查询词"],
        "skipped": [{"source": "sentiment", "reason": "无舆情因果"}],
        "rationale": "高频小额指向跑分",
    }, ensure_ascii=False)
    import asyncio
    plan = asyncio.run(P.make_plan(_sig_velocity(), set(), client=StubLlm(
        "```json\n" + raw + "\n```")))                # markdown 包裹也要能解析
    assert plan.source == "llm"
    assert {q.source for q in plan.queries} == {"credit"}   # darkweb 被滤除
    assert plan.skipped[0]["source"] == "sentiment"
    assert plan.kb_queries == ["跑分 查询词"]


def test_make_plan_llm_empty_queries_falls_back_to_all_sources():
    raw = json.dumps({"queries": [], "skipped": [], "hypotheses": []})
    import asyncio
    plan = asyncio.run(P.make_plan([], set(), client=StubLlm(raw)))
    assert {q.source for q in plan.queries} == {"credit", "sentiment", "complaint"}


def test_make_plan_llm_failure_degrades_to_rule():
    import asyncio
    plan = asyncio.run(P.make_plan(_sig_velocity(), set(), client=StubLlm(fail=True)))
    assert plan.source == "rule"
    assert {q.source for q in plan.queries} == {"credit", "complaint"}


def test_make_plan_unavailable_client_uses_rule_without_llm_call():
    import asyncio

    class NoKey:
        available = False

        async def chat(self, *a, **k):  # pragma: no cover - 不应被调用
            raise AssertionError("unavailable client must not call chat")

    plan = asyncio.run(P.make_plan(_sig_velocity(), set(), client=NoKey()))
    assert plan.source == "rule"


# ---------- 计划执行：单源降级 / 通道未装配 ----------

class FlakyExternal:
    """credit 源抛异常，其余正常（单源失败不阻断其余计划项）"""

    async def query_credit_report(self, subject_id, query_reason):
        raise ConnectionError("credit source down")

    async def query_sentiment(self, subject_id, query_reason):
        return {"source": "sentiment-mock", "hits": [], "degraded": False}

    async def query_complaints(self, subject_id, query_reason):
        return {"source": "complaint-mock", "items": [], "degraded": False}


def test_execute_plan_single_source_degrades_without_blocking():
    plan = P.rule_plan(_sig_velocity(), set())       # credit + complaint
    import asyncio
    findings = asyncio.run(P.execute_plan(plan, FlakyExternal(), "s01"))
    by_src = {f["source"]: f for f in findings}
    assert by_src["credit"]["degraded"] is True      # 异常源记录降级
    assert by_src["complaint"]["ok"] is True         # 其余计划项不受阻断


def test_execute_plan_none_external_marks_all_unexecuted():
    plan = P.rule_plan(_sig_velocity(), set())
    import asyncio
    findings = asyncio.run(P.execute_plan(plan, None, "s01"))
    assert findings and all(f["degraded"] for f in findings)


# ---------- 反思：判定与降级 ----------

def test_rule_reflect_sufficient_when_all_ok_and_pattern_named():
    plan = P.rule_plan(_sig_velocity(), set())
    findings = [{"source": q.source, "ok": True, "degraded": False}
                for q in plan.queries]
    r = P.rule_reflect(plan, findings, "跑分")
    assert r.verdict == "sufficient" and r.gaps == []


def test_rule_reflect_reports_gaps_on_degraded_or_pending():
    plan = P.rule_plan(_sig_velocity(), set())
    findings = [{"source": q.source, "ok": False, "degraded": True}
                for q in plan.queries]
    r = P.rule_reflect(plan, findings, "待定")
    assert r.verdict == "gaps"
    assert any("降级" in g for g in r.gaps)
    assert any("待定" in g or "人工" in g for g in r.gaps)


def test_reflect_llm_degrades_to_rule():
    plan = P.rule_plan(_sig_velocity(), set())
    findings = [{"source": q.source, "ok": True, "degraded": False}
                for q in plan.queries]
    import asyncio
    r = asyncio.run(P.reflect(plan, findings, "跑分", client=StubLlm(fail=True)))
    assert r.source == "rule" and r.verdict == "sufficient"


def test_reflect_llm_verdict_validated():
    plan = P.rule_plan(_sig_velocity(), set())
    findings = [{"source": q.source, "ok": True, "degraded": False}
                for q in plan.queries]
    import asyncio
    r = asyncio.run(P.reflect(
        plan, findings, "跑分",
        client=StubLlm(json.dumps({"verdict": "gaps", "gaps": ["征信未覆盖历史月"],
                                   "summary": "建议补查"}, ensure_ascii=False))))
    assert r.source == "llm" and r.verdict == "gaps" and r.gaps


# ---------- 端到端：investigation.run 嵌入规划-反思 ----------

async def test_investigation_run_embeds_plan_and_reflection(pool, investigation):
    """调查主链路含 AG-01 计划执行与反思：结果结构、证据链 claim、审计标记"""
    svc, repo, _ = investigation
    subject = uuid.uuid4().hex
    reg = await repo.register(subject, risk_score=55, source_type="TEST")
    case_id = reg["case_id"]
    r = await repo.transition(case_id, CaseEvent.AGGREGATION_STARTED, "agent:AA-AG-02", 0)
    await repo.transition(case_id, CaseEvent.SIGNALS_AGGREGATED, "agent:AA-AG-02", r["version"])
    await svc.core.record_case_signals(case_id, 55, [{
        "source": "tx", "type": "velocity_anomaly", "confidence": 0.8,
        "raw_ref": f"{case_id}:tx", "query_reason": "test",
        "velocity_json": {"velocity_1h": {"count": 12, "amount": 900.0},
                          "velocity_24h": {"count": 18, "amount": 1500.0}}}])

    out = await svc.run(case_id)

    # 计划与反思出现在结果结构（LLM 无 Key → 规则版下限，行为可预期）
    assert out["plan"]["source"] in ("rule", "llm")
    assert out["plan"]["queries"], "计划至少含一个源查询"
    assert out["plan"]["reflection"]["verdict"] in ("sufficient", "gaps")
    # 证据链落 AG-01 计划/反思 claim（DA-T-05 只增，可回放）
    claims = await pool.fetch(
        "SELECT claim FROM case_evidence WHERE case_id=$1 AND source_ref='AA-AG-01:plan-reflect'",
        case_id)
    assert claims and "AG-01 计划" in claims[0]["claim"]
    # 审计 basis 带 plan/reflect 标记（R-47）
    audit = await pool.fetchval(
        "SELECT basis FROM audit_log WHERE target=$1"
        " AND action='investigation.complete'", case_id)
    assert "plan=" in audit and "reflect=" in audit
