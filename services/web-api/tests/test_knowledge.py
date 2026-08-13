# -*- coding: utf-8 -*-
"""E6 知识沉淀与审计回放测试（SC-05 / SC-08 验收）

SC-05：Agent 申请入库 → pending 检索不可见（DA-INV-06）→ 人工确认发布 →
向量化入库 → AA-SK-02 检索可命中（doc_id 引用对齐）；非 human 发布被双守护拒绝。
SC-08：全链路审计回放——立案→聚合→调查→审批→执行→核验→归档动作序列完整，
每条含操作者/依据/trace_id（BA-BR-09，AA-CL-06）。
"""
import uuid

import asyncpg
import pytest

from app.core.state_machine import CaseEvent
from app.skills.knowledge import index_document, publish_and_index, search_kb


async def _subject() -> str:
    return uuid.uuid4().hex


# ---------- SC-05 知识入库人工确认门控（DA-INV-06，BA-BR-11） ----------

async def test_sc05_kb_human_gate_and_vector_search(pool, verification):
    """pending 检索不可见 → 人工发布+向量化 → 检索命中且附 doc_id"""
    svc = verification[0]
    case_id = f"CASE-KB-{uuid.uuid4().hex[:8]}"
    pattern = f"跑分手法特征-{uuid.uuid4().hex[:6]}"
    # Agent 提交入库申请（AA-SK-05，仅能 pending）
    out = await svc.core.submit_kb_application(
        case_id, "case", "测试复盘", f"复盘摘要：{pattern}，夜间高频小额转出。")
    doc_id = out["doc_id"]
    assert out["status"] == "pending"

    hits = await search_kb(pool, pattern)
    assert all(h["doc_id"] != doc_id for h in hits)            # pending 对检索不可见（DA-INV-06）

    res = await publish_and_index(pool, doc_id, operator="human:strategist", comment="确认发布")
    assert res["status"] == "published" and res["chunks"] >= 1  # 向量化入库（DA-T-10）

    hits = await search_kb(pool, pattern)
    assert hits and hits[0]["doc_id"] == doc_id                # AA-SK-02 检索可命中
    row = await pool.fetchrow("SELECT reviewer FROM kb_document WHERE doc_id=$1", doc_id)
    assert row["reviewer"] == "human:strategist"


async def test_sc05_non_human_publish_rejected_by_db_guard(pool, app_pool):
    """绕过人类标记直置 published：DB 触发器拒绝（DA-INV-06 双守护）
    播种经 tg_app（kb_document INSERT 权限）；直 UPDATE 经 tg_web（唯一持 UPDATE
    权限的角色，02-roles.sql），未声明 tg.actor 即被触发器拒绝。"""
    doc_id = uuid.uuid4().hex
    await app_pool.execute(
        """INSERT INTO kb_document (doc_id, category, title, content, status, applicant)
           VALUES ($1, 'case', '守护测试', '内容', 'pending', 'AA-AG-05')""", doc_id)
    with pytest.raises(asyncpg.PostgresError, match="E-KB-HUMAN-GATE"):
        await pool.execute("UPDATE kb_document SET status='published' WHERE doc_id=$1", doc_id)


# ---------- SC-08 审计留痕完整性（BA-BR-09，AA-CL-06） ----------

async def test_sc08_full_chain_audit_replay(pool, investigation, disposition, verification):
    """立案→聚合→调查→审批→执行→核验→归档全链留痕，按序可回放"""
    inv_svc, repo, _ = investigation
    disp_svc = disposition[0]
    ver_svc = verification[0]

    reg = await repo.register(await _subject(), risk_score=82, source_type="TEST")
    case_id, trace_id = reg["case_id"], reg["trace_id"]
    r = await repo.transition(case_id, CaseEvent.AGGREGATION_STARTED, "agent:AA-AG-02", 0)
    await repo.transition(case_id, CaseEvent.SIGNALS_AGGREGATED, "agent:AA-AG-02", r["version"])
    await inv_svc.run(case_id)                                  # 调查：假设+证据固化+转待审批
    gate = await disp_svc.submit(case_id, "freeze", None, f"{case_id}:freeze")
    await disp_svc.approve(gate["approval_id"], "human:approver", "同意")
    exec_row = await pool.fetchrow(
        "SELECT exec_id FROM disposition_record WHERE case_id=$1", case_id)
    await ver_svc.verify(case_id, exec_row["exec_id"])          # 核验→归档→复盘申请

    trail = await repo.audit_trail(case_id)
    actions = [r["action"] for r in trail]
    # 完整动作序列（SC-08 Then 子句）
    for expected in ("case.register",
                     f"case.transition.{CaseEvent.AGGREGATION_STARTED.value}",
                     f"case.transition.{CaseEvent.SIGNALS_AGGREGATED.value}",
                     "investigation.complete", "approval.create",
                     f"case.transition.{CaseEvent.APPROVAL_APPROVED.value}",
                     "disposition.submit",
                     f"case.transition.{CaseEvent.DISPOSITION_EXECUTED.value}",
                     "verification.run",
                     f"case.transition.{CaseEvent.CASE_ARCHIVED.value}"):
        assert expected in actions, f"审计链缺失动作 {expected}"
    # 时序单调不减（ORDER BY ts 回放）
    ts = [r["ts"] for r in trail]
    assert ts == sorted(ts)
    # 每条含操作者/依据/trace_id（BA-BR-09）
    assert all(r["actor"] and r["trace_id"] == trace_id for r in trail)
    assert all(r["basis"] for r in trail)
