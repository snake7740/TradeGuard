# -*- coding: utf-8 -*-
"""LoopEngine 有界确定性环测试（loop 工程三步落地验证）

覆盖：
  L1 DLQ 失败归宿 —— deadletter_record 累计/驻车幂等、list、人工复位（DB 实链路）；
                     EventWorker 重试耗尽接 DLQ、驻车发 E-WORKER-DLQ、轮询排除驻车案件；
  L2 plan-reflect 双轮有界环 —— replan_from_gaps 可行动源判定（纯函数）+
                     investigation 首轮降级→二轮补查→merge→再 reflect（DB 实链路）；
  L3 慢环显式化 —— attribute_rule_proposals 归因闭环与幂等（DB 实链路）。
"""
import urllib.parse
import uuid

import pytest

from app.core.event_worker import MAX_RETRIES, EventWorker, SingleFlight
from app.core.loop_engine import (
    DEFAULT_POLICY,
    LoopPolicy,
    deadletter_list,
    deadletter_record,
    deadletter_retry,
)
from app.skills.planner import (
    InvestigationPlan,
    SourceQuery,
    replan_from_gaps,
)


# ---------------------------------------------------------------- L1 DLQ（DB 实链路）

async def test_deadletter_lifecycle_accumulate_park_retry(pool):
    """累计 → 达上限驻车（parked_now 仅触发一次）→ 人工复位清零（只增不删）"""
    cid = f"DLQ-{uuid.uuid4().hex[:12]}"
    err = RuntimeError("mcp 不可达")

    d1 = await deadletter_record(pool, cid, "aggregation", err, 3)
    d2 = await deadletter_record(pool, cid, "aggregation", err, 3)
    assert (d1["attempts"], d2["attempts"]) == (3, 6)
    assert not d1["parked"] and not d2["parked"]

    d3 = await deadletter_record(pool, cid, "aggregation", err, 3)
    assert d3["attempts"] == 9 and d3["parked"] and d3["parked_now"]

    d4 = await deadletter_record(pool, cid, "aggregation", err, 3)
    assert d4["parked"] and not d4["parked_now"]  # 幂等：已驻车不再重复告知

    items = await deadletter_list(pool)
    assert cid in [r["case_id"] for r in items]

    res = await deadletter_retry(pool, cid, "human:test_oncall")
    assert res["ok"] is True
    row = await pool.fetchrow(
        "SELECT attempts, parked, resolved_by FROM processing_deadletter"
        " WHERE case_id=$1", cid)
    assert row["attempts"] == 0 and not row["parked"]
    assert row["resolved_by"] == "human:test_oncall"

    miss = await deadletter_retry(pool, f"NO-{uuid.uuid4().hex[:8]}", "human:x")
    assert miss["ok"] is False  # 无记录视为无需复位


def test_loop_policy_backoff_is_linear_and_bounded():
    assert DEFAULT_POLICY.next_delay(0) == DEFAULT_POLICY.backoff_base
    assert DEFAULT_POLICY.next_delay(2) == DEFAULT_POLICY.backoff_base * 3
    assert LoopPolicy(max_retries=1).dead_letter_cap == 9  # 上限独立于单轮重试


# ---------------------------------------------------------------- L1 EventWorker 接 DLQ（单测替身）

class _DLQFakePool:
    """fetch 返回预设行；fetchrow/execute 记录 DLQ 写入（upsert RETURNING 模拟）"""

    def __init__(self, case_ids, parked=False):
        self.case_ids = list(case_ids)
        self.records: list[tuple] = []
        self.executes: list[tuple] = []
        self.parked = parked

    async def fetch(self, query, *args):
        return [{"case_id": c} for c in self.case_ids]

    async def fetchrow(self, query, *args):
        self.records.append(args)
        return {"attempts": args[4], "parked": self.parked}

    async def execute(self, query, *args):
        self.executes.append((query, args))
        return "UPDATE 1"


class _FailAgg:
    def __init__(self):
        self.calls: list[str] = []

    async def run(self, case_id):
        self.calls.append(case_id)
        raise RuntimeError("mcp 不可达")


class _RecPub:
    def __init__(self):
        self.published: list[dict] = []

    async def publish(self, case_id, event, payload, actor, trace_id=None):
        rec = {"case_id": case_id, "event": event, "payload": payload, "actor": actor}
        self.published.append(rec)
        return rec


