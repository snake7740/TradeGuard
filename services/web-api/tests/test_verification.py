# -*- coding: utf-8 -*-
"""E6 核验审计集成测试（US-E6-01/02 验收，AA-SK-04 全链路实链路）

覆盖：核验通过 → VerificationPassed → VERIFIED → CaseArchived → ARCHIVED +
审计报告固化 DA-T-05 + 复盘入库申请（US-E6-03 联动）；核验不一致 →
VerificationFailed → ROLLBACK → 反向处置（幂等键 :rollback）→ RollbackExecuted →
MANUAL_REVIEW + P0 审计；BA-BR-08 十分钟核验超时提醒（幂等）。
"""
import uuid

from app.core.state_machine import CaseEvent
from app.skills.verification import scan_verification_overdue


async def _subject() -> str:
    return uuid.uuid4().hex


async def _disposed_case(pool, disp_svc, score: int = 82):
    """立案→调查→门控建单→批准→执行完毕（DISPOSED），返回 (case_id, exec_id)"""
    repo = disp_svc.cases
    reg = await repo.register(await _subject(), risk_score=score, source_type="TEST")
    case_id = reg["case_id"]
    r = await repo.transition(case_id, CaseEvent.AGGREGATION_STARTED, "agent:AA-AG-02", 0)
    await repo.transition(case_id, CaseEvent.SIGNALS_AGGREGATED, "agent:AA-AG-02", r["version"])
    await disp_svc.core.record_case_evidence(
        case_id, [{"claim": "持卡人否认交易", "source_ref": "AA-AG-03:test", "confidence": 0.9}])
    gate = await disp_svc.submit(case_id, "freeze", None, f"{case_id}:freeze")
    approved = await disp_svc.approve(gate["approval_id"], "human:approver", "同意")
    assert approved["route"] == "executed"
    return case_id, approved["exec_id"]


async def _audit_actions(pool, case_id: str) -> list[str]:
    rows = await pool.fetch(
        "SELECT action FROM audit_log WHERE target=$1 OR basis LIKE '%'||$1||'%'", case_id)
    return [r["action"] for r in rows]


# ---------- 核验通过 → 归档 + 复盘入库申请 ----------

async def test_verification_passed_chain(pool, disposition, verification):
    """一致 → VERIFIED → ARCHIVED；审计报告落 DA-T-05；复盘摘要提 kb 申请（pending）"""
    svc, repo, pub = verification
    case_id, exec_id = await _disposed_case(pool, disposition[0])

    out = await svc.verify(case_id, exec_id)

    assert out["consistency_check"] is True and out["trace_complete"] is True
    case = await repo.get(case_id)
    assert case["status"] == "ARCHIVED"                        # VERIFIED → CaseArchived
    events = [e["event"] for e in pub.published]
    assert "VerificationPassed" in events and "CaseArchived" in events
    evs = await pool.fetch(
        "SELECT * FROM case_evidence WHERE case_id=$1 AND claim LIKE '审计报告%'", case_id)
    assert len(evs) == 1                                       # AA-SK-04 步骤 5：报告落 DA-T-05
    kb = await pool.fetchrow("SELECT * FROM kb_document WHERE content LIKE '%'||$1||'%'", case_id)
    assert kb and kb["status"] == "pending"                    # US-E6-03 复盘入库申请（人工门控前置）


# ---------- 核验不一致 → 反向处置 + P0 升级 ----------

async def test_verification_failed_rollback_and_p0(pool, app_pool, disposition, verification):
    """实际状态与凭证不一致 → VerificationFailed → 反向处置 → MANUAL_REVIEW + P0 审计"""
    svc, repo, pub = verification
    case_id, exec_id = await _disposed_case(pool, disposition[0])
    # 篡改执行结果模拟不一致（tg_app 写角色；生产为下游系统回报异常）
    await app_pool.execute(
        "UPDATE disposition_record SET status='failed' WHERE exec_id=$1", exec_id)

    out = await svc.verify(case_id, exec_id)

    assert out["consistency_check"] is False
    case = await repo.get(case_id)
    assert case["status"] == "MANUAL_REVIEW"                   # RollbackExecuted 后转人工
    events = [e["event"] for e in pub.published]
    assert "VerificationFailed" in events and "RollbackExecuted" in events
    rows = await pool.fetch(
        "SELECT * FROM disposition_record WHERE case_id=$1 ORDER BY ts", case_id)
    assert len(rows) == 2                                      # 原处置 + 反向处置
    rollback = rows[1]
    assert rollback["action"] == "release" and rollback["status"] == "executed"
    assert rollback["idempotency_key"].endswith(":rollback")   # AA-SK-04 反向处置幂等键后缀
    assert "verification.p0" in await _audit_actions(pool, case_id)


# ---------- BA-BR-08 十分钟核验超时提醒 ----------

async def test_ba_br08_overdue_reminder_idempotent(pool, disposition, verification):
    """DISPOSED 超 10 分钟未核验 → 审计提醒；二次扫描幂等不重复"""
    _, _, pub = verification
    case_id, _ = await _disposed_case(pool, disposition[0])
    await pool.execute(
        "UPDATE risk_case SET updated_at=now() - interval '11 minutes' WHERE case_id=$1", case_id)

    first = await scan_verification_overdue(pool, pub, minutes=10)
    second = await scan_verification_overdue(pool, pub, minutes=10)

    assert any(r["case_id"] == case_id for r in first)
    assert second == []                                        # 已提醒不重复（幂等）
    actions = await _audit_actions(pool, case_id)
    assert actions.count("verification.overdue") == 1
