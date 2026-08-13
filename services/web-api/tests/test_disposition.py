# -*- coding: utf-8 -*-
"""E5 处置执行与审批回滚集成测试（SC-02/03/07/09/10 验收，全链路实链路）

链路：立案（tg_web）→ 状态迁移至 INVESTIGATING → AA-SK-03 确定性内核
（app/skills/disposition.py）→ mcp-core execute_disposition（审批门控 DA-INV-02 /
幂等 DA-INV-03，tg_app 写角色 DA-INV-05）→ 审批工单 API-M-11 → 批准/驳回编排。
US-E5-06 状态机守护与乐观锁由 test_state_machine.py / test_repositories.py 承载。
"""
import uuid

from app.core.state_machine import CaseEvent
from app.skills.disposition import scan_pending_escalations


async def _subject() -> str:
    return uuid.uuid4().hex


async def _investigating_case(repo, score: int) -> str:
    """立案并推进至 INVESTIGATING（聚合完成入调查的合法路径）"""
    reg = await repo.register(await _subject(), risk_score=score, source_type="TEST")
    case_id = reg["case_id"]
    r = await repo.transition(case_id, CaseEvent.AGGREGATION_STARTED, "agent:AA-AG-02", 0)
    await repo.transition(case_id, CaseEvent.SIGNALS_AGGREGATED, "agent:AA-AG-02", r["version"])
    return case_id


async def _with_evidence(svc, case_id: str):
    """DA-INV-04 前置：冻结须附证据链（BA-BR-03），经 API-M-12 固化（tg_app 写角色）"""
    out = await svc.core.record_case_evidence(
        case_id, [{"claim": "持卡人否认交易且设备指纹关联多账户",
                   "source_ref": "AA-AG-03:test-evidence", "confidence": 0.9}])
    assert out["recorded"] >= 1


async def _audit_actions(pool, case_id: str) -> list[str]:
    rows = await pool.fetch(
        "SELECT action FROM audit_log WHERE target=$1 OR basis LIKE '%'||$1||'%'", case_id)
    return [r["action"] for r in rows]


async def _disposition_rows(pool, case_id: str):
    return await pool.fetch("SELECT * FROM disposition_record WHERE case_id=$1", case_id)


# ---------- SC-07 处置幂等（DA-INV-03，US-E5-01） ----------

async def test_sc07_disposition_idempotent_replay(pool, disposition):
    """SC-07：相同幂等键重投返回首次执行凭证，不产生第二条 disposition 记录"""
    svc, repo, pub = disposition
    case_id = await _investigating_case(repo, score=82)
    await _with_evidence(svc, case_id)
    # 高风险冻结需审批凭证：先建单并经人类批准（走合法链取得 approval_ref）
    gate = await svc.submit(case_id, "freeze", None, f"{case_id}:freeze")
    assert gate["route"] == "approval_required" and gate["code"] == "E-DISP-AUTH"
    approved = await svc.approve(gate["approval_id"], "human:approver", "同意冻结")
    assert approved["route"] == "executed"

    # 消息重投：与批准执行相同的幂等键再次提交（approve 键构造：case:action:approval，DA-INV-03）
    replay = await svc.core.execute_disposition(
        case_id, "freeze", None, f"{case_id}:freeze:{gate['approval_id']}",
        approval_ref=gate["approval_id"])
    assert replay["code"] == "E-IDEMPOTENT-CONFLICT"
    assert replay["first_result"]["exec_id"] == approved["exec_id"]

    rows = await _disposition_rows(pool, case_id)
    assert len(rows) == 1                                   # DA-INV-03：不重复执行
    assert rows[0]["status"] == "executed"
    assert rows[0]["approval_ref"] == gate["approval_id"]


# ---------- SC-02 高风险强制人工审批（DA-INV-02，US-E5-02/03） ----------

