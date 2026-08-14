# -*- coding: utf-8 -*-
"""BA-BR-05 高频异常规则专项测试（docs/06 §4 完备性声明承载）

规则：同 subject_ref 近 N 天风险事件 ≥M 次（排除当前案件，缺省 7 天/3 次）
→ 追加 internal/high_freq_case 信号参与评分；阈值经 ConfigService 热加载
（br-05-window-days / br-05-case-count）。internal 信号仅参与评分与响应，
不落 DA-T-04（risk_signal.source CHECK 白名单，01-schema.sql）。
"""
import hashlib
import uuid
from types import SimpleNamespace

from conftest import FakeExternal


def _subject(tag: str) -> str:
    return hashlib.sha256(f"{tag}-{uuid.uuid4().hex}".encode()).hexdigest()[:64]


async def _register_history(repo, subject: str, n: int) -> None:
    """同主体播种 n 条历史风险事件（risk_case 行即计数依据）"""
    for _ in range(n):
        await repo.register(subject, risk_score=30, source_type="TEST")


def _internal_signals(result: dict) -> list[dict]:
    return [s for s in result["signals"] if s["source"] == "internal"]


async def test_br05_hit_appends_internal_signal(aggregation):
    """命中：近 7 天同主体 3 条历史案件 → internal/high_freq_case 信号参与评分"""
    svc, repo, _ = aggregation
    svc.external = FakeExternal(complaint_items=0)   # 隔离外部信号，聚焦 BR-05
    subject = _subject("br05-hit")
    await _register_history(repo, subject, 3)
    reg = await repo.register(subject, risk_score=50, source_type="TEST")

    result = await svc.run(reg["case_id"])

    hits = _internal_signals(result)
    assert len(hits) == 1, "3 条历史案件应命中 BR-05 追加高频异常信号"
    assert hits[0]["type"] == "high_freq_case"
    assert "BA-BR-05" in hits[0]["query_reason"]
    assert result["risk_score"] > 0                  # internal 权重 0.25 参与评分
    # internal 不落 DA-T-04（CHECK 白名单）：库内该案信号不含 internal 源
    db_sources = {s["source"] for s in await repo.signals(reg["case_id"])}
    assert "internal" not in db_sources


async def test_br05_below_threshold_no_signal(aggregation):
    """未命中：历史案件 2 条 < 缺省阈值 3 → 无 internal 信号，零信号降噪"""
    svc, repo, _ = aggregation
    svc.external = FakeExternal(complaint_items=0)
    subject = _subject("br05-miss")
    await _register_history(repo, subject, 2)
    reg = await repo.register(subject, risk_score=50, source_type="TEST")

    result = await svc.run(reg["case_id"])

    assert _internal_signals(result) == []
    assert result["route"] == "noise"                # BR-05 未命中不放大评分


async def test_br05_count_threshold_configurable(aggregation):
    """阈值可配置：br-05-case-count 降为 2 后，同样 2 条历史案件即命中"""
    svc, repo, _ = aggregation
    svc.external = FakeExternal(complaint_items=0)
    svc.config = SimpleNamespace(values={"br-05-case-count": "2"})   # ConfigService 替身
    subject = _subject("br05-cfg")
    await _register_history(repo, subject, 2)
    reg = await repo.register(subject, risk_score=50, source_type="TEST")

    result = await svc.run(reg["case_id"])

    hits = _internal_signals(result)
    assert len(hits) == 1 and hits[0]["type"] == "high_freq_case"


async def test_br05_window_days_configurable(aggregation, pool):
    """窗口可配置：3 条历史案件回溯至 10 天前——缺省 7 天窗口不命中，
    br-05-window-days=30 后命中"""
    svc, repo, _ = aggregation
    svc.external = FakeExternal(complaint_items=0)
    subject = _subject("br05-window")
    await _register_history(repo, subject, 3)
    await pool.execute(                              # tg_web UPDATE risk_case 回溯建案时间
        """UPDATE risk_case SET created_at = now() - interval '10 days'
           WHERE subject_ref=$1""", subject)
    reg = await repo.register(subject, risk_score=50, source_type="TEST")

    miss = await svc.run(reg["case_id"])
    assert _internal_signals(miss) == []             # 7 天窗口外不命中

    svc.config = SimpleNamespace(values={"br-05-window-days": "30"})
    reg2 = await repo.register(subject, risk_score=50, source_type="TEST")
    hit = await svc.run(reg2["case_id"])
    assert len(_internal_signals(hit)) == 1          # 30 天窗口命中
