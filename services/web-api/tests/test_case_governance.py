# -*- coding: utf-8 -*-
"""案件治理批次单测（BA-BR-26/27/28，SC-25~27 的确定性内核层，docs/14 v1.7 US-E17~19）

三层覆盖：
1. 纯函数层（case_governance / narrative）：分级边界、aging、自动关闭准入、
   引用对齐门禁——零外部依赖直接断言；
2. 仓储层：优先级队列排布（risk DESC + updated ASC，归档剔除）与归档复位
   迁移守卫（human_only + 非法状态对，DA-INV-01）；
3. 叙事编排：规则轨构造性对齐、注入轨未对齐降级、生成留痕（audit narrative.generated）。
"""
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.state_machine import CaseEvent, InvalidTransition
from app.skills.case_governance import (aging_breach, aging_hours,
                                        auto_close_eligible, priority_tier)
from app.skills.narrative import (build_case_narrative, citation_universe,
                                  compose_narrative, verify_citations)

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


# ---------- BA-BR-26 优先级分级与 aging ----------

def test_priority_tier_boundaries_reuse_br01_br02():
    """分级边界与 BA-BR-02 审批线（70）/BA-BR-01 自动线（40）同源"""
    assert priority_tier(100) == "high" and priority_tier(70) == "high"
    assert priority_tier(69) == "mid" and priority_tier(40) == "mid"
    assert priority_tier(39) == "low" and priority_tier(0) == "low"


def test_aging_hours_and_breach():
    assert aging_hours(NOW - timedelta(hours=25), NOW) == 25.0
    assert aging_breach(25.0, 24) is True
    assert aging_breach(24.0, 24) is False          # 恰等于阈值不算超期
    naive = datetime(2026, 8, 22, 10, 0)            # naive 输入按 UTC 补齐
    assert aging_hours(naive, NOW) == 2.0
    assert aging_hours(NOW + timedelta(hours=1), NOW) == 0.0  # 时钟漂移不为负


# ---------- BA-BR-28 自动关闭准入 ----------

def test_auto_close_eligible_standard():
    assert auto_close_eligible(0, 4999.99) is True
    assert auto_close_eligible(1, 100.0) is False   # 有信号不得自动关闭
    assert auto_close_eligible(0, 5000.0) is False  # 金额触限不得自动关闭
    assert auto_close_eligible(0, 100.0, max_amount=100) is False  # 热配置收紧生效


# ---------- BA-BR-27 叙事装配与引用对齐 ----------

def _sig(sid: str) -> dict:
    return {"signal_id": sid, "source": "tx", "type": "velocity_anomaly",
            "confidence": 0.8}


def test_compose_narrative_citations_subset_of_material():
    sid = uuid.uuid4().hex
    case = {"case_id": "CASE-T", "subject_ref": "acc1", "risk_score": 60,
            "status": "INVESTIGATING", "created_at": "2026-08-22T00:00:00+00:00"}
    out = compose_narrative(case, [_sig(sid)], [], [], [])
    assert [s["heading"] for s in out["sections"]] == \
        ["案件概况", "风险信号", "证据链", "处置记录", "审批记录"]
    assert out["citations"] == [f"[SIG:{sid[:8]}]"]
    universe = citation_universe([_sig(sid)], [], [], [])
    text = " ".join(s["text"] for s in out["sections"])
    assert verify_citations(text, universe) == []   # 规则轨构造性恒通过


def test_compose_narrative_empty_material_declares_honestly():
    """空素材如实声明无记录而非编造（负结果不粉饰）"""
    case = {"case_id": "CASE-E", "subject_ref": "acc", "risk_score": 10,
            "status": "REGISTERED", "created_at": None}
    out = compose_narrative(case, [], [], [], [])
    texts = " ".join(s["text"] for s in out["sections"])
    assert "无风险信号记录" in texts and "无证据记录" in texts
    assert out["citations"] == []


def test_verify_citations_rejects_ungrounded_injection():
    sid = uuid.uuid4().hex
    universe = citation_universe([_sig(sid)], [], [], [])
    forged = f"论断引用 [{sid[:8]}]" + "[SIG:" + uuid.uuid4().hex[:8] + "]"
    bad = verify_citations(forged, universe)
    assert len(bad) == 1 and bad[0].startswith("[SIG:")


# ---------- 仓储层：优先级队列排布与归档复位守卫 ----------

