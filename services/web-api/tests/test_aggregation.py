# -*- coding: utf-8 -*-
"""AA-SK-01 确定性聚合内核单元测试（US-E3-03，06 §3 纯函数优先，先测后码）

覆盖：velocity 频次统计（BA-BR-14）、降噪合并、加权评分、防腐层翻译、分级裁决。
验收锚点：SC-11 的单元层证据（velocity 参与评分）；行覆盖 ≥60%。
"""
import math
from datetime import datetime, timedelta, timezone

import pytest

from app.skills.aggregation import (
    AUTO_AMOUNT_MAX, AUTO_SCORE_MAX, SOURCE_WEIGHTS, VELOCITY_BONUS, ZERO_VELOCITY,
    build_tx_signal, compute_velocity, dedupe_signals, normalize_complaints,
    normalize_credit_report, normalize_sentiment, score_signals, triage,
    velocity_bonus,
)

NOW = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)
REASON = "case=CASE-TEST reason"


def _txs(n, amount=50.0, minutes_ago=10):
    """近 N 笔交易（均在 1h 窗口内）"""
    return [{"amount": amount, "ts": NOW - timedelta(minutes=minutes_ago)} for _ in range(n)]


# ---------- velocity 频次统计（BA-BR-14） ----------

def test_compute_velocity_counts_and_amounts():
    txs = _txs(12, amount=50.0) + [{"amount": 100.0, "ts": NOW - timedelta(hours=5)}]
    v = compute_velocity(txs, NOW)
    assert v["velocity_1h"]["count"] == 12
    assert v["velocity_24h"]["count"] == 13
    assert math.isclose(v["velocity_1h"]["amount"], 600.0)
    assert math.isclose(v["velocity_24h"]["amount"], 700.0)


def test_compute_velocity_empty():
    assert compute_velocity([], NOW) == ZERO_VELOCITY


def test_compute_velocity_outside_24h_ignored():
    v = compute_velocity([{"amount": 1.0, "ts": NOW - timedelta(hours=25)}], NOW)
    assert v["velocity_24h"]["count"] == 0 and v["velocity_1h"]["count"] == 0


@pytest.mark.parametrize("c1,c24,expected", [
    (10, 10, VELOCITY_BONUS),   # 边界：1h≥10 笔
    (9, 50, VELOCITY_BONUS),    # 边界：24h≥50 笔
    (9, 49, 0),                 # 双不达标
    (0, 0, 0),
    (55, 55, VELOCITY_BONUS),   # 高频簇（演示档单账户峰值 55 笔）
])
def test_velocity_bonus_thresholds(c1, c24, expected):
    """BA-BR-14：1h≥10 笔或 24h≥50 笔 +30 分（Right-BICEP 边界全覆盖）"""
    v = {"velocity_1h": {"count": c1, "amount": 0.0},
         "velocity_24h": {"count": c24, "amount": 0.0}}
    assert velocity_bonus(v) == expected


# ---------- tx 源信号（velocity_json 必填，DA-T-04） ----------

def test_build_tx_signal_only_on_burst():
    """无 velocity 突破不产生 tx 信号（低风险场景不放大评分）"""
    v = {"velocity_1h": {"count": 1, "amount": 800.0},
         "velocity_24h": {"count": 1, "amount": 800.0}}
    assert build_tx_signal("CASE-1", v, REASON, NOW) is None


def test_build_tx_signal_burst_carries_velocity_json():
    """SC-11 单元层：burst 信号必含与流水一致的 velocity_json"""
    v = compute_velocity(_txs(12, 50.0), NOW)
    sig = build_tx_signal("CASE-1", v, REASON, NOW)
    assert sig is not None
    assert sig["source"] == "tx" and sig["type"] == "tx_velocity_burst"
    assert sig["velocity_json"]["velocity_1h"]["count"] == 12
    assert sig["velocity_json"]["velocity_24h"]["count"] == 12
    assert math.isclose(sig["velocity_json"]["velocity_1h"]["amount"], 600.0)
    assert sig["query_reason"] == REASON
    assert 0 < sig["confidence"] <= 1.0


# ---------- 防腐层翻译（US-E3-02，AA-MCP-02 原始载荷 → DA-T-04 Schema） ----------

def test_normalize_credit_report_bands():
    base = {"source": "credit-mock", "subject_id": "a", "overdue_count_12m": 0,
            "query_reason": REASON, "degraded": False}
    high = normalize_credit_report({**base, "credit_score": 450, "risk_band": "high"}, "C", REASON, NOW)
    mid = normalize_credit_report({**base, "credit_score": 600, "risk_band": "mid"}, "C", REASON, NOW)
    low = normalize_credit_report({**base, "credit_score": 750, "risk_band": "low"}, "C", REASON, NOW)
    assert len(high) == 1 and high[0]["confidence"] == 0.8 and high[0]["type"] == "credit_band_high"
    assert len(mid) == 1 and mid[0]["confidence"] == 0.45
    assert low == []


def test_normalize_credit_report_degraded_yields_nothing():
    sig = normalize_credit_report({"code": "E-TIMEOUT", "degraded": True}, "C", REASON, NOW)
    assert sig == []


