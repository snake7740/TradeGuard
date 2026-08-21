# -*- coding: utf-8 -*-
"""场景矩阵：SC-01~SC-27 紧凑 E2E（验收出口"场景全绿"的单一取证文件）

每个 SC 一条测试，聚焦 06 §2 Gherkin 的 Then 核心断言（细分支由专题测试文件
test_pipeline / test_disposition / test_verification / test_knowledge 承载）。
Sprint 7 新增重点：SC-04 黑名单聚合裁决（BA-BR-04）与 SC-06 阈值热更（US-E1-03），
SC-08 增加技能执行 span 回放断言（US-E7-04 可观测并入留痕完整率取证）。
docs/14 增强（US-E8~E12）：SC-12~17 六场景——自适应基线双轨（BR-15）/拓扑
研判不裁决（BR-16·INV-07）/时序回路（BR-17）/并行假设豁免留痕（BR-18）/
控辩互审（BR-19·INV-09）/知识代谢人审门（BR-20·INV-06/08）。
LoopEngine 环设施（US-E13，docs/14 v1.3）：SC-19/20 两场景——DLQ 失败归宿
驻车与复位人工门（BR-22）/双轮有界环不空转与慢环归因可度量（BR-22）。
RAG 深化（US-E14，docs/14 v1.4）：SC-21/22 两场景——归档复盘产结构化
案例分析且可检索复用（BR-23 语料面）/B 端知识问答引用守护与人工角色门
（BR-23 消费面，API-W-27 × AA-AG-06）。
企业资质外部源（US-E15，docs/14 v1.5）：SC-23 单场景——无特征案件保守
全查纳入 enterprise 五维（BA-BR-24 仅线索不裁决，API-M-16 双轨，BA-BR-10
查询事由门），走活栈 AA-MCP-02 真实链路。
案件治理批次（US-E17~19，docs/14 v1.7，docs/09 v1.3 赛道对标三缺口）：
SC-25 优先级队列风险分级派生与 aging 留痕（BR-26）/SC-26 叙事生成引用
对齐防幻觉与人审门（BR-27，docs/13 D2 闭合）/SC-27 可治理自动关闭标准
留痕与人工复位通道（BR-28）。
"""
import urllib.parse
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config_service import ConfigService
from app.core.state_machine import CaseEvent
from app.core.tracing import recent_spans
from app.skills.disposition import scan_pending_escalations
from app.skills.knowledge import publish_and_index, search_kb
from conftest import FakeExternal, MCP_EXTERNAL_URL


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
    source=db 暴露来源；测试后恢复种子值，不污染运行中栈。

    闭环修复 D6：样本键从 br-01-auto-block-score 换为 br-14-velocity-1h-count——
    70 分线已成 mcp-core 门控实时读取的活键（C/D2），瞬时改写会与并行用例
    （SC-02/07/08 审批链）串扰；velocity 阈值键对审批门控无影响。"""
    key = "br-14-velocity-1h-count"
    original = await pool.fetchval(
        "SELECT value FROM sys_config WHERE key=$1", key)
    cfg = ConfigService(pool=pool, addr="http://127.0.0.1:9")   # 不可达地址强制降级
    await cfg._reload()
    assert cfg.snapshot()["source"] == "db"
    assert cfg.values.get(key) == original
    try:
        await pool.execute(
            "UPDATE sys_config SET value='11' WHERE key=$1", key)
        await cfg._reload()                                     # 等价一次 5s 轮询周期
        snap = cfg.snapshot()
        assert snap["source"] == "db"
        assert snap["values"][key] == "11"                      # 不重启生效
    finally:
        await pool.execute(
            "UPDATE sys_config SET value=$1 WHERE key=$2", original, key)


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


async def _seed_history_tx(app_pool, subject: str, n: int = 25, amount: float = 100.0):
    """30 天窗口内（排除近 24h）平稳小额流水 → 基线样本（≥20 过冷启动线）"""
    now = datetime.now(timezone.utc)
    for i in range(n):
        await app_pool.execute(
            """INSERT INTO transaction (tx_id, account_hash, amount, mcc, channel, ts)
               VALUES ($1, $2, $3, '5411', 'CNP', $4)""",
            f"tx-{uuid.uuid4().hex[:12]}", subject, amount,
            now - timedelta(days=2 + i % 25, minutes=i))


# ---------- SC-12 自适应基线捕获渐进盗用（BA-BR-15，DA-INV-01） ----------

async def test_matrix_sc12_baseline_deviation_dual_track(aggregation, app_pool, pool):
    """Given 30 天小额平稳基线；When 突增 8 倍交易；Then 偏离度入评分，
    全局频次阈值未触发亦经基线轨道入中通道（双轨取高，SC-12）"""
    svc, repo, pub = aggregation
    subject = await _subject()
    await _seed_history_tx(app_pool, subject)                    # 基线样本 25×100
    await _seed_tx(app_pool, subject, n=3, amount=800.0)         # 近期突发 8 倍（3 笔 < 全局频次阈值）
    reg = await repo.register(subject, risk_score=50, source_type="TEST")

    result = await svc.run(reg["case_id"])

    assert result["baseline_dev"] is not None and result["baseline_dev"] >= 3.0
    assert result["velocity"]["velocity_1h"]["count"] == 3       # 全局阈值未触发（双轨之基线轨独立产出）
    assert result["risk_score"] >= 15                            # BASELINE_DEV_BONUS 已计入
    assert result["route"] != "auto_release"                     # 不入自动放行，进中通道
    row = await pool.fetchrow(
        'SELECT tx_count, "window" FROM account_baseline WHERE account_id=$1', subject)
    assert row is not None and row["tx_count"] >= 20 and row["window"] == "30d"


# ---------- SC-13 拓扑分命中团伙但不驱动处置（BA-BR-16，DA-INV-07） ----------

async def test_matrix_sc13_topology_clue_not_driver(app_pool, pool, investigation):
    """Given 5 账户同设备二部子图；When 调查查询；Then topology_stats 输出高嫌疑分，
    且案件风险分/状态迁移不因该分发生（拓扑仅线索，DA-INV-07）"""
    svc, repo, _ = investigation
    subject = await _subject()
    device = uuid.uuid4().hex
    for acct in [subject] + [uuid.uuid4().hex for _ in range(4)]:
        await app_pool.execute(
            """INSERT INTO transaction (tx_id, account_hash, device_fp_hash,
                                        amount, mcc, channel, ts)
               VALUES ($1, $2, $3, 50.0, '5411', 'CNP', now())""",
            uuid.uuid4().hex, acct, device)
    reg = await repo.register(subject, risk_score=60, source_type="TEST")
    case_id = reg["case_id"]
    r = await repo.transition(case_id, CaseEvent.AGGREGATION_STARTED, "agent:AA-AG-02", 0)
    await repo.transition(case_id, CaseEvent.SIGNALS_AGGREGATED,
                          "agent:AA-AG-02", r["version"])

    out = await svc.run(case_id)

    topo = out["graph"]["topology"]
    assert topo["bipartite_concentration"] >= 0.8                # 同设备二部高集中
    assert topo["suspicion"] >= 0.3 and not topo["degraded"]     # 高嫌疑线索分
    # DA-INV-07：拓扑分不进评分——调查前后风险分不变（无 BA-BR-06 类加分源）
    assert await pool.fetchval(
        "SELECT risk_score FROM risk_case WHERE case_id=$1", case_id) == 60


# ---------- SC-14 时序回路命中跑分剧本（BA-BR-17） ----------

async def test_matrix_sc14_fund_loop_temporal(aggregation, app_pool):
    """Given A→B→C→A 90 分钟内闭环；When 聚合；Then temporal_json 命中回路模式，
    评分上调（时序轨独立产出，全局频次阈值未触发亦入中通道）"""
    svc, repo, pub = aggregation
    a, b, c = await _subject(), await _subject(), await _subject()
    now = datetime.now(timezone.utc)
    for acct, payee, mins in ((a, b, 60), (b, c, 40), (c, a, 20)):
        await app_pool.execute(
            """INSERT INTO transaction (tx_id, account_hash, payee_hash,
                                        amount, mcc, channel, ts)
               VALUES ($1, $2, $3, 500.0, '5411', 'transfer', $4)""",
            uuid.uuid4().hex, acct, payee, now - timedelta(minutes=mins))
    reg = await repo.register(a, risk_score=50, source_type="TEST")

    result = await svc.run(reg["case_id"])

    assert result["temporal"]["fund_loop"] is True               # 回路模式命中
    assert result["risk_score"] >= 20                            # TEMPORAL_BONUS[fund_loop] 已计入
    assert result["route"] != "auto_release"


# ---------- SC-15 并行假设留痕完备（BA-BR-18） ----------

class _FailingDeepExternal:
    """credit/complaint 深查双失败 → 首选源均未成功，触发假设豁免留痕"""

    async def query_credit_report(self, subject_id, query_reason):
        raise RuntimeError("mock：credit 深查失败")

    async def query_sentiment(self, subject_id, query_reason):
        return {"source": "sentiment-mock", "hits": [], "degraded": False}

    async def query_complaints(self, subject_id, query_reason):
        raise RuntimeError("mock：complaint 深查失败")

    async def query_enterprise(self, subject_id, query_reason):
        return {"source": "enterprise-mock", "reg_status": "active",
                "abnormal_ops_count": 0, "admin_penalty_12m": 0,
                "judicial_risk_count": 0, "related_entity_count": 1,
                "risk_flag": "low", "query_reason": query_reason, "degraded": False}


async def test_matrix_sc15_parallel_hypothesis_trace(investigation):
    """Given 高风险案 ≥2 假设（跑分+盗卡）；When 调查完成；Then E-INV-HYPOTHESIS
    并行分支留痕 + 「为什么没查 X」豁免留痕（BA-BR-18）"""
    svc, repo, pub = investigation
    case_id = await _investigating_case(repo, score=80)
    await svc.core.record_case_signals(case_id, 80, [{
        "source": "tx", "type": "velocity_anomaly", "confidence": 0.8,
        "raw_ref": f"{case_id}:tx", "query_reason": "SC-15",
        "velocity_json": {"velocity_1h": {"count": 12, "amount": 900.0}}}, {
        "source": "tx", "type": "large_amount_burst", "confidence": 0.85,
        "raw_ref": f"{case_id}:tx2", "query_reason": "SC-15", "velocity_json": None}])
    svc.external = _FailingDeepExternal()   # 首选深查源（credit/complaint）全失败

    out = await svc.run(case_id)

    hyp_events = [e for e in pub.published if e["event"] == "E-INV-HYPOTHESIS"]
    assert hyp_events                                            # 并行分支领域事件已发
    payload = hyp_events[0]["payload"]
    assert payload["parallel"] is True and len(payload["hypotheses"]) >= 2
    hyp_skipped = [s for s in out["plan"]["skipped"] if "hypothesis" in s]
    assert hyp_skipped                                           # 豁免留痕非空
    assert all("BA-BR-18" in s["reason"] and "未深查留痕" in s["reason"]
               for s in hyp_skipped)


# ---------- SC-16 控辩辩论入审计不改裁决（BA-BR-19，DA-INV-09） ----------

async def test_matrix_sc16_debate_recorded_human_decides(pool, disposition):
    """Given 冻结建议；When 建单互审；Then 审批单含控/辩/裁三段 + E-REVIEW-DEBATE，
    最终裁决仍由审批官作出（控辩只建议不决策）"""
    svc, repo, pub = disposition
    case_id = await _investigating_case(repo, score=82)
    await _with_evidence(svc, case_id)

    gate = await svc.submit(case_id, "freeze", None, f"{case_id}:freeze")

    assert gate["route"] == "approval_required"                  # 控辩不代裁：仍需人工审批
    debate = json.loads(await pool.fetchval(
        "SELECT debate_json FROM approval_record WHERE approval_id=$1",
        gate["approval_id"]))
    assert set(debate) == {"source", "prosecution", "defense",
                           "adjudication", "verdict", "summary"}  # DA-INV-09 只增形状
    assert debate["verdict"] in ("pass", "concerns", "escalate")
    assert debate["adjudication"] and debate["summary"]          # 控/辩/裁三段齐备
    if debate["source"] == "rule":                               # 规则版声明裁决权归属（LLM 版同约束在提示词内）
        assert "审批官" in debate["summary"]                      # BA-BR-19
    assert "E-REVIEW-DEBATE" in [e["event"] for e in pub.published]
    approved = await svc.approve(gate["approval_id"], "human:approver", "同意")
    assert approved["route"] == "executed"                       # 最终裁决由审批官作出


# ---------- SC-17 知识降级自动、发布人工（BA-BR-20/21，DA-INV-06/08） ----------

async def test_matrix_sc17_kb_metabolism_human_gate(pool, verification):
    """Given 条目 30 天零引用；When 代谢任务运行；Then 自动转 pending + E-KB-DECAY；
    When Agent 试图直接 published；Then 被拒（发布仅限 human:*）"""
    from app.skills.knowledge import kb_metabolism
    from conftest import RecordingPublisher
    svc = verification[0]
    pub = RecordingPublisher()
    app = await svc.core.submit_kb_application(
        f"CASE-MTX-{uuid.uuid4().hex[:8]}", "case",
        f"SC-17 代谢样本-{uuid.uuid4().hex[:6]}", "长期零引用的场景矩阵测试知识")
    doc_id = app["doc_id"]
    await publish_and_index(pool, doc_id, "human:风控策略管理员")
    await pool.execute(
        "UPDATE kb_document SET reviewed_at = now() - interval '40 days'"
        " WHERE doc_id=$1", doc_id)

    out = await kb_metabolism(pool, pub)

    assert doc_id in out["doc_ids"]                              # 零引用超窗自动降级
    assert await pool.fetchval(
        "SELECT status FROM kb_document WHERE doc_id=$1", doc_id) == "pending"
    decay = [e for e in pub.published if e["event"] == "E-KB-DECAY"]
    assert decay and decay[0]["payload"]["reason"] == "zero_citation_30d"
    # Agent 直接发布被拒：发布门控仅 human:*（DA-INV-06 应用层守护）
    app2 = await svc.core.submit_kb_application(
        f"CASE-MTX-{uuid.uuid4().hex[:8]}", "case",
        f"SC-17 越权样本-{uuid.uuid4().hex[:6]}", "agent 试图越过人审门直接发布")
    with pytest.raises(PermissionError):
        await publish_and_index(pool, app2["doc_id"], "agent:AA-AG-05")
    assert await pool.fetchval(
        "SELECT status FROM kb_document WHERE doc_id=$1", app2["doc_id"]) == "pending"


# ---------- SC-19/20 LoopEngine 环设施（BA-BR-22，US-E13） ----------

@pytest.fixture(scope="module")
def api_client():
    """全链 TestClient（同 test_loop_engine 装配法）：SC-19 复位人工门走真实 HTTP 守卫"""
    import os
    from fastapi.testclient import TestClient
    from conftest import PG_DSN
    os.environ["PG_DSN"] = PG_DSN
    os.environ.pop("TG_API_TOKEN", None)
    from app.main import create_app
    with TestClient(create_app()) as c:
        yield c


async def test_matrix_sc19_dlq_park_and_human_gate(pool, case_repo, api_client):
    """Given 环重试耗尽；When 累计达上限；Then 驻车停扫（轮询排除细分支见
    test_loop_engine）；When agent/越权角色复位；Then 409/403 拒绝；
    When 值班员复位；Then 清零放行且 resolved_by=human:*（只增不删）"""
    from app.core.loop_engine import deadletter_record
    repo, _ = case_repo
    cid = (await repo.register(await _subject(), risk_score=50,
                               source_type="DEMO"))["case_id"]
    d = await deadletter_record(pool, cid, "aggregation", RuntimeError("SC-19"), 9)
    assert d["parked"] is True                                   # 失败归宿驻车而非静默丢弃

    r = api_client.post(f"/api/deadletter/{cid}/retry",
                        headers={"X-Operator": "agent:AA-AG-04"})
    assert r.status_code == 409                                  # 环不得自清失败归宿
    assert r.json()["detail"]["code"] == "E-HUMAN-ONLY"
    r = api_client.post(f"/api/deadletter/{cid}/retry",
                        headers={"X-Operator": urllib.parse.quote("合规审计员")})
    assert r.status_code == 403                                  # 角色门：越权拒绝
    r = api_client.post(f"/api/deadletter/{cid}/retry",
                        headers={"X-Operator": urllib.parse.quote("风控值班员")})
    assert r.status_code == 200 and r.json()["ok"] is True
    row = await pool.fetchrow(
        "SELECT attempts, parked, resolved_by FROM processing_deadletter"
        " WHERE case_id=$1", cid)
    assert row["attempts"] == 0 and row["parked"] is False
    assert row["resolved_by"].startswith("human:")               # 复位留痕人工身份


async def test_matrix_sc20_bounded_loop_and_slow_attribution(pool, case_repo,
                                                             investigation):
    """Given 全部调查源首轮成功；When 调查；Then 环一轮终止不空转（rounds 留痕，
    双轮补查细分支见 test_loop_engine）；Given 规则提案人审发布；When 同主体
    再犯；Then 慢环归因 recurred_after=True（效果可度量）"""
    from app.skills.knowledge import attribute_rule_proposals
    svc, repo, _ = investigation

    class _NoLlm:
        available = False  # 隔离外呼非确定性（同 test_loop_engine/_NoLlm）
    svc.llm_client = _NoLlm()
    case_id = await _investigating_case(repo, 60)
    await svc.core.record_case_signals(case_id, 60, [{
        "source": "tx", "type": "velocity_anomaly", "confidence": 0.8,
        "raw_ref": f"{case_id}:tx", "query_reason": "SC-20",
        "velocity_json": {"velocity_1h": {"count": 12, "amount": 900.0},
                          "velocity_24h": {"count": 18, "amount": 1500.0}}}])
    out = await svc.run(case_id)
    assert len(out["plan"]["rounds"]) == 1                       # 有界：无缺口不空转二轮
    assert out["plan"]["reflection"]["verdict"] == "sufficient"

    subject = await _subject()
    src = (await repo.register(subject, risk_score=60,
                               source_type="TEST"))["case_id"]
    await repo.register(subject, risk_score=60, source_type="TEST")  # 同主体再犯
    app = await svc.core.submit_kb_application(
        src, "rule_proposal", f"SC-20 提案-{uuid.uuid4().hex[:6]}",
        "场景矩阵：规则提案慢环归因样本")
    if isinstance(app, str):
        app = json.loads(app)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('tg.actor', 'human:test', true)")
        await conn.execute(
            "UPDATE kb_document SET status='published', reviewer='human:test',"
            " reviewed_at=now() - interval '1 day' WHERE doc_id=$1", app["doc_id"])
    res = await attribute_rule_proposals(pool)
    assert res["checked"] >= 1 and res["recurred"] >= 1
    assert await pool.fetchval(
        "SELECT recurred_after FROM proposal_attribution WHERE doc_id=$1",
        app["doc_id"]) is True                                   # 发布后再犯可度量


# ---------- SC-21/22 RAG 深化：案例分析语料 × B 端问答（BA-BR-23，US-E14） ----------

async def test_matrix_sc21_structured_retrospective_retrievable(
        aggregation, app_pool, pool, disposition, verification):
    """Given 案件全链归档；When 复盘申请产出；Then 结构化案例分析四段齐备
    （信号指纹为主型×分布）；When 人审发布；Then 以主型手法特征检索即命中
    该复盘（语料可检索复用，后续调查与 B 端问答同源受益）"""
    agg_svc, repo, _ = aggregation
    disp_svc = disposition[0]
    ver_svc = verification[0]
    subject = await _subject()
    await _seed_tx(app_pool, subject, n=12, amount=50.0)      # velocity 簇（信号主型锚点）
    reg = await repo.register(subject, risk_score=50, source_type="TEST")
    case_id = reg["case_id"]
    await agg_svc.run(case_id)
    await _with_evidence(disp_svc, case_id)
    gate = await disp_svc.submit(case_id, "freeze", None, f"{case_id}:freeze")
    await disp_svc.approve(gate["approval_id"], "human:approver", "同意")
    exec_id = (await pool.fetchrow(
        "SELECT exec_id FROM disposition_record WHERE case_id=$1", case_id))["exec_id"]
    out = await ver_svc.verify(case_id, exec_id)               # → ARCHIVED + 复盘申请

    doc = await pool.fetchrow(
        "SELECT title, content FROM kb_document WHERE doc_id=$1",
        out["kb_application"])
    for section in ("【案件概况】", "【手法指纹】", "【处置结论】", "【复用提示】"):
        assert section in doc["content"]
    # 主型确定性：复盘主型 == 库内信号类型按计数降序首位（分布段格式 类型×计数）
    primary = await pool.fetchval(
        "SELECT type FROM risk_signal WHERE case_id=$1"
        " GROUP BY type ORDER BY count(*) DESC, type LIMIT 1", case_id)
    assert f"{primary}×1" in doc["content"]                    # 信号类型分布（主型×计数）
    assert f"{primary} 手法特征" in doc["title"]                # 标题携带检索锚点
    await publish_and_index(pool, out["kb_application"], "human:风控策略管理员")
    hits = await search_kb(pool, f"{primary} 手法特征")
    assert hits and hits[0]["doc_id"] == out["kb_application"]  # 语料发布即可检索复用


async def test_matrix_sc22_kb_ask_grounded_and_human_gate(pool, verification,
                                                          api_client):
    """Given 已发布知识；When 人工角色问同类问题；Then 回答带 doc_id 引用
    （grounded）；When 问无关联问题；Then 显式声明无先例不虚构；
    When agent 调用；Then 403（问答可追责到人）；且问答留痕 kb.ask"""
    svc = verification[0]
    pattern = f"SC-22 跨行清算垫资手法-{uuid.uuid4().hex[:6]}"
    app = await svc.core.submit_kb_application(
        f"CASE-MTX-{uuid.uuid4().hex[:8]}", "case", pattern,
        f"复盘摘要：{pattern}，夜间高频小额转出后集中清算。")
    await publish_and_index(pool, app["doc_id"], "human:风控策略管理员")

    r = api_client.post("/api/kb/ask", json={"question": pattern},
                        headers={"X-Operator": urllib.parse.quote("风控值班员")})
    assert r.status_code == 200
    body = r.json()
    assert body["grounded"] is True
    assert any(c["doc_id"] == app["doc_id"] for c in body["citations"])  # doc_id 引用
    assert app["doc_id"] in body["answer"]

    r = api_client.post("/api/kb/ask",
                        json={"question": f"完全无关问题-{uuid.uuid4().hex[:12]}"},
                        headers={"X-Operator": urllib.parse.quote("合规审计员")})
    assert r.status_code == 200 and r.json()["grounded"] is False
    assert "无先例" in r.json()["answer"]                       # 未命中显式声明，不虚构

    r = api_client.post("/api/kb/ask", json={"question": pattern},
                        headers={"X-Operator": "agent:AA-AG-04"})
    assert r.status_code == 403                                 # 人工角色门（BA-BR-23）
    assert r.json()["detail"]["code"] == "E-FORBIDDEN-ROLE"
    assert await pool.fetchval(
        "SELECT count(*) FROM audit_log WHERE action='kb.ask'") >= 2  # 问答留痕可追责


# ---------- SC-23 企业资质外部源五维扩维（BA-BR-24，US-E15，API-M-16） ----------

async def test_matrix_sc23_enterprise_external_source(pool, investigation):
    """Given 无特征案件（保守全查）；When 调查执行；Then enterprise 源五维
    齐备入 findings 与证据链（仅线索不裁决：评分不受 risk_flag 影响）；
    且 query_reason 缺失拒绝 E-REASON-REQUIRED（BA-BR-10）；且同主体确定性
    回放一致（双轨无厂商 Key 默认 mock 轨）"""
    from app.skills.mcp_adapters import ExternalSourcesClient
    svc, repo, _ = investigation

    class _NoLlm:
        available = False  # 隔离外呼非确定性（同 SC-20/_NoLlm）
    svc.llm_client = _NoLlm()
    svc.external = ExternalSourcesClient(MCP_EXTERNAL_URL)  # 活栈 AA-MCP-02 实链路

    case_id = await _investigating_case(repo, 60)       # 不记信号 → 无特征保守全查路径
    out = await svc.run(case_id)
    queries = {q["source"] for q in out["plan"]["queries"]}
    assert queries == {"credit", "sentiment", "complaint", "enterprise", "stat"}  # 五源全查（含 stat 建议线 BA-BR-25）
    ent = next(f for f in out["plan"]["findings"] if f["source"] == "enterprise")
    assert ent["ok"] is True
    for dim in ("reg_status", "abnormal_ops_count", "admin_penalty_12m",
                "judicial_risk_count", "related_entity_count", "risk_flag"):
        assert dim in ent["summary"]                    # 五维 + 合成标记齐备留痕

    subject = (await repo.get(case_id))["subject_ref"]
    direct = await svc.external.query_enterprise(subject, "SC-23 五维取样")
    assert direct["source"] == "enterprise-mock"        # 无厂商 Key → 默认 mock 轨
    assert direct["degraded"] is False
    assert direct["reg_status"] in ("active", "cancelled", "revoked")
    assert direct["risk_flag"] in ("low", "mid", "high")
    denied = await svc.external.query_enterprise(subject, "")
    assert denied.get("code") == "E-REASON-REQUIRED"    # BA-BR-10 查询事由门
    replay = await svc.external.query_enterprise(subject, "SC-23 五维取样")
    assert replay == direct                             # 同主体确定性回放一致

    ev = await pool.fetch(
        "SELECT claim FROM case_evidence WHERE case_id=$1", case_id)
    assert any("enterprise" in r["claim"] for r in ev)  # 企业源入证据链可审计
    assert (await repo.get(case_id))["risk_score"] == 60  # 仅线索不裁决：不改评分


# ---------- SC-24 统计异常检测建议线降级不阻断（BA-BR-25，US-E16，API-M-17~19） ----------

async def test_matrix_sc24_stat_advisory_degrades_without_blocking(pool, investigation):
    """Given 无特征案件（保守全查含 stat 建议线）；When 调查执行；Then stat 项
    入计划并留痕（本地无 pyod → E-TOOL-UNAVAILABLE 降级；装了 pyod → advisory 成功），
    两种结果之一均不阻断主链；且 advisory/降级均不改评分（仅参谋不裁决）"""
    from app.skills.mcp_adapters import ExternalSourcesClient
    svc, repo, _ = investigation

    class _NoLlm:
        available = False  # 隔离外呼非确定性（同 SC-23）
    svc.llm_client = _NoLlm()
    svc.external = ExternalSourcesClient(MCP_EXTERNAL_URL)  # 活栈 AA-MCP-02 实链路

    case_id = await _investigating_case(repo, 58)
    out = await svc.run(case_id)

    queries = {q["source"] for q in out["plan"]["queries"]}
    assert "stat" in queries                          # 保守全查路径纳入建议线
    st = next(f for f in out["plan"]["findings"] if f["source"] == "stat")
    if st["ok"]:                                      # 环境装了 pyod/numpy → 成功档
        assert st["degraded"] is False
        assert "advisory" in st["summary"]            # 仅参谋分留痕
    else:                                             # 本地无 pyod → 降级档（活栈实况）
        assert st["degraded"] is True
        assert ("E-TOOL-UNAVAILABLE" in st["summary"]
                or "样本不足" in st["summary"])        # 两种降级留痕之一，均不阻断
    assert (await repo.get(case_id))["risk_score"] == 58  # 建议线不裁决：评分不变

    # 事由门直验：无 query_reason 调 pyod 工具拒绝（BA-BR-10 同门）
    denied = await svc.external.call_tool("pyod_iforest",
                                          values=[1.0, 2.0, 3.0, 4.0, 5.0],
                                          query_reason="")
    assert denied.get("code") == "E-REASON-REQUIRED"


# ---------- SC-25 优先级队列风险分级派生与 aging 留痕（BA-BR-26，US-E17，API-W-28） ----------

async def test_matrix_sc25_priority_queue_risk_first(api_client, case_repo):
    """Given 多案在办；When 取优先级队列；Then 高分案置顶且分级/aging 字段齐备，
    归档案件不入队（队列按风险优先而非立案时序，主管看板主动管理）"""
    repo, _ = case_repo
    # 顶格分档（100/99）：共享库残留案件中 ≥99 分极少，自身案件稳入队首区
    hi = await repo.register(await _subject(), risk_score=100, source_type="TEST")
    lo = await repo.register(await _subject(), risk_score=99, source_type="TEST")
    arc = await repo.register(await _subject(), risk_score=99, source_type="TEST")
    # 立即推进出 REGISTERED：EventWorker 2s 轮询抢跑窗口免疫（同直插法理据）
    for c in (hi, lo):
        r = await repo.transition(c["case_id"], CaseEvent.AGGREGATION_STARTED,
                                  "agent:AA-AG-02", 0)
        await repo.transition(c["case_id"], CaseEvent.SIGNALS_AGGREGATED,
                              "agent:AA-AG-02", r["version"])
    r = await repo.transition(arc["case_id"], CaseEvent.AGGREGATION_STARTED,
                              "agent:AA-AG-02", 0)
    await repo.transition(arc["case_id"], CaseEvent.NOISE_DISMISSED,
                          "agent:AA-AG-02", r["version"])

    resp = api_client.get("/api/cases/queue?size=200")
    assert resp.status_code == 200
    body = resp.json()
    assert body["threshold_hours"] == 24              # br-26-aging-hours 种子缺省
    ids = [c["case_id"] for c in body["items"]]
    assert hi["case_id"] in ids and lo["case_id"] in ids
    assert ids.index(hi["case_id"]) < ids.index(lo["case_id"])
    assert arc["case_id"] not in ids
    hi_row = next(c for c in body["items"] if c["case_id"] == hi["case_id"])
    assert hi_row["priority_tier"] == "high"          # ≥70 复用 BA-BR-02 审批线
    assert hi_row["aging_hours"] >= 0 and isinstance(hi_row["aging_breach"], bool)


# ---------- SC-26 叙事生成引用对齐防幻觉（BA-BR-27，US-E18，API-W-30，docs/13 D2） ----------

async def test_matrix_sc26_narrative_citation_alignment(pool, app_pool, api_client,
                                                        aggregation):
    """Given 案件含信号与证据；When 人工角色生成叙事；Then 五段 DRAFT 全引用对齐
    且生成留痕；When agent 调用；Then 403（叙事可追责到人，同 BA-BR-23 模式）"""
    svc, repo, _ = aggregation
    case_id = await _investigating_case(repo, score=60)
    sid = uuid.uuid4().hex
    await app_pool.execute(  # risk_signal 写角色为 tg_app（DA-INV-05，02-roles.sql）
        """INSERT INTO risk_signal (signal_id, case_id, source, type, confidence,
                                    raw_ref, query_reason)
           VALUES ($1, $2, 'tx', 'velocity_anomaly', 0.8, 'test', 'SC-26')""",
        sid, case_id)
    await svc.core.record_case_evidence(
        case_id, [{"claim": "SC-26 叙事素材证据", "source_ref": "AA-AG-03:sc26",
                   "confidence": 0.9}])

    r = api_client.post(f"/api/cases/{case_id}/narrative",
                        headers={"X-Operator": urllib.parse.quote("风控值班员")})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "DRAFT" and body["track"] == "rule"
    assert [s["heading"] for s in body["sections"]] == \
        ["案件概况", "风险信号", "证据链", "处置记录", "审批记录"]
    assert f"[SIG:{sid[:8]}]" in body["citations"]    # 引用自素材全集，无据论断不可构造
    assert await pool.fetchval(
        "SELECT count(*) FROM audit_log WHERE target=$1 AND action='narrative.generated'",
        case_id) >= 1                                 # 生成行为留痕可追责

    r = api_client.post(f"/api/cases/{case_id}/narrative",
                        headers={"X-Operator": "agent:AA-AG-04"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "E-FORBIDDEN-ROLE"


# ---------- SC-27 可治理自动关闭与人工复位（BA-BR-28，US-E19，API-W-29） ----------

async def test_matrix_sc27_governed_auto_close_and_reopen(pool, api_client,
                                                          aggregation, app_pool,
                                                          case_repo):
    """Given 零信号案件；When 金额在标准内聚合；Then 降噪归档且 case.auto_closed
    留痕带标准引用；When 热配置收紧标准使金额超限；Then 转调查不自动关闭；
    When 值班员复位；Then ARCHIVED→MANUAL_REVIEW；审批官越权 → 403，agent → 409
    （403=角色无权/409=业务门拒，语义分层）"""
    from conftest import FakeExternal
    svc, repo, _ = aggregation
    svc.external = FakeExternal(credit_band="low", complaint_items=0)

    # 1) 标准内自动关闭：零信号 + 金额 800 < 5000（br-28 缺省标准）
    subject = await _subject()
    await _seed_tx(app_pool, subject, amount=800.0)
    reg = await repo.register(subject, risk_score=20, source_type="TEST")
    result = await svc.run(reg["case_id"])
    assert result["route"] == "noise" and result["status"] == "ARCHIVED"
    assert await pool.fetchval(
        "SELECT count(*) FROM audit_log WHERE target=$1 AND action='case.auto_closed'",
        reg["case_id"]) == 1                          # 关闭留痕恰好一条
    basis = await pool.fetchval(
        "SELECT basis FROM audit_log WHERE target=$1 AND action='case.auto_closed'",
        reg["case_id"])
    assert "br-28-auto-close-max-amount" in basis     # 留痕带当时标准引用可复算

    # 2) 热配置收紧（br-28=100）：同档金额 800 超限 → 转调查不自动关闭
    svc.config = type("C", (), {"values": {"br-28-auto-close-max-amount": "100"}})()
    subject2 = await _subject()
    await _seed_tx(app_pool, subject2, amount=800.0)
    reg2 = await repo.register(subject2, risk_score=20, source_type="TEST")
    result2 = await svc.run(reg2["case_id"])
    assert result2["route"] == "investigate" and result2["status"] == "INVESTIGATING"
    assert await pool.fetchval(
        "SELECT count(*) FROM audit_log WHERE target=$1 AND action='case.auto_closed'",
        reg2["case_id"]) == 0

    # 3) 复位通道：值班员 → MANUAL_REVIEW；审批官 403；agent 409（语义分层）
    body = {"basis": "SC-27 误关补救复位验证"}
    r = api_client.post(f"/api/cases/{reg['case_id']}/reopen", json=body,
                        headers={"X-Operator": urllib.parse.quote("风控审批官")})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "E-FORBIDDEN-ROLE"
    r = api_client.post(f"/api/cases/{reg['case_id']}/reopen", json=body,
                        headers={"X-Operator": "agent:AA-AG-02"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "E-HUMAN-ONLY"
    r = api_client.post(f"/api/cases/{reg['case_id']}/reopen", json=body,
                        headers={"X-Operator": urllib.parse.quote("风控值班员")})
    assert r.status_code == 200 and r.json()["status"] == "MANUAL_REVIEW"
    assert await pool.fetchval(
        "SELECT count(*) FROM audit_log WHERE target=$1 "
        "AND action='case.transition.CaseReopened'", reg["case_id"]) == 1