async def test_retry_exhaustion_records_deadletter(monkeypatch):
    """重试耗尽 → DLQ 累计记录（未达上限不驻车、不发告警事件）"""
    monkeypatch.setattr("app.core.event_worker.RETRY_BASE_DELAY", 0)
    pool, agg, pub = _DLQFakePool(["CASE-DLQ-1"]), _FailAgg(), _RecPub()
    w = EventWorker(pool, agg, SingleFlight(), pub=pub)
    await w._sweep(window_minutes=None)
    assert len(agg.calls) == MAX_RETRIES
    assert len(pool.records) == 1 and pool.records[0][4] == MAX_RETRIES
    assert [e["event"] for e in pub.published] == []  # 3 < 9 未驻车


async def test_retry_exhaustion_parks_and_emits_dlq_event(monkeypatch):
    """累计达上限 → 驻车告知：审计 worker.deadletter + E-WORKER-DLQ 事件"""
    monkeypatch.setattr("app.core.event_worker.RETRY_BASE_DELAY", 0)
    monkeypatch.setattr("app.core.event_worker.MAX_RETRIES", 9)  # 单轮 9 次恰达上限
    pool, agg, pub = _DLQFakePool(["CASE-DLQ-2"]), _FailAgg(), _RecPub()
    w = EventWorker(pool, agg, SingleFlight(), pub=pub)
    await w._sweep(window_minutes=None)
    assert len(agg.calls) == 9
    dlq_events = [e for e in pub.published if e["event"] == "E-WORKER-DLQ"]
    assert len(dlq_events) == 1
    assert dlq_events[0]["payload"]["stage"] == "aggregation"
    assert any("worker.deadletter" in str(q) for q, _ in pool.executes)  # 审计留痕


async def test_sweep_sql_excludes_parked_cases(pool, case_repo):
    """驻车案件排除在轮询候选外（DB 实链路 SQL 验证）：不再无限重试。
    用带窗 sweep（近 10 分钟）隔离共享库历史残留 REGISTERED 案件干扰；
    种子源用 DEMO（非 TEST）——TEST 源被自动环确定性排除（10-case-source.sql）。"""
    repo, _ = case_repo
    parked = (await repo.register(uuid.uuid4().hex, risk_score=50,
                                  source_type="DEMO"))["case_id"]
    active = (await repo.register(uuid.uuid4().hex, risk_score=50,
                                  source_type="DEMO"))["case_id"]
    await deadletter_record(pool, parked, "aggregation", RuntimeError("x"), 9)

    calls: list[str] = []

    class _RecAgg:
        async def run(self, case_id):
            calls.append(case_id)

    w = EventWorker(pool, _RecAgg(), SingleFlight())
    await w._sweep(window_minutes=10)
    assert parked not in calls          # 驻车案件不再被捞起
    assert active in calls              # 正常案件不受影响


# ---------------------------------------------------------------- L2 replan_from_gaps（纯函数）

def _plan(*sources) -> InvestigationPlan:
    return InvestigationPlan(
        source="rule",
        queries=[SourceQuery(s, priority=1, reason="t") for s in sources])


def test_replan_targets_degraded_sources_only():
    plan = _plan("credit", "complaint", "sentiment")
    findings = [
        {"source": "credit", "ok": True, "degraded": False},
        {"source": "complaint", "ok": False, "degraded": True},
        {"source": "sentiment", "ok": True, "degraded": False},
    ]
    follow = replan_from_gaps(plan, findings)
    assert follow is not None and follow.source == "rule+loop-r2"
    assert [q.source for q in follow.queries] == ["complaint"]


def test_replan_targets_missing_sources():
    plan = _plan("credit", "complaint")
    findings = [{"source": "credit", "ok": True, "degraded": False}]
    follow = replan_from_gaps(plan, findings)
    assert follow is not None and [q.source for q in follow.queries] == ["complaint"]


def test_replan_none_when_no_actionable_gap():
    """全部成功（或仅「假设未定性」类不可行动缺口）→ 不触发二轮，环自然终止"""
    plan = _plan("credit", "complaint")
    findings = [
        {"source": "credit", "ok": True, "degraded": False},
        {"source": "complaint", "ok": True, "degraded": False},
    ]
    assert replan_from_gaps(plan, findings) is None


# ---------------------------------------------------------------- L2 双轮有界环（DB 实链路）