def test_normalize_sentiment_hits():
    payload = {"hits": [{"title": "x", "sentiment": "negative", "confidence": 0.7}], "degraded": False}
    sigs = normalize_sentiment(payload, "C", REASON, NOW)
    assert len(sigs) == 1 and sigs[0]["type"] == "sentiment_negative"
    assert sigs[0]["confidence"] == 0.7 and sigs[0]["source"] == "sentiment"
    assert normalize_sentiment({"hits": [], "degraded": False}, "C", REASON, NOW) == []


def test_normalize_complaints_deny_transaction():
    payload = {"items": [{"type": "deny_transaction", "content": "否认交易", "channel": "phone"}],
               "degraded": False}
    sigs = normalize_complaints(payload, "C", REASON, NOW)
    assert len(sigs) == 1 and sigs[0]["type"] == "complaint_deny_transaction"
    assert sigs[0]["confidence"] == 0.9 and sigs[0]["source"] == "complaint"
    assert normalize_complaints({"items": [], "degraded": False}, "C", REASON, NOW) == []


# ---------- 降噪合并（AA-SK-01 步骤 3） ----------

def _sig(source, type_, conf, ts):
    return {"source": source, "type": type_, "confidence": conf, "raw_ref": "r",
            "query_reason": REASON, "degraded": False, "velocity_json": None, "ts": ts}


def test_dedupe_merges_same_source_type_within_hour():
    # 降噪按整点小时桶合并（AA-SK-01 步骤 3）：12:30 与 12:50 同桶
    sigs = [_sig("complaint", "complaint_deny_transaction", 0.6, NOW.replace(minute=50)),
            _sig("complaint", "complaint_deny_transaction", 0.9, NOW.replace(minute=30))]
    merged = dedupe_signals(sigs)
    assert len(merged) == 1
    assert merged[0]["confidence"] == 0.9          # severity 取 max
    assert "2" in merged[0]["raw_ref"]             # count 累计留痕


def test_dedupe_keeps_different_hours_and_types():
    sigs = [_sig("complaint", "complaint_deny_transaction", 0.9, NOW),
            _sig("complaint", "complaint_deny_transaction", 0.9, NOW - timedelta(hours=2)),
            _sig("sentiment", "sentiment_negative", 0.5, NOW)]
    assert len(dedupe_signals(sigs)) == 3


# ---------- 加权评分（AA-SK-01 步骤 5，权重 tx0.4/credit0.2/complaint0.25/sentiment0.15） ----------

def test_score_weights_exact():
    sigs = [_sig("complaint", "complaint_deny_transaction", 0.9, NOW)]  # 0.9×0.25×100=22.5→23
    assert score_signals(sigs, ZERO_VELOCITY) == 23


def test_score_includes_velocity_bonus():
    """SC-11：同等信号下，velocity 突破场景分数高出至少 30"""
    sigs = [_sig("tx", "tx_velocity_burst", 0.6, NOW)]
    hot = {"velocity_1h": {"count": 12, "amount": 600.0},
           "velocity_24h": {"count": 12, "amount": 600.0}}
    cold = {"velocity_1h": {"count": 2, "amount": 100.0},
            "velocity_24h": {"count": 2, "amount": 100.0}}
    assert score_signals(sigs, hot) >= score_signals(sigs, cold) + VELOCITY_BONUS


def test_score_capped_at_100():
    sigs = [_sig(s, f"{s}_x", 1.0, NOW) for s in SOURCE_WEIGHTS]
    hot = {"velocity_1h": {"count": 99, "amount": 1.0},
           "velocity_24h": {"count": 999, "amount": 1.0}}
    assert score_signals(sigs, hot) == 100


def test_score_empty_signals_zero():
    assert score_signals([], ZERO_VELOCITY) == 0


# ---------- 分级裁决（US-E3-04，BA-BR-01 边界） ----------

@pytest.mark.parametrize("score,amount,n_signals,route", [
    (25, 800.0, 1, "auto_release"),          # SC-01：低风险小额自动放行
    (39, 4999.9, 1, "auto_release"),         # 双边界内侧
    (40, 800.0, 1, "investigate"),           # 风险分边界（BA-BR-01 中风险分段）
    (39, 5000.0, 1, "investigate"),          # 金额边界（≥5000 不得自动）
    (82, 100.0, 1, "investigate"),           # 高风险（SC-02 前置）
    (0, 0.0, 0, "noise"),                    # 零信号降噪
])
def test_triage_routes(score, amount, n_signals, route):
    sigs = [_sig("complaint", "t", 0.5, NOW)] * n_signals
    assert triage(score, amount, sigs) == route


def test_triage_thresholds_traceable():
    """阈值与 BA-BR-01 可追溯对账"""
    assert AUTO_SCORE_MAX == 40 and AUTO_AMOUNT_MAX == 5000
    assert VELOCITY_BONUS == 30
    assert SOURCE_WEIGHTS == {"tx": 0.4, "credit": 0.2, "complaint": 0.25, "sentiment": 0.15}