async def test_queue_orders_by_risk_desc_and_excludes_archived(case_repo):
    repo, _ = case_repo
    # 共享库存在历史残留案件，取顶格分档（100/99）保证自身案件稳入队首区
    hi = await repo.register(uuid.uuid4().hex, risk_score=100, source_type="TEST")
    lo = await repo.register(uuid.uuid4().hex, risk_score=99, source_type="TEST")
    arc = await repo.register(uuid.uuid4().hex, risk_score=99, source_type="TEST")
    # 立即推进出 REGISTERED：compose 栈 EventWorker 2s 轮询抢跑窗口免疫（同直插法）
    for c in (hi, lo):
        r = await repo.transition(c["case_id"], CaseEvent.AGGREGATION_STARTED,
                                  "agent:AA-AG-02", 0)
        await repo.transition(c["case_id"], CaseEvent.SIGNALS_AGGREGATED,
                              "agent:AA-AG-02", r["version"])
    r = await repo.transition(arc["case_id"], CaseEvent.AGGREGATION_STARTED,
                              "agent:AA-AG-02", 0)
    await repo.transition(arc["case_id"], CaseEvent.NOISE_DISMISSED,
                          "agent:AA-AG-02", r["version"])

    items = await repo.queue(size=200)
    ids = [c["case_id"] for c in items]
    assert hi["case_id"] in ids and lo["case_id"] in ids
    assert ids.index(hi["case_id"]) < ids.index(lo["case_id"])  # 风险优先而非立案时序
    assert arc["case_id"] not in ids                            # 归档案件不入队


async def test_reopen_transition_guards(case_repo):
    repo, pub = case_repo
    reg = await repo.register(uuid.uuid4().hex, risk_score=20, source_type="TEST")
    r = await repo.transition(reg["case_id"], CaseEvent.AGGREGATION_STARTED,
                              "agent:AA-AG-02", 0)
    r = await repo.transition(reg["case_id"], CaseEvent.NOISE_DISMISSED,
                              "agent:AA-AG-02", r["version"])
    assert (await repo.get(reg["case_id"]))["status"] == "ARCHIVED"

    with pytest.raises(InvalidTransition) as e:      # agent 越权 human_only → E-HUMAN-ONLY
        await repo.transition(reg["case_id"], CaseEvent.CASE_REOPENED,
                              "agent:AA-AG-02", r["version"], basis="自动复位")
    assert e.value.code == "E-HUMAN-ONLY"

    out = await repo.transition(reg["case_id"], CaseEvent.CASE_REOPENED,
                                "human:风控值班员", r["version"], basis="误关补救")
    assert out["status"] == "MANUAL_REVIEW"          # 人工复位入人工复核
    assert any(m["event"] == "CaseReopened" for m in pub.published)

    with pytest.raises(InvalidTransition) as e2:     # 已复位案件重复复位 → 非法迁移
        await repo.transition(reg["case_id"], CaseEvent.CASE_REOPENED,
                              "human:风控值班员", out["version"], basis="再次复位")
    assert e2.value.code == "E-BAD-TRANSITION"


# ---------- 叙事编排：双轨降级 + 生成留痕 ----------

async def test_build_narrative_rule_track_and_audit(pool, case_repo):
    repo, _ = case_repo
    reg = await repo.register(uuid.uuid4().hex, risk_score=55, source_type="TEST")
    out = await build_case_narrative(pool, reg["case_id"], "human:风控值班员")
    assert out["status"] == "DRAFT" and out["track"] == "rule"
    assert len(out["sections"]) == 5
    hits = await pool.fetch(
        "SELECT basis FROM audit_log WHERE target=$1 AND action='narrative.generated'",
        reg["case_id"])
    assert hits and "track=rule" in hits[0]["basis"]


async def test_build_narrative_ungrounded_generator_falls_back(pool, case_repo):
    repo, _ = case_repo
    reg = await repo.register(uuid.uuid4().hex, risk_score=55, source_type="TEST")

    def _evil(case, signals, evidence, dispositions, approvals):
        return "凭空论断 [EV:" + uuid.uuid4().hex[:8] + "]"   # 素材之外的引用

    out = await build_case_narrative(pool, reg["case_id"], "human:风控值班员",
                                     generator=_evil)
    assert out["track"] == "rule"                    # 未对齐降级规则轨（R-49 先例）


async def test_build_narrative_grounded_generator_passes_gate(pool, app_pool, case_repo):
    repo, _ = case_repo
    reg = await repo.register(uuid.uuid4().hex, risk_score=55, source_type="TEST")
    sid = uuid.uuid4().hex
    await app_pool.execute(  # risk_signal 写角色为 tg_app（DA-INV-05，02-roles.sql）
        """INSERT INTO risk_signal (signal_id, case_id, source, type, confidence,
                                    raw_ref, query_reason)
           VALUES ($1, $2, 'tx', 'velocity_anomaly', 0.8, 'test', 'SC')""",
        sid, reg["case_id"])

    def _good(case, signals, evidence, dispositions, approvals):
        return f"本段引用真实信号 [SIG:{sid[:8]}]"

    out = await build_case_narrative(pool, reg["case_id"], "human:风控值班员",
                                     generator=_good)
    assert out["track"] == "llm"                     # 对齐通过走注入轨
    assert re.search(r"\[SIG:[0-9a-f]{8}\]", out["sections"][0]["text"])
