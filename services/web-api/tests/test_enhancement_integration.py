# -*- coding: utf-8 -*-
"""docs/14 增强集成测试（§4 集成层：真实 PG 5433 + 真 MCP 链路）

覆盖：
  1. baseline upsert 与回读（A1，DA-T-14，BA-BR-15 双轨输入）；
  2. follow_outcomes 登记 + T+7/T+30 双窗回填（C2：clean/recidivism/appealed
     三标签 + E-OUTCOME-FOLLOW + 审计留痕，到窗不回退）；
  3. kb_metabolism 知识代谢（E1：effectiveness 重算 + 超窗零引用自动降级
     pending + E-KB-DECAY，BA-BR-20 降级自动/发布人工方向不变）。
"""
import uuid
from datetime import datetime, timedelta, timezone

from app.skills.disposition import follow_outcomes
from app.skills.knowledge import kb_metabolism
from conftest import MCP_CORE_URL


def _new_subject() -> str:
    return uuid.uuid4().hex


async def _seed_history_tx(app_pool, subject: str, n: int = 25, amount: float = 100.0):
    """30 天窗口内（排除近 24h）平稳小额流水 → 基线样本（≥20 过冷启动线）"""
    now = datetime.now(timezone.utc)
    for i in range(n):
        await app_pool.execute(
            """INSERT INTO transaction (tx_id, account_hash, amount, mcc, channel, ts)
               VALUES ($1, $2, $3, '5411', 'CNP', $4)""",
            f"tx-{uuid.uuid4().hex[:12]}", subject, amount,
            now - timedelta(days=2 + i % 25, minutes=i))


# ---------- 1. A1 基线 upsert（DA-T-14） ----------

async def test_baseline_upsert_and_readback(aggregation, app_pool, pool):
    svc = aggregation[0]
    subject = _new_subject()
    await _seed_history_tx(app_pool, subject)
    b = await svc._refresh_baseline(subject, datetime.now(timezone.utc))
    assert b is not None and b["tx_count"] >= 20 and b["ewma_amount"] > 0
    row = await pool.fetchrow(
        'SELECT ewma_amount, p95_amount, tx_count, "window" FROM account_baseline'
        ' WHERE account_id=$1', subject)
    assert row is not None and row["tx_count"] >= 20 and row["window"] == "30d"
    # 幂等 upsert：重跑只更新不新增
    await svc._refresh_baseline(subject, datetime.now(timezone.utc))
    assert await pool.fetchval(
        "SELECT count(*) FROM account_baseline WHERE account_id=$1", subject) == 1


async def test_baseline_cold_start_fallback_none(aggregation):
    svc = aggregation[0]
    # 无历史流水且无存量基线 → None（BA-BR-15 双轨：回退全局阈值不阻断）
    assert await svc._refresh_baseline(_new_subject(),
                                       datetime.now(timezone.utc)) is None


# ---------- 2. C2 outcome 双窗回填（DA-T-15） ----------

async def _disposed_case(pool, app_pool, subject: str, disposed_days_ago: int) -> str:
    """直造已处置闭环案件：DISPOSED 案件 + executed 凭证（ts 回溯到处置时刻）"""
    case_id = f"CASE-OC-{uuid.uuid4().hex[:6]}"
    await pool.execute(
        """INSERT INTO risk_case (case_id, subject_ref, status, risk_score, trace_id)
           VALUES ($1, $2, 'DISPOSED', 70, $3)""",
        case_id, subject, uuid.uuid4().hex)
    await app_pool.execute(
        """INSERT INTO disposition_record (exec_id, case_id, action, idempotency_key,
                                           status, ts)
           VALUES ($1, $2, 'freeze', $3, 'executed', now() - make_interval(days=>$4))""",
        uuid.uuid4().hex, case_id, f"{case_id}:freeze", disposed_days_ago)
    return case_id


async def test_follow_outcomes_clean_t7(pool, app_pool):
    from conftest import RecordingPublisher
    pub = RecordingPublisher()
    subject = _new_subject()
    case_id = await _disposed_case(pool, app_pool, subject, disposed_days_ago=9)
    updated = await follow_outcomes(pool, pub)
    hit = [u for u in updated if u["case_id"] == case_id]
    assert hit and hit[0]["window"] == "T+7" and hit[0]["label"] == "clean"
    # T+30 未到窗不回填（只增不改：双窗独立）
    row = await pool.fetchrow(
        "SELECT t7_label, t30_label FROM disposition_outcome WHERE case_id=$1", case_id)
    assert row["t7_label"] == "clean" and row["t30_label"] is None
    assert "E-OUTCOME-FOLLOW" in [e["event"] for e in pub.published]
    assert await pool.fetchval(
        "SELECT count(*) FROM audit_log WHERE target=$1 AND action='outcome.follow'",
        case_id) >= 1
    # 再跑一轮：已到窗标签不回退不重发（幂等）
    again = await follow_outcomes(pool, pub)
    assert not [u for u in again if u["case_id"] == case_id]