class _FlakyCreditExternal:
    """credit 源首轮不可用（降级）、二轮恢复——确定性双轮触发器。
    其余源与 conftest.FakeExternal 同构（complaint 1 条否认交易）。"""

    def __init__(self):
        self.credit_calls = 0

    async def query_credit_report(self, subject_id, query_reason):
        self.credit_calls += 1
        if self.credit_calls == 1:
            raise ConnectionError("credit source temporarily unavailable")
        return {"source": "credit-mock", "subject_id": subject_id,
                "credit_score": 750, "risk_band": "low",
                "overdue_count_12m": 0, "query_reason": query_reason,
                "degraded": False}

    async def query_sentiment(self, subject_id, query_reason):
        return {"source": "sentiment-mock", "subject_id": subject_id,
                "hits": [], "query_reason": query_reason, "degraded": False}

    async def query_complaints(self, subject_id, query_reason):
        return {"source": "complaint-mock", "subject_id": subject_id,
                "items": [{"type": "deny_transaction",
                           "content": "持卡人否认该笔交易", "channel": "phone"}],
                "query_reason": query_reason, "degraded": False}

    async def query_enterprise(self, subject_id, query_reason):
        return {"source": "enterprise-mock", "subject_id": subject_id,
                "reg_status": "active", "abnormal_ops_count": 0,
                "admin_penalty_12m": 0, "judicial_risk_count": 0,
                "related_entity_count": 1, "risk_flag": "low",
                "query_reason": query_reason, "degraded": False}


async def _investigating_case(repo, score: int = 60) -> str:
    from app.core.state_machine import CaseEvent
    reg = await repo.register(uuid.uuid4().hex, risk_score=score, source_type="TEST")
    case_id = reg["case_id"]
    r = await repo.transition(case_id, CaseEvent.AGGREGATION_STARTED,
                              "agent:AA-AG-02", 0)
    await repo.transition(case_id, CaseEvent.SIGNALS_AGGREGATED,
                          "agent:AA-AG-02", r["version"])
    return case_id


class _NoLlm:
    """配 Key 环境下隔离真实外呼（同 R-49 先例）：环机制测试走确定性
    规则反思/规划，LLM 协商由注入 client 单测覆盖"""
    available = False


async def test_double_loop_retries_degraded_source_then_sufficient(investigation):
    """首轮 credit 降级 → reflect=gaps → 二轮仅补查 credit → merge 覆盖 → sufficient；
    rounds 留痕 2 轮，环有界不空转（external=None 时退化为单轮，不崩链路）"""
    svc, repo, _ = investigation
    svc.llm_client = _NoLlm()
    case_id = await _investigating_case(repo)
    # 高频小额特征 → rule_plan 计划 credit+complaint（跑分假设，BA-BR-14）
    await svc.core.record_case_signals(case_id, 60, [{
        "source": "tx", "type": "velocity_anomaly", "confidence": 0.8,
        "raw_ref": f"{case_id}:tx", "query_reason": "test",
        "velocity_json": {"velocity_1h": {"count": 12, "amount": 900.0},
                          "velocity_24h": {"count": 18, "amount": 1500.0}}}])
    svc.external = _FlakyCreditExternal()

    out = await svc.run(case_id)

    rounds = out["plan"]["rounds"]
    assert len(rounds) == 2                       # 有界：恰好二轮
    assert rounds[0]["verdict"] == "gaps"
    assert rounds[1]["replan"] == ["credit"]      # 仅补查降级源
    credit = [f for f in out["plan"]["findings"] if f["source"] == "credit"][0]
    assert credit["ok"] is True                   # merge 后首轮降级被二轮覆盖
    assert out["plan"]["reflection"]["verdict"] == "sufficient"


async def test_single_round_when_no_actionable_gap(investigation):
    """全部源首轮成功 → 无反思缺口 → 环一轮终止（不空转二轮）"""
    svc, repo, _ = investigation
    svc.llm_client = _NoLlm()
    case_id = await _investigating_case(repo)
    await svc.core.record_case_signals(case_id, 60, [{
        "source": "tx", "type": "velocity_anomaly", "confidence": 0.8,
        "raw_ref": f"{case_id}:tx", "query_reason": "test",
        "velocity_json": {"velocity_1h": {"count": 12, "amount": 900.0},
                          "velocity_24h": {"count": 18, "amount": 1500.0}}}])

    out = await svc.run(case_id)

    assert len(out["plan"]["rounds"]) == 1
    assert out["plan"]["reflection"]["verdict"] == "sufficient"


# ---------------------------------------------------------------- L3 慢环归因（DB 实链路）

