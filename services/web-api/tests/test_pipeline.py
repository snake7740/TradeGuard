# -*- coding: utf-8 -*-
"""E3 信号聚合闭环集成测试（SC-01/SC-11 验收，全链路：web-api→MCP→PolarDB）

链路：立案（tg_web）→ AA-SK-01 内核聚合（FakeExternal 确定性外部源 + 真实流水）
→ mcp-core record_case_signals 落 DA-T-04（tg_app 写角色，DA-INV-05）
→ 分级裁决路由（自动放行/调查/降噪/全源失败）。
测试数据自播种自清理无关（case_id 唯一，只增表不回删，符合 DA-T-04/08 语义）。
"""
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.skills.aggregation import (
    VELOCITY_BONUS, ZERO_VELOCITY, AggregationStateError, score_signals,
)
from conftest import FakeExternal


def _subject(tag: str) -> str:
    return hashlib.sha256(f"{tag}-{uuid.uuid4().hex}".encode()).hexdigest()[:64]


async def _seed_txs(app_pool, subject, n, amount=50.0, minutes_ago=10):
    """以 tg_app（数据发生器写角色）播种近 1h 流水"""
    now = datetime.now(timezone.utc)
    for i in range(n):
        await app_pool.execute(
            """INSERT INTO transaction (tx_id, account_hash, amount, mcc, channel, ts)
               VALUES ($1, $2, $3, '5411', 'CNP', $4)""",
            f"tx-{uuid.uuid4().hex[:12]}", subject, amount,
            now - timedelta(minutes=minutes_ago + i))


async def test_sc11_velocity_feature_and_scoring(aggregation, app_pool):
    """SC-11：高频簇聚合产出 velocity 特征且参与评分（BA-BR-14）"""
    svc, repo, pub = aggregation
    subject = _subject("sc11")
    await _seed_txs(app_pool, subject, 12, amount=50.0)   # 近 1 小时 12 笔小额
    reg = await repo.register(subject, risk_score=50, source_type="TEST")
    svc.external = FakeExternal(complaint_items=0)        # 隔离外部信号，聚焦 velocity

    result = await svc.run(reg["case_id"])

    # Then-1：tx 源信号 velocity_json 与流水统计一致
    tx_rows = [s for s in await repo.signals(reg["case_id"]) if s["source"] == "tx"]
    assert len(tx_rows) == 1
    vj = tx_rows[0]["velocity_json"]
    if isinstance(vj, str):  # asyncpg 对 jsonb 返回 JSON 文本
        vj = json.loads(vj)
    assert vj["velocity_1h"]["count"] == 12 and vj["velocity_24h"]["count"] == 12
    assert float(vj["velocity_1h"]["amount"]) == pytest.approx(600.0)
    # Then-2：velocity 参与评分——高出无 velocity 基线至少 30 分
    assert result["velocity"]["velocity_1h"]["count"] == 12
    assert result["risk_score"] >= VELOCITY_BONUS
    base_without_velocity = score_signals(
        [dict(s, velocity_json=None) for s in result["signals"] if s["source"] != "tx"],
        ZERO_VELOCITY)
    assert result["risk_score"] >= base_without_velocity + VELOCITY_BONUS
    assert (await repo.get(reg["case_id"]))["risk_score"] == result["risk_score"]


async def test_sc01_low_risk_auto_release(aggregation, app_pool):
    """SC-01：低风险小额自动放行（风险分<40 且涉案 800<5000 → release → DISPOSED）"""
    svc, repo, pub = aggregation
    subject = _subject("sc01")
    await _seed_txs(app_pool, subject, 1, amount=800.0)   # 涉案金额 800 元
    reg = await repo.register(subject, risk_score=50, source_type="TEST")

    result = await svc.run(reg["case_id"])

    # 场景档：投诉 1 条（0.9×0.25×100=22.5→23 分，与 Gherkin 风险分 25 同属低风险档 <40）
    assert result["route"] == "auto_release"
    assert result["risk_score"] < 40
    assert result["velocity"]["velocity_24h"]["amount"] == pytest.approx(800.0)
    # Then-1：事件状态流转为"已处置"并发布 DispositionExecuted 事件
    case = await repo.get(reg["case_id"])
    assert case["status"] == "DISPOSED"
    events = [m["event"] for m in pub.published]
    assert "AggregationStarted" in events
    assert "DispositionSubmitted" in events
    assert "DispositionExecuted" in events
    # Then-2：审计含操作者=AA-AG-04、依据=风险分（BA-BR-09）
    trail = await repo.audit_trail(reg["case_id"])
    disp = [a for a in trail if a["actor"] == "AA-AG-04"
            and f"risk_score={result['risk_score']}" in (a["basis"] or "")]
    assert disp, "缺 AA-AG-04 且依据含风险分的审计记录"
    # 处置凭证落 DA-T-06（幂等键 DA-INV-03）
    assert result["exec_id"]
    row = await svc.pool.fetchrow(
        "SELECT * FROM disposition_record WHERE exec_id=$1", result["exec_id"])
    assert row["action"] == "release" and row["case_id"] == reg["case_id"]


