# -*- coding: utf-8 -*-
"""DA-T-12 agent_memory 落地摘要写入（03 §4 写/读时机；US-E7 观测延伸）

四技能（AA-SK-01/02/03/04）执行完毕后经 mcp-core record_agent_memory
（API-M-14，tg_app 为 agent_memory 唯一 INSERT 角色）写入执行摘要；
stage 合入 summary JSON（DA-T-12 无 stage 列）；写入失败仅告警不阻断主流程。
"""
import json
import uuid

from app.core.state_machine import CaseEvent
from app.skills.mcp_adapters import CoreClient
from conftest import FakeExternal


def _subject() -> str:
    return uuid.uuid4().hex


async def _memories(pool, case_id: str, stage: str) -> list[tuple[str, dict]]:
    """按 case + stage 取 agent_memory 摘要（summary 为 JSON 文本，含 stage 键）"""
    rows = await pool.fetch(
        "SELECT agent_id, summary FROM agent_memory WHERE case_id=$1 ORDER BY ts", case_id)
    out = []
    for r in rows:
        s = json.loads(r["summary"])
        if s.get("stage") == stage:
            out.append((r["agent_id"], s))
    return out


async def test_sk01_aggregation_writes_memory(aggregation, pool):
    """AA-SK-01：聚合完成后写 stage=aggregation 摘要（route/risk_score 在场）"""
    svc, repo, _ = aggregation
    svc.external = FakeExternal(complaint_items=0)
    reg = await repo.register(_subject(), risk_score=50, source_type="TEST")

    result = await svc.run(reg["case_id"])

    mems = await _memories(pool, reg["case_id"], "aggregation")
    assert len(mems) == 1
    agent_id, summary = mems[0]
    assert agent_id == "AA-AG-02"
    assert summary["route"] == result["route"]
    assert summary["risk_score"] == result["risk_score"]


async def test_sk03_disposition_writes_memory(disposition, pool):
    """AA-SK-03：处置提交（中风险拒自动 E-DISP-SCOPE）后写 stage=disposition 摘要"""
    svc, repo, _ = disposition
    reg = await repo.register(_subject(), risk_score=50, source_type="TEST")

    out = await svc.submit(reg["case_id"], "freeze", None, f"{reg['case_id']}:freeze")

    assert out["route"] == "refused_mid_risk"
    mems = await _memories(pool, reg["case_id"], "disposition")
    assert len(mems) == 1
    agent_id, summary = mems[0]
    assert agent_id == "AA-AG-04"
    assert summary["route"] == "refused_mid_risk" and summary["action"] == "freeze"


async def test_sk02_investigation_writes_memory(investigation, pool):
    """AA-SK-02：调查完成后写 stage=investigation 摘要（pattern/case_status 在场）"""
    svc, repo, _ = investigation
    reg = await repo.register(_subject(), risk_score=55, source_type="TEST")
    case_id = reg["case_id"]
    r = await repo.transition(case_id, CaseEvent.AGGREGATION_STARTED, "agent:AA-AG-02", 0)
    await repo.transition(case_id, CaseEvent.SIGNALS_AGGREGATED, "agent:AA-AG-02", r["version"])
    await svc.core.record_case_signals(case_id, 55, [{
        "source": "tx", "type": "velocity_anomaly", "confidence": 0.8,
        "raw_ref": f"{case_id}:tx", "query_reason": "test",
        "velocity_json": {"velocity_1h": {"count": 12, "amount": 900.0},
                          "velocity_24h": {"count": 18, "amount": 1500.0}}}])

    result = await svc.run(case_id)

    mems = await _memories(pool, case_id, "investigation")
    assert len(mems) == 1
    agent_id, summary = mems[0]
    assert agent_id == "AA-AG-03"
    assert summary["pattern"] == result["hypothesis"]["pattern"]
    assert summary["case_status"] == result["case_status"]


async def test_sk04_verification_writes_memory(pool, disposition, verification):
    """AA-SK-04：核验完成后写 stage=verification 摘要（consistency_check 在场）"""
    from test_verification import _disposed_case
    svc, _, _ = verification
    case_id, exec_id = await _disposed_case(pool, disposition[0])

    out = await svc.verify(case_id, exec_id)

    mems = await _memories(pool, case_id, "verification")
    assert len(mems) == 1
    assert mems[0][1]["consistency_check"] == out["consistency_check"]


async def test_memory_write_failure_not_blocking(aggregation, monkeypatch):
    """失败不阻断：record_agent_memory 抛异常，聚合主流程仍正常闭环"""
    svc, repo, _ = aggregation
    svc.external = FakeExternal(complaint_items=0)

    async def _boom(self, *a, **kw):
        raise ConnectionError("agent_memory 通道不可用")

    monkeypatch.setattr(CoreClient, "record_agent_memory", _boom)
    reg = await repo.register(_subject(), risk_score=50, source_type="TEST")

    result = await svc.run(reg["case_id"])

    assert result["route"] == "noise"          # 主链路不受记忆写入失败影响
    assert (await repo.get(reg["case_id"]))["status"] == "ARCHIVED"