async def _publish_proposal(pool, svc_core, case_id: str, title: str,
                            published_ago: str) -> str:
    """rule_proposal 播种：经 AA-MCP-05 提交 pending 申请单（tg_app 写路径，
    02-roles.sql）→ tg_web 人审发布（DA-INV-08 触发器守护），并回溯
    reviewed_at 使「再犯/未再犯」观测窗可控；返回 source_case_id"""
    import json
    out = await svc_core.submit_kb_application(
        case_id, "rule_proposal", title, "测试提案内容（LoopEngine 慢环归因）")
    if isinstance(out, str):  # 适配器未解析时的兼容（mcp 原始串）
        out = json.loads(out)
    doc_id = out["doc_id"]
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "SELECT set_config('tg.actor', 'human:test', true)")
        await conn.execute(
            f"""UPDATE kb_document SET status='published', reviewer='human:test',
                    reviewed_at=now() - interval '{published_ago}'
                WHERE doc_id=$1""", doc_id)
    return case_id


async def test_attribute_rule_proposals_recurrence_closed_loop(pool, case_repo,
                                                               investigation):
    """rule_proposal 发布后同主体再犯 → recurred_after=True（慢环可度量）；
    归因只增（重复运行 checked=0，DA-INV-08 发布仍须人审不经由此函数）"""
    from app.skills.knowledge import attribute_rule_proposals
    repo, _ = case_repo
    svc, _, _ = investigation
    subject = uuid.uuid4().hex
    src = (await repo.register(subject, risk_score=60, source_type="TEST"))["case_id"]
    await repo.register(subject, risk_score=60, source_type="TEST")  # 同主体再犯
    await _publish_proposal(pool, svc.core, src, "测试收紧提案", "1 day")

    res = await attribute_rule_proposals(pool)
    assert res["checked"] == 1 and res["recurred"] == 1  # 再犯案件晚于发布时刻
    row = await pool.fetchrow(
        "SELECT source_case_id, recurred_after FROM proposal_attribution"
        " WHERE doc_id IN (SELECT doc_id FROM kb_document"
        "                  WHERE source_case_id=$1 AND category='rule_proposal')",
        src)
    assert row["source_case_id"] == src and row["recurred_after"] is True

    again = await attribute_rule_proposals(pool)
    assert again["checked"] == 0  # 幂等：归因只增不重算


async def test_attribute_rule_proposals_no_recurrence(pool, case_repo,
                                                      investigation):
    """发布后主体未再犯 → recurred_after=False"""
    from app.skills.knowledge import attribute_rule_proposals
    repo, _ = case_repo
    svc, _, _ = investigation
    src = (await repo.register(uuid.uuid4().hex, risk_score=60,
                               source_type="TEST"))["case_id"]
    await _publish_proposal(pool, svc.core, src, "测试提案B", "0 hours")

    res = await attribute_rule_proposals(pool)
    assert res["checked"] == 1 and res["recurred"] == 0


# ---------------------------------------------------------------- API 人工门契约

@pytest.fixture(scope="module")
def api_client():
    """全链 TestClient（同 test_multi_role_flow 装配法）：验证守卫层角色门"""
    import os
    from fastapi.testclient import TestClient
    from conftest import PG_DSN
    os.environ["PG_DSN"] = PG_DSN
    os.environ.pop("TG_API_TOKEN", None)
    from app.main import create_app
    with TestClient(create_app()) as c:
        yield c


async def test_deadletter_retry_agent_refused(api_client):
    """人工门：agent: 自声明复位 → 409 E-HUMAN-ONLY（环不得自清失败归宿）"""
    r = api_client.post("/api/deadletter/CASE-ANY/retry",
                        headers={"X-Operator": "agent:AA-AG-04"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "E-HUMAN-ONLY"


async def test_deadletter_retry_role_gate_and_oncall_reset(pool, api_client):
    """角色门：审计员越权复位 → 403 E-FORBIDDEN-ROLE；值班员复位驻车行放行"""
    cid = f"DLQ-{uuid.uuid4().hex[:12]}"
    await deadletter_record(pool, cid, "aggregation", RuntimeError("x"), 9)
    r = api_client.post(f"/api/deadletter/{cid}/retry",
                        headers={"X-Operator": urllib.parse.quote("合规审计员")})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "E-FORBIDDEN-ROLE"
    r = api_client.post(f"/api/deadletter/{cid}/retry",
                        headers={"X-Operator": urllib.parse.quote("风控值班员")})
    assert r.status_code == 200 and r.json()["ok"] is True
    row = await pool.fetchrow(
        "SELECT parked, resolved_by FROM processing_deadletter WHERE case_id=$1", cid)
    assert row["parked"] is False and row["resolved_by"].startswith("human:")