async def test_sc01_auto_release_idempotent_rerun(aggregation, app_pool):
    """幂等守护：已处置案件不得再聚合，且不产生第二条 disposition（DA-INV-03）"""
    svc, repo, _ = aggregation
    subject = _subject("sc01-idem")
    await _seed_txs(app_pool, subject, 1, amount=800.0)
    reg = await repo.register(subject, risk_score=50, source_type="TEST")
    first = await svc.run(reg["case_id"])
    with pytest.raises(AggregationStateError):
        await svc.run(reg["case_id"])  # 已 DISPOSED，重入被拒
    n = await svc.pool.fetchval(
        "SELECT count(*) FROM disposition_record WHERE case_id=$1", reg["case_id"])
    assert first["route"] == "auto_release" and n == 1


async def test_noise_zero_signals_archived(aggregation):
    """降噪通道：零信号 → NoiseDismissed → ARCHIVED（AA-SK-01 步骤 6 出口）"""
    svc, repo, pub = aggregation
    svc.external = FakeExternal(complaint_items=0)        # 征信 low/舆情空/投诉空
    reg = await repo.register(_subject("noise"), risk_score=50, source_type="TEST")

    result = await svc.run(reg["case_id"])

    assert result["route"] == "noise" and result["signals"] == []
    assert (await repo.get(reg["case_id"]))["status"] == "ARCHIVED"
    assert pub.published[-1]["event"] == "NoiseDismissed"


async def test_mid_risk_goes_investigating(aggregation, app_pool):
    """中风险（40-69）转调查：征信高段+投诉+舆情命中叠加越 40 分界"""
    svc, repo, pub = aggregation
    subject = _subject("mid")
    await _seed_txs(app_pool, subject, 1, amount=800.0)
    svc.external = FakeExternal(
        credit_band="high", complaint_items=1,
        sentiment_hits=[{"title": "负面舆情", "sentiment": "negative", "confidence": 0.9}])
    reg = await repo.register(subject, risk_score=50, source_type="TEST")

    result = await svc.run(reg["case_id"])

    assert 40 <= result["risk_score"] < 70
    assert result["route"] == "investigate"
    assert (await repo.get(reg["case_id"]))["status"] == "INVESTIGATING"
    assert pub.published[-1]["event"] == "SignalsAggregated"


async def test_all_sources_fail_escalates(aggregation, monkeypatch):
    """全源失败 → E-AGG-ALL-FAIL 转人工（AA-SK-01 失败处理，不推进状态）"""
    svc, repo, pub = aggregation
    svc.external = FakeExternal(fail=True)

    async def _broken_tx(subject):
        raise ConnectionError("tx source unavailable")

    monkeypatch.setattr(svc, "_fetch_tx", _broken_tx)
    reg = await repo.register(_subject("allfail"), risk_score=50, source_type="TEST")

    result = await svc.run(reg["case_id"])

    assert result["route"] == "all_fail"
    assert sorted(result["degraded_sources"]) == ["complaint", "credit", "sentiment", "tx"]
    assert (await repo.get(reg["case_id"]))["status"] == "AGGREGATING"
    trail = await repo.audit_trail(reg["case_id"])
    assert any(a["action"] == "signals.all_fail" for a in trail)


async def test_ba_br07_disabled_auto_channel_blocks_auto_release(aggregation, app_pool, pool):
    """BA-BR-07：驳回回滚禁用自动通道后，同档低风险聚合不再自动放行，转调查"""
    svc, repo, pub = aggregation
    subject = _subject("br07")
    await _seed_txs(app_pool, subject, 1, amount=800.0)   # 低风险档（同 SC-01 场景）
    reg = await repo.register(subject, risk_score=50, source_type="TEST")
    await pool.execute(
        "UPDATE risk_case SET context_json=$2::jsonb WHERE case_id=$1",
        reg["case_id"], '{"auto_channel": "disabled"}')

    result = await svc.run(reg["case_id"])

    assert result["risk_score"] < 40                      # 同 SC-01 低风险档
    assert result["route"] == "investigate"               # 自动通道被禁用（BA-BR-07）
    assert (await repo.get(reg["case_id"]))["status"] == "INVESTIGATING"