async def test_follow_outcomes_recidivism_flag(pool, app_pool):
    from conftest import RecordingPublisher
    pub = RecordingPublisher()
    subject = _new_subject()
    case_id = await _disposed_case(pool, app_pool, subject, disposed_days_ago=40)
    # 同主体处置后再立案（created_at=now > disposed_at）→ recidivism
    await pool.execute(
        """INSERT INTO risk_case
               (case_id, subject_ref, status, risk_score, trace_id, source_type)
           VALUES ($1, $2, 'REGISTERED', 50, $3, 'TEST')""",
        f"CASE-RC-{uuid.uuid4().hex[:6]}", subject, uuid.uuid4().hex)
    updated = await follow_outcomes(pool, pub)
    hit = [u for u in updated if u["case_id"] == case_id]
    assert hit and all(u["label"] == "recidivism" for u in hit)
    assert {u["window"] for u in hit} == {"T+7", "T+30"}   # 双窗均到窗回填
    row = await pool.fetchrow(
        "SELECT recidivism_flag, appealed_flag FROM disposition_outcome"
        " WHERE case_id=$1", case_id)
    assert row["recidivism_flag"] is True and row["appealed_flag"] is False


async def test_follow_outcomes_appealed(pool, app_pool):
    from conftest import RecordingPublisher
    pub = RecordingPublisher()
    subject = _new_subject()
    case_id = await _disposed_case(pool, app_pool, subject, disposed_days_ago=40)
    # 处置后同主体新增投诉信号（ts 默认 now > disposed_at，不新立案避免触发
    # 再犯计数，申诉判定经 risk_case.subject_ref 关联）→ appealed（误处置信号）
    await app_pool.execute(
        """INSERT INTO risk_signal (signal_id, case_id, source, type, confidence,
                                    query_reason)
           VALUES ($1, $2, 'complaint', 'deny_transaction', 0.8, '测试：申诉信号')""",
        uuid.uuid4().hex, case_id)
    # 窗口参数显式对齐处置时刻（40 天前），双窗均到窗
    updated = await follow_outcomes(pool, pub, t7_days=40, t30_days=40)
    hit = [u for u in updated if u["case_id"] == case_id]
    assert hit and all(u["label"] == "appealed" for u in hit)


# ---------- 3. E1 知识代谢（kb_document 三列 + 自动降级） ----------

async def test_kb_metabolism_decay_zero_citation(pool):
    from app.skills.knowledge import publish_and_index
    from app.skills.mcp_adapters import CoreClient
    from conftest import RecordingPublisher
    pub = RecordingPublisher()
    core = CoreClient(MCP_CORE_URL)
    case_id = f"CASE-KB-{uuid.uuid4().hex[:6]}"
    app = await core.submit_kb_application(
        case_id, "case", "代谢测试：零引用条目", "长期无引用的测试知识内容")
    doc_id = app["doc_id"]
    await publish_and_index(pool, doc_id, "human:风控策略管理员")
    # 回溯发布时间至 40 天前（超 30 天代谢窗）
    await pool.execute(
        "UPDATE kb_document SET reviewed_at = now() - interval '40 days'"
        " WHERE doc_id=$1", doc_id)
    out = await kb_metabolism(pool, pub)
    assert doc_id in out["doc_ids"] and out["decayed"] >= 1
    row = await pool.fetchrow(
        "SELECT status, effectiveness_score FROM kb_document WHERE doc_id=$1", doc_id)
    assert row["status"] == "pending"                   # 降级自动（BA-BR-20）
    assert float(row["effectiveness_score"]) == 0.0     # 零引用有效性 0
    decay_events = [e for e in pub.published if e["event"] == "E-KB-DECAY"]
    assert decay_events and decay_events[0]["payload"]["reason"] == "zero_citation_30d"
    assert await pool.fetchval(
        "SELECT count(*) FROM audit_log WHERE target=$1 AND action='kb.decay'",
        doc_id) == 1


async def test_kb_metabolism_keeps_cited_documents(pool):
    from app.skills.knowledge import publish_and_index, mark_kb_feedback
    from app.skills.mcp_adapters import CoreClient
    from conftest import RecordingPublisher
    pub = RecordingPublisher()
    core = CoreClient(MCP_CORE_URL)
    app = await core.submit_kb_application(
        f"CASE-KB-{uuid.uuid4().hex[:6]}", "case",
        "代谢测试：有引用条目", "被调查引用过的知识不应被降级")
    doc_id = app["doc_id"]
    await publish_and_index(pool, doc_id, "human:风控策略管理员")
    await pool.execute(
        "UPDATE kb_document SET reviewed_at = now() - interval '40 days',"
        " cite_count = 5 WHERE doc_id=$1", doc_id)
    await mark_kb_feedback(pool, [doc_id, doc_id])      # 去重后 hit_correct +1
    out = await kb_metabolism(pool, pub)
    assert doc_id not in out["doc_ids"]                 # 有引用不降级
    row = await pool.fetchrow(
        "SELECT status, hit_correct, effectiveness_score FROM kb_document"
        " WHERE doc_id=$1", doc_id)
    assert row["status"] == "published" and row["hit_correct"] == 1
    assert float(row["effectiveness_score"]) == 0.2     # 1/5 重算生效
