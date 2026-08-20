# -*- coding: utf-8 -*-
"""A1 自适应基线 + B2 时序三模式单元测试（docs/14 §4 单元层，US-E8）

覆盖：EWMA/分位计算（compute_baseline）、偏离度与双轨加分（BA-BR-15）、
资金回路/快进快出/夜间突发三模式匹配（BA-BR-17）、effectiveness 统计（E1）。
纯函数无 IO，SC-12/SC-14 的算法侧取证（场景级见 test_scenario_matrix）。
"""
from datetime import datetime, timedelta, timezone
from typing import Any

from app.skills.aggregation import (
    BASELINE_ALPHA, BASELINE_DEV_BONUS, BASELINE_DEV_RATIO, BASELINE_MIN_TX,
    baseline_bonus, baseline_deviation, compute_baseline, ewma,
    match_fund_loop, match_night_burst, match_rapid_inout, percentile,
    temporal_bonus,
)
from app.skills.knowledge import effectiveness

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _tx(amount: float, ts: datetime) -> dict[str, Any]:
    return {"amount": amount, "ts": ts}


# ---------- EWMA / 分位 / 基线聚合（DA-T-14 纯计算） ----------

def test_ewma_cold_start_and_smoothing():
    assert ewma(0, 100.0) == 100.0                     # 无历史取首笔观测
    nxt = ewma(100.0, 200.0)
    assert abs(nxt - (100 + BASELINE_ALPHA * 100)) < 1e-9  # 指数加权收敛方向正确
    assert 100.0 < nxt < 200.0


def test_percentile_interpolation():
    assert percentile([]) == 0.0
    assert percentile([7.0]) == 7.0
    vals = [float(i) for i in range(1, 101)]           # 1..100
    assert abs(percentile(vals, 0.95) - 95.05) < 1e-6  # 线性插值
    assert percentile([3.0, 1.0, 2.0], 0.0) == 1.0


def test_compute_baseline_shape():
    txs = [_tx(100.0, NOW.replace(hour=h) - timedelta(days=1)) for h in range(24)]
    b = compute_baseline(txs)
    assert b["tx_count"] == 24
    assert b["ewma_amount"] > 0 and len(b["hour_histogram"]) == 24
    assert sum(b["hour_histogram"]) == 24
    assert b["p95_amount"] == 100.0


def test_baseline_deviation_cold_start_none():
    txs = [_tx(900.0, NOW)]
    assert baseline_deviation(None, txs) is None                       # 无基线回退全局阈值
    assert baseline_deviation({"tx_count": BASELINE_MIN_TX, "ewma_amount": 100.0}, []) is None
    cold = {"tx_count": BASELINE_MIN_TX - 1, "ewma_amount": 100.0}
    assert baseline_deviation(cold, txs) is None                       # 样本不足冷启动
    assert baseline_deviation({"tx_count": 30, "ewma_amount": 0.0}, txs) is None


def test_baseline_deviation_ratio_and_bonus():
    base = {"tx_count": 30, "ewma_amount": 100.0}
    dev = baseline_deviation(base, [_tx(800.0, NOW)])                  # 笔均 8 倍
    assert dev is not None and abs(dev - 8.0) < 1e-9
    assert baseline_bonus(dev) == BASELINE_DEV_BONUS                   # SC-12：≥3 倍入中通道
    assert baseline_bonus(BASELINE_DEV_RATIO - 0.01) == 0
    assert baseline_bonus(None) == 0                                   # 双轨：缺失不加分不阻断


# ---------- B2 三时序模式（BA-BR-17） ----------

def test_match_fund_loop_closed_ring():
    # A→B→C→A 90 分钟内闭环（SC-14）
    edges = [("A", "B", NOW - timedelta(minutes=30)),
             ("B", "C", NOW - timedelta(minutes=20)),
             ("C", "A", NOW - timedelta(minutes=10))]
    assert match_fund_loop(edges, "A", NOW) is True


def test_match_fund_loop_window_and_self_loop_negative():
    edges_in = [("A", "B", NOW - timedelta(minutes=30)),
                ("B", "C", NOW - timedelta(minutes=20)),
                ("C", "A", NOW - timedelta(minutes=10))]
    # 闭环最后一跳落在 90 分钟窗口外 → 不命中
    edges_out = [("A", "B", NOW - timedelta(minutes=120)),
                 ("B", "C", NOW - timedelta(minutes=110)),
                 ("C", "A", NOW - timedelta(minutes=100))]
    assert match_fund_loop(edges_out, "A", NOW) is False
    # 自环/一级收款方回流不算闭环
    edges_self = [("A", "B", NOW - timedelta(minutes=20)),
                  ("B", "A", NOW - timedelta(minutes=10))]
    assert match_fund_loop(edges_self, "A", NOW) is False
    assert match_fund_loop(edges_in, "D", NOW) is False                # 非主体无关链


def test_match_rapid_inout_pass_through():
    inc = [_tx(1000.0, NOW - timedelta(minutes=60 - i)) for i in range(4)]
    out = [_tx(990.0, NOW - timedelta(minutes=55 - i)) for i in range(4)]
    assert match_rapid_inout(inc, out, NOW) is True                    # 总额比≈0.99 且过账及时
    # 总额比失衡（截留型非过账）→ 不命中
    out_low = [_tx(300.0, NOW - timedelta(minutes=55 - i)) for i in range(4)]
    assert match_rapid_inout(inc, out_low, NOW) is False
    # 笔数不足 → 不命中
    assert match_rapid_inout(inc[:2], out, NOW) is False


def test_match_night_burst_absolute_and_relative():
    night = [_tx(50.0, NOW.replace(hour=2) - timedelta(minutes=i)) for i in range(5)]
    assert match_night_burst(night, None, NOW.replace(hour=3)) is True  # 绝对突发 ≥5 无基线兜底
    # 相对突发：历史夜间占比 <10%，当夜 3 笔即命中
    hist = [0] * 24
    hist[12] = 100                                                     # 历史全在日间
    three = [_tx(50.0, NOW.replace(hour=1) - timedelta(minutes=i)) for i in range(3)]
    assert match_night_burst(three, hist, NOW.replace(hour=2)) is True
    # 夜间本就活跃（占比高）且不足 5 笔 → 非突发
    hist_night = [10] * 6 + [0] * 18
    assert match_night_burst(three, hist_night, NOW.replace(hour=2)) is False
    assert match_night_burst([], None, NOW) is False


def test_temporal_bonus_sum():
    assert temporal_bonus({}) == 0
    hit = {"fund_loop": True, "rapid_inout": True, "night_burst": True}
    assert temporal_bonus(hit) == 20 + 15 + 10
    assert temporal_bonus({"fund_loop": True, "rapid_inout": False}) == 20


# ---------- E1 effectiveness 统计（BA-BR-20 降级依据同源） ----------

def test_effectiveness_zero_citation_and_clamp():
    assert effectiveness(0, 5) == 0.0          # 零引用未经验证不得给高分
    assert effectiveness(4, 3) == 0.75
    assert effectiveness(2, 9) == 1.0          # 上界钳制
    assert effectiveness(3, -1) == 0.0         # 下界钳制