async def test_sc02_high_risk_gate_and_full_approval_chain(pool, disposition):
    """SC-02：风险分 82 无凭证冻结被拒 E-DISP-AUTH → 建单 → 批准 → 冻结成功关联落库"""
    svc, repo, pub = disposition
    case_id = await _investigating_case(repo, score=82)
    await _with_evidence(svc, case_id)

    # When：不带 approval_ref 执行冻结
    gate = await svc.submit(case_id, "freeze", None, f"{case_id}:freeze")

    # Then-1：拒绝 + 错误码 + 生成审批工单 + 事件进入待审批
    assert gate["route"] == "approval_required" and gate["code"] == "E-DISP-AUTH"
    rec = await pool.fetchrow("SELECT * FROM approval_record WHERE approval_id=$1",
                              gate["approval_id"])
    assert rec["decision"] == "pending" and rec["requested_action"] == "freeze"
    assert (await repo.get(case_id))["status"] == "PENDING_APPROVAL"
    assert await _disposition_rows(pool, case_id) == []    # 未产生处置记录

    # When：审批官批准并回填
    result = await svc.approve(gate["approval_id"], "human:approver", "证据充分，同意冻结")

    # Then-2：冻结执行成功，审批记录与执行凭证关联落库
    assert result["route"] == "executed"
    case = await repo.get(case_id)
    assert case["status"] == "DISPOSED"
    rows = await _disposition_rows(pool, case_id)
    assert len(rows) == 1 and rows[0]["approval_ref"] == gate["approval_id"]
    assert rows[0]["status"] == "executed"
    events = [e["event"] for e in pub.published]
    assert "ApprovalApproved" in events and "DispositionExecuted" in events


# ---------- SC-03 审批驳回回滚（BA-BR-07，US-E5-03） ----------

async def test_sc03_reject_rolls_back_to_manual_review(pool, disposition):
    """SC-03：驳回→回退人工复核且自动通道禁用，ApprovalRejected 发布、意见留痕"""
    svc, repo, pub = disposition
    case_id = await _investigating_case(repo, score=82)
    await _with_evidence(svc, case_id)
    gate = await svc.submit(case_id, "freeze", None, f"{case_id}:freeze")

    await svc.reject(gate["approval_id"], "human:approver", "证据不足，驳回")

    case = await repo.get(case_id)
    assert case["status"] == "MANUAL_REVIEW"               # 回退人工复核（BA-BR-07）
    assert case["context_json"].get("auto_channel") == "disabled"  # 禁止再次进入自动通道
    rec = await pool.fetchrow("SELECT * FROM approval_record WHERE approval_id=$1",
                              gate["approval_id"])
    assert rec["decision"] == "rejected" and rec["opinion"] == "证据不足，驳回"
    events = [e["event"] for e in pub.published]
    assert "ApprovalRejected" in events
    assert await _disposition_rows(pool, case_id) == []    # 驳回不产生处置
    audit = await _audit_actions(pool, case_id)
    assert "approval.decide" in audit


# ---------- SC-10 中风险禁止自动处置（BA-BR-01 分段，US-E5-04） ----------

async def test_sc10_mid_risk_auto_disposition_refused(pool, disposition):
    """SC-10：风险分 55 自动放行被拒，不产生 disposition 记录，仅审计留痕"""
    svc, repo, pub = disposition
    case_id = await _investigating_case(repo, score=55)

    result = await svc.submit(case_id, "release", None, f"{case_id}:release")

    assert result["route"] == "refused_mid_risk" and result["code"] == "E-DISP-SCOPE"
    assert await _disposition_rows(pool, case_id) == []    # 不产生处置记录
    audit = await _audit_actions(pool, case_id)
    assert "disposition.refused" in audit                  # 仅审计留痕
    # 事件留在人工处理队列（中风险一律转人工复核，BA-BR-01），不自动流转
    assert (await repo.get(case_id))["status"] == "INVESTIGATING"


# ---------- SC-09 审批时效升级（BA-BR-13，US-E5-05） ----------

async def test_sc09_approval_timeout_escalates(pool, disposition):
    """SC-09：待审批工单超 30 分钟 → 升级标记 + 审计 + ApprovalEscalated 事件，不重复升级"""
    svc, repo, pub = disposition
    case_id = await _investigating_case(repo, score=82)
    await _with_evidence(svc, case_id)
    gate = await svc.submit(case_id, "freeze", None, f"{case_id}:freeze")
    # 模拟工单已滞留 31 分钟（BA-BR-13 阈值 30 分钟）
    await pool.execute(
        "UPDATE approval_record SET created_at=now() - interval '31 minutes' WHERE approval_id=$1",
        gate["approval_id"])

    escalated = await scan_pending_escalations(pool, pub, minutes=30)

    assert [r["approval_id"] for r in escalated] == [gate["approval_id"]]
    rec = await pool.fetchrow("SELECT escalated_at FROM approval_record WHERE approval_id=$1",
                              gate["approval_id"])
    assert rec["escalated_at"] is not None                 # 门户标红依据（API-W-08 返回）
    audit = await _audit_actions(pool, case_id)
    assert "approval.escalate" in audit                    # 升级动作写入审计（BA-BR-09）
    assert "ApprovalEscalated" in [e["event"] for e in pub.published]
    # 幂等：再次扫描不重复升级
    assert await scan_pending_escalations(pool, pub, minutes=30) == []
