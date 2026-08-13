# -*- coding: utf-8 -*-
"""Sprint 7 场景矩阵：SC-01~SC-11 紧凑 E2E（决赛出口"11/11 场景通过"的单一取证文件）

每个 SC 一条测试，聚焦 06 §2 Gherkin 的 Then 核心断言（细分支由专题测试文件
test_pipeline / test_disposition / test_verification / test_knowledge 承载）。
Sprint 7 新增重点：SC-04 黑名单聚合裁决（BA-BR-04）与 SC-06 阈值热更（US-E1-03），
SC-08 增加技能执行 span 回放断言（US-E7-04 可观测并入留痕完整率取证）。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config_service import ConfigService
from app.core.state_machine import CaseEvent
from app.core.tracing import recent_spans
from app.skills.disposition import scan_pending_escalations
from app.skills.knowledge import publish_and_index, search_kb
from conftest import FakeExternal


async def _subject() -> str:
    return uuid.uuid4().hex


async def _seed_tx(app_pool, subject, n=1, amount=800.0):
    now = datetime.now(timezone.utc)
    for i in range(n):
        await app_pool.execute(
            """INSERT INTO transaction (tx_id, account_hash, amount, mcc, channel, ts)
               VALUES ($1, $2, $3, '5411', 'CNP', $4)""",
            f"tx-{uuid.uuid4().hex[:12]}", subject, amount, now - timedelta(minutes=5 + i))


async def _investigating_case(repo, score: int) -> str:
    """立案并推进至 INVESTIGATING（聚合完成入调查的合法路径）"""
    reg = await repo.register(await _subject(), risk_score=score, source_type="TEST")
    r = await repo.transition(reg["case_id"], CaseEvent.AGGREGATION_STARTED, "agent:AA-AG-02", 0)
    await repo.transition(reg["case_id"], CaseEvent.SIGNALS_AGGREGATED,
                          "agent:AA-AG-02", r["version"])
    return reg["case_id"]


async def _with_evidence(svc, case_id: str):
    """DA-INV-04 前置：冻结类处置须附证据链（BA-BR-03）"""
    await svc.core.record_case_evidence(
        case_id, [{"claim": "场景矩阵：证据链固化", "source_ref": "AA-AG-03:matrix",
                   "confidence": 0.9}])


async def _audit_actions(pool, case_id: str) -> list[str]:
    rows = await pool.fetch(
        "SELECT action FROM audit_log WHERE target=$1 OR basis LIKE '%'||$1||'%'", case_id)
    return [r["action"] for r in rows]


# ---------- SC-01 低风险自动放行（BA-BR-01/BA-CAP-05） ----------

async def test_matrix_sc01_auto_release(aggregation, app_pool):
    svc, repo, pub = aggregation
    subject = await _subject()
    await _seed_tx(app_pool, subject, amount=800.0)
    result = await svc.run((await repo.register(subject, risk_score=50,
                                                 source_type="TEST"))["case_id"]
                           if False else (reg := await repo.register(subject, risk_score=50,
                                                                     source_type="TEST"))["case_id"])
    assert result["route"] == "auto_release" and result["risk_score"] < 40
    assert result["status"] == "DISPOSED"
    assert "DispositionExecuted" in [m["event"] for m in pub.published]


# ---------- SC-02 高风险强制人工审批（DA-INV-02，BA-BR-02） ----------

async def test_matrix_sc02_high_risk_approval_gate(pool, disposition):
    svc, repo, pub = disposition
    case_id = await _investigating_case(repo, score=82)
    await _with_evidence(svc, case_id)
    gate = await svc.submit(case_id, "freeze", None, f"{case_id}:freeze")
    assert gate["route"] == "approval_required" and gate["code"] == "E-DISP-AUTH"
    assert (await repo.get(case_id))["status"] == "PENDING_APPROVAL"
    approved = await svc.approve(gate["approval_id"], "human:approver", "同意")
    assert approved["route"] == "executed"
    assert (await repo.get(case_id))["status"] == "DISPOSED"


# ---------- SC-03 审批驳回回滚（BA-BR-07） ----------

async def test_matrix_sc03_reject_rollback(pool, disposition):
    svc, repo, pub = disposition
    case_id = await _investigating_case(repo, score=82)
    await _with_evidence(svc, case_id)
    gate = await svc.submit(case_id, "freeze", None, f"{case_id}:freeze")
    await svc.reject(gate["approval_id"], "human:approver", "证据不足")
    case = await repo.get(case_id)
    assert case["status"] == "MANUAL_REVIEW"
    assert case["context_json"].get("auto_channel") == "disabled"


# ---------- SC-04 黑名单立案即高风险（BA-BR-04，Sprint 7 新补） ----------

async def test_matrix_sc04_black_flag_direct_high_risk(aggregation, app_pool, pool):
    """Given list_flag=black → Then 风险分≥75 + 处置建议=block + 无论金额进人工审批通道"""
    svc, repo, pub = aggregation
    subject = (await _subject()).ljust(64)
    # 黑名单主体建档（tg_app 为 account 唯一写角色，02-roles.sql）
    await app_pool.execute(
        "INSERT INTO account (account_hash, risk_level, list_flag) VALUES ($1, 3, 'black')",
        subject)
    await _seed_tx(app_pool, subject.strip(), amount=800.0)   # 低风险金额档，验证"无论金额"
    reg = await repo.register(subject.strip(), risk_score=50, source_type="TEST")

    result = await svc.run(reg["case_id"])

    assert result["risk_score"] >= 75                          # 垫高至 ≥BA-BR-02 审批线
    assert result["recommended_action"] == "block"             # 处置建议拦截
    assert result["route"] == "investigate"                    # 入人工通道（不得降噪/自动放行）
    assert "signals.black_flag" in await _audit_actions(pool, reg["case_id"])
    # 处置通道：block 无凭证被 E-DISP-AUTH 门控，建单转人工审批（BA-BR-02）
    from app.skills.disposition import DispositionService
    from app.skills.mcp_adapters import CoreClient
    from conftest import MCP_CORE_URL, RecordingPublisher
    disp = DispositionService(pool=pool, cases=repo, core=CoreClient(MCP_CORE_URL),
                              pub=RecordingPublisher())
    gate = await disp.submit(reg["case_id"], "block", None, f"{reg['case_id']}:block")
    assert gate["route"] == "approval_required" and gate["code"] == "E-DISP-AUTH"
    assert (await repo.get(reg["case_id"]))["status"] == "PENDING_APPROVAL"


# ---------- SC-05 知识入库人工门控（DA-INV-06） ----------

async def test_matrix_sc05_kb_human_gate(pool, verification):
    svc = verification[0]
    pattern = f"矩阵复盘手法-{uuid.uuid4().hex[:6]}"
    out = await svc.core.submit_kb_application(
        f"CASE-MTX-{uuid.uuid4().hex[:8]}", "case", "矩阵复盘", f"复盘摘要：{pattern}")
    assert out["status"] == "pending"
    assert all(h["doc_id"] != out["doc_id"] for h in await search_kb(pool, pattern))
    res = await publish_and_index(pool, out["doc_id"], operator="human:strategist",
                                  comment="确认发布")
    assert res["status"] == "published" and res["chunks"] >= 1
    hits = await search_kb(pool, pattern)
    assert hits and hits[0]["doc_id"] == out["doc_id"]


# ---------- SC-06 阈值热更不重启生效（US-E1-03，Sprint 7 新补） ----------

async def test_matrix_sc06_threshold_hot_reload(pool):
    """Nacos 不可达 → 降级 sys_config；tg_web UPDATE 后经 5s 轮询等价 _reload 生效，
    source=db 暴露来源；测试后恢复种子值，不污染运行中栈。"""
    original = await pool.fetchval(
        "SELECT value FROM sys_config WHERE key='br-01-auto-block-score'")
    cfg = ConfigService(pool=pool, addr="http://127.0.0.1:9")   # 不可达地址强制降级
    await cfg._reload()
    assert cfg.snapshot()["source"] == "db"
    assert cfg.values.get("br-01-auto-block-score") == original
    try:
        await pool.execute(
            "UPDATE sys_config SET value='77' WHERE key='br-01-auto-block-score'")
        await cfg._reload()                                     # 等价一次 5s 轮询周期
        snap = cfg.snapshot()
        assert snap["source"] == "db"
        assert snap["values"]["br-01-auto-block-score"] == "77"  # 不重启生效
    finally:
        await pool.execute(
            "UPDATE sys_config SET value=$1 WHERE key='br-01-auto-block-score'", original)


# ---------- SC-07 处置幂等重放（DA-INV-03） ----------

async def test_matrix_sc07_idempotent_replay(pool, disposition):
    svc, repo, pub = disposition
    case_id = await _investigating_case(repo, score=82)
    await _with_evidence(svc, case_id)
    gate = await svc.submit(case_id, "freeze", None, f"{case_id}:freeze")
    approved = await svc.approve(gate["approval_id"], "human:approver", "同意")
    replay = await svc.core.execute_disposition(
        case_id, "freeze", None, f"{case_id}:freeze:{gate['approval_id']}",
        approval_ref=gate["approval_id"])
    assert replay["code"] == "E-IDEMPOTENT-CONFLICT"
    assert replay["first_result"]["exec_id"] == approved["exec_id"]
    assert await pool.fetchval(
        "SELECT count(*) FROM disposition_record WHERE case_id=$1", case_id) == 1


# ---------- SC-08 全链留痕回放（BA-BR-09 + US-E7-04 span） ----------

async def test_matrix_sc08_full_chain_replay_with_spans(pool, app_pool, aggregation,
                                                        disposition, verification):
    agg_svc, repo, _ = aggregation
    disp_svc = disposition[0]
    ver_svc = verification[0]
    subject = await _subject()
    await _seed_tx(app_pool, subject, n=12, amount=50.0)   # velocity 簇垫高至审批线上方
    agg_svc.external = FakeExternal(credit_band="high", complaint_items=1,
                                    sentiment_hits=[{"title": "负面", "sentiment": "negative",
                                                     "confidence": 0.9}])
    reg = await repo.register(subject, risk_score=50, source_type="TEST")
    case_id = reg["case_id"]
    await agg_svc.run(case_id)                                   # AA-SK-01 → INVESTIGATING
    await _with_evidence(disp_svc, case_id)                      # DA-INV-04 前置（冻结须附证据）
    gate = await disp_svc.submit(case_id, "freeze", None, f"{case_id}:freeze")  # AA-SK-03
    await disp_svc.approve(gate["approval_id"], "human:approver", "同意")
    exec_id = (await pool.fetchrow(
        "SELECT exec_id FROM disposition_record WHERE case_id=$1", case_id))["exec_id"]
    await ver_svc.verify(case_id, exec_id)                       # AA-SK-04 → ARCHIVED

    # Then-1：审计链关键动作齐备且按序可回放（BA-BR-09，AA-CL-06）
    trail = await repo.audit_trail(case_id)
    actions = [r["action"] for r in trail]
    for expected in ("case.register", "approval.create",
                     "disposition.submit", "verification.run"):
        assert expected in actions, f"审计链缺失动作 {expected}"
    assert all(r["actor"] and r["basis"] and r["trace_id"] == reg["trace_id"] for r in trail)
    # Then-2（US-E7-04）：技能执行 span 按 case_id 可回放，覆盖 AA-SK-01/03/04
    spans = [s for s in recent_spans(1000) if s["case_id"] == case_id]
    assert {s["skill_id"] for s in spans} >= {"AA-SK-01", "AA-SK-03", "AA-SK-04"}
    assert all(s["status"] == "ok" and s["duration_ms"] >= 0 for s in spans)


# ---------- SC-09 审批时效升级（BA-BR-13） ----------

async def test_matrix_sc09_approval_escalation(pool, disposition):
    svc, repo, pub = disposition
    case_id = await _investigating_case(repo, score=82)
    await _with_evidence(svc, case_id)
    gate = await svc.submit(case_id, "freeze", None, f"{case_id}:freeze")
    await pool.execute(
        "UPDATE approval_record SET created_at=now() - interval '31 minutes' "
        "WHERE approval_id=$1", gate["approval_id"])
    escalated = await scan_pending_escalations(pool, pub, minutes=30)
    assert [r["approval_id"] for r in escalated] == [gate["approval_id"]]
    assert "approval.escalate" in await _audit_actions(pool, case_id)
    assert "ApprovalEscalated" in [e["event"] for e in pub.published]


# ---------- SC-10 中风险禁止自动处置（BA-BR-01 分段） ----------

async def test_matrix_sc10_mid_risk_refused(pool, disposition):
    svc, repo, pub = disposition
    case_id = await _investigating_case(repo, score=55)
    result = await svc.submit(case_id, "release", None, f"{case_id}:release")
    assert result["route"] == "refused_mid_risk" and result["code"] == "E-DISP-SCOPE"
    assert await pool.fetch(
        "SELECT * FROM disposition_record WHERE case_id=$1", case_id) == []
    assert "disposition.refused" in await _audit_actions(pool, case_id)


# ---------- SC-11 velocity 特征与评分（BA-BR-14） ----------

async def test_matrix_sc11_velocity_feature(aggregation, app_pool):
    svc, repo, pub = aggregation
    subject = await _subject()
    await _seed_tx(app_pool, subject, n=12, amount=50.0)         # 近 1h 12 笔小额
    svc.external = FakeExternal(complaint_items=0)               # 聚焦 velocity 贡献
    reg = await repo.register(subject, risk_score=50, source_type="TEST")
    result = await svc.run(reg["case_id"])
    assert result["velocity"]["velocity_1h"]["count"] == 12
    assert result["risk_score"] >= 30                            # BA-BR-14 加分已计入
    tx_rows = [s for s in await repo.signals(reg["case_id"]) if s["source"] == "tx"]
    assert len(tx_rows) == 1 and tx_rows[0]["velocity_json"] is not None
