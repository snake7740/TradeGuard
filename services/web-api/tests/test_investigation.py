# -*- coding: utf-8 -*-
"""E4 欺诈调查与关联分析集成测试（US-E4-01~03 验收，全链路实链路）

覆盖：AA-SK-02 假设匹配（规则兜底 + KB 检索引用 doc_id / 未命中显式声明）、
BA-BR-06 关联网络黑名单加分（幂等）、影响面统计、证据固化（DA-T-05 只增）、
DA-INV-04 冻结缺证据拒绝（E-EVIDENCE-MISSING）、复核确认自动建单（US-E5-04）。
"""
import uuid

from app.core.state_machine import CaseEvent


async def _subject() -> str:
    return uuid.uuid4().hex


async def _investigating_case(repo, score: int) -> str:
    reg = await repo.register(await _subject(), risk_score=score, source_type="TEST")
    case_id = reg["case_id"]
    r = await repo.transition(case_id, CaseEvent.AGGREGATION_STARTED, "agent:AA-AG-02", 0)
    await repo.transition(case_id, CaseEvent.SIGNALS_AGGREGATED, "agent:AA-AG-02", r["version"])
    return case_id


async def _seed_velocity_signals(core, case_id: str):
    """夜间高频小额特征（跑分假设输入，BA-BR-14 velocity 结构）"""
    await core.record_case_signals(case_id, 55, [{
        "source": "tx", "type": "velocity_anomaly", "confidence": 0.8,
        "raw_ref": f"{case_id}:tx", "query_reason": "test",
        "velocity_json": {"velocity_1h": {"count": 12, "amount": 900.0},
                          "velocity_24h": {"count": 18, "amount": 1500.0}}}])


async def _seed_graph_with_black_neighbor(app_pool, subject: str) -> str:
    """构造 SAME_PAYEE 边：主体 ↔ 黑名单账户共享收款方（BA-BR-06 命中条件）"""
    black = uuid.uuid4().hex
    payee = uuid.uuid4().hex
    for acct in (subject, black):
        await app_pool.execute(
            """INSERT INTO account (account_hash, risk_level, list_flag)
               VALUES ($1, 1, $2) ON CONFLICT DO NOTHING""",
            acct, "black" if acct == black else "none")
        await app_pool.execute(
            """INSERT INTO transaction (tx_id, account_hash, payee_hash, amount, mcc, channel, ts)
               VALUES ($1, $2, $3, 100.00, '5411', 'transfer', now())""",
            uuid.uuid4().hex, acct, payee)
    return black


# ---------- DA-INV-04 冻结证据链守卫（BA-BR-03，US-E4-03） ----------

async def test_da_inv04_freeze_without_evidence_rejected(pool, disposition):
    """无证据链的冻结被拒 E-EVIDENCE-MISSING，不产生处置记录"""
    svc, repo, _ = disposition
    case_id = await _investigating_case(repo, score=82)

    out = await svc.submit(case_id, "freeze", None, f"{case_id}:freeze")

    assert out["code"] == "E-EVIDENCE-MISSING"
    rows = await pool.fetch("SELECT * FROM disposition_record WHERE case_id=$1", case_id)
    assert rows == []                                        # BA-BR-03：缺一不可，拒绝写入


# ---------- US-E4-01 假设匹配（规则兜底 + KB 引用 doc_id） ----------

async def test_hypothesis_rule_match_with_kb_citation(pool, app_pool, investigation):
    """跑分假设命中规则兜底；KB 内已发布同手法定位文档，结论引用 doc_id（SC-05 联动）"""
    from app.skills.knowledge import index_document, search_kb
    svc, repo, _ = investigation
    case_id = await _investigating_case(repo, score=55)
    await _seed_velocity_signals(svc.core, case_id)

    # 知识前置：发布一篇跑分手法文档（human 路径，DA-INV-06 内建定向量化；
    # kb_document INSERT 权限仅 tg_app，02-roles.sql）
    doc_id = uuid.uuid4().hex
    await app_pool.execute(
        """INSERT INTO kb_document (doc_id, category, title, content, status, applicant)
           VALUES ($1, 'case', '夜间高频小额跑分手法复盘',
                   '夜间高频小额 transfer 跑分手法：1 小时内多笔小额转出，收款方集中。',
                   'pending', 'AA-AG-05')""", doc_id)
    await index_document(pool, doc_id, operator="human:strategist")

    out = await svc.run(case_id)

    assert out["hypothesis"]["pattern"] == "跑分"
    assert out["hypothesis"]["rule_basis"]                     # 规则兜底依据非空
    assert any(c["doc_id"] == doc_id for c in out["hypothesis"]["citations"])
    hits = await search_kb(pool, "夜间高频小额 transfer 跑分")
    assert hits and hits[0]["doc_id"] == doc_id                # AA-SK-02 检索可命中
    case = await repo.get(case_id)
    assert case["status"] == "PENDING_APPROVAL"                # InvestigationCompleted 移交


async def test_hypothesis_no_kb_match_declared_explicitly(pool, investigation):
    """KB 无该手法文档时显式声明'无库内匹配'，不虚构引用"""
    svc, repo, _ = investigation
    case_id = await _investigating_case(repo, score=75)
    # 单卡突发大额 → 盗卡假设（KB 内无盗卡文档——本套件仅发布跑分文档）
    await svc.core.record_case_signals(case_id, 75, [{
        "source": "tx", "type": "large_amount_burst", "confidence": 0.85,
        "raw_ref": f"{case_id}:tx", "query_reason": "test", "velocity_json": None}])

    out = await svc.run(case_id)

    assert out["hypothesis"]["pattern"] == "盗卡"
    assert out["hypothesis"]["citations"] == []
    assert out["hypothesis"]["kb_note"] == "无库内匹配"          # 未命中显式声明


# ---------- R-47 KB 提示接线（规划/假设排序的「知识库提示」真实输入） ----------

class _CapturePlanLlm:
    """捕获提示词的替身 LLM：按 system 角色区分规划/反思，返回合法 JSON"""

    available = True

    def __init__(self):
        self.prompts: list[str] = []

    async def chat(self, messages, temperature=0.2):
        import json

        self.prompts.append(messages[-1]["content"])
        if "调查规划员" in messages[0]["content"]:
            return json.dumps({
                "hypotheses": [{"pattern": "跑分", "priority": 1,
                                "rationale": "KB 提示同主题手法佐证"}],
                "queries": [{"source": "credit", "reason": "核验流水", "priority": 1}],
                "kb_queries": [], "skipped": [], "rationale": "测试计划"})
        return json.dumps({"verdict": "sufficient", "gaps": [], "summary": "ok"})


class _FlatExternalForHints:
    """四源平返还（计划执行的确定外部输入）"""

    async def query_credit_report(self, subject_id, query_reason):
        return {"source": "credit-mock", "degraded": False}

    async def query_sentiment(self, subject_id, query_reason):
        return {"source": "sentiment-mock", "hits": [], "degraded": False}

    async def query_complaints(self, subject_id, query_reason):
        return {"source": "complaint-mock", "items": [], "degraded": False}

    async def query_enterprise(self, subject_id, query_reason):
        return {"source": "enterprise-mock", "reg_status": "active",
                "abnormal_ops_count": 0, "admin_penalty_12m": 0,
                "judicial_risk_count": 0, "related_entity_count": 1,
                "risk_flag": "low", "query_reason": query_reason, "degraded": False}


async def test_plan_kb_hints_grounded_from_kb(pool, app_pool, case_repo):
    """AG-01 规划的「知识库提示」输入真实接线：规划前 KB 预检（信号特征词）
    命中摘要进入 LLM 提示词——此前生产恒传空串（宣称能力未接线的空槽，
    多角度 review 修复）；未命中场景由既有全量回归覆盖（空串=基线行为）"""
    import os

    from app.skills.investigation import InvestigationService
    from app.skills.knowledge import index_document
    from app.skills.mcp_adapters import CoreClient

    os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1")
    os.environ.setdefault("no_proxy", "localhost,127.0.0.1")
    repo, pub = case_repo
    cap = _CapturePlanLlm()
    svc = InvestigationService(
        pool=pool, cases=repo, core=CoreClient("http://127.0.0.1:8101/mcp/"),
        pub=pub, external=_FlatExternalForHints(), llm_client=cap)

    case_id = await _investigating_case(repo, score=55)
    await _seed_velocity_signals(svc.core, case_id)   # sig_types=["velocity_anomaly"]

    # 信号词必须写进 content：kb_embedding 只向量化 content（title 不参与向量，
    # knowledge.publish_and_index L94），预检词去空格后与 content 前缀共享
    # 「velocity_anomaly手法」18 连续字符 → cosine 远超 0.22 阈值，可靠命中
    title = f"velocity_anomaly 手法复盘-{uuid.uuid4().hex[:6]}"
    content = f"{title}：同设备指纹异常关联的夜间小额高频转出手法复盘。"
    doc_id = uuid.uuid4().hex
    await app_pool.execute(
        """INSERT INTO kb_document (doc_id, category, title, content, status, applicant)
           VALUES ($1, 'case', $2, $3, 'pending', 'AA-AG-05')""",
        doc_id, title, content)
    await index_document(pool, doc_id, operator="human:strategist")

    out = await svc.run(case_id)

    assert out["plan"]["source"] == "llm"                 # 替身被采纳（真实 LLM 分支）
    plan_prompt = next(p for p in cap.prompts if "知识库提示" in p)
    assert title in plan_prompt                          # 预检命中摘要进入提示词（接线断言）
    assert "无" != plan_prompt.split("知识库提示：")[1].split("\n")[0]


# ---------- US-E4-02 关联网络扩展 + BA-BR-06 加分 ----------

async def test_ba_br06_black_neighbor_bonus_applied_once(pool, app_pool, investigation):
    """2 跳内命中黑名单主体 +30 分；重复调查/重复调用幂等不叠加"""
    svc, repo, _ = investigation
    subject = await _subject()
    reg = await repo.register(subject, risk_score=55, source_type="TEST")
    case_id = reg["case_id"]
    r = await repo.transition(case_id, CaseEvent.AGGREGATION_STARTED, "agent:AA-AG-02", 0)
    await repo.transition(case_id, CaseEvent.SIGNALS_AGGREGATED, "agent:AA-AG-02", r["version"])
    await _seed_graph_with_black_neighbor(app_pool, subject)

    out = await svc.run(case_id)

    score = await pool.fetchval("SELECT risk_score FROM risk_case WHERE case_id=$1", case_id)
    assert score == 85                                         # 55 + 30（BA-BR-06）
    assert out["graph"]["nodes"] >= 2 and out["graph"]["edges"] >= 1
    # 幂等：同案同依据再次调用加分工具不叠加
    again = await svc.core.apply_risk_bonus(case_id, 30, "BA-BR-06 关联网络命中黑名单主体")
    assert again["applied"] is False
    assert await pool.fetchval("SELECT risk_score FROM risk_case WHERE case_id=$1", case_id) == 85


# ---------- US-E4-03 影响面报告与证据固化 ----------

async def test_impact_report_and_evidence_fixed(pool, app_pool, investigation):
    """影响面统计图内账户数/涉险金额；结论固化 DA-T-05（只增，BA-BR-03）"""
    svc, repo, _ = investigation
    subject = await _subject()
    reg = await repo.register(subject, risk_score=60, source_type="TEST")
    case_id = reg["case_id"]
    r = await repo.transition(case_id, CaseEvent.AGGREGATION_STARTED, "agent:AA-AG-02", 0)
    await repo.transition(case_id, CaseEvent.SIGNALS_AGGREGATED, "agent:AA-AG-02", r["version"])
    await _seed_graph_with_black_neighbor(app_pool, subject)

    out = await svc.run(case_id)

    assert out["impact"]["accounts"] >= 2                      # 主体 + 黑名单邻居
    assert out["impact"]["amount_24h"] >= 200                  # 两笔 100 元（近 24h）
    evs = await pool.fetch("SELECT * FROM case_evidence WHERE case_id=$1", case_id)
    assert len(evs) >= 1                                       # 调查结论固化证据链
    assert all(e["confidence"] > 0 for e in evs)


# ---------- 复核确认自动建单（US-E5-04，清 cases.py TODO） ----------

async def test_review_confirm_creates_approval_ticket(pool, disposition):
    """人工复核确认欺诈 → PENDING_APPROVAL + 自动建处置审批工单（API-M-11）"""
    svc, repo, _ = disposition
    case_id = await _investigating_case(repo, score=55)

    out = await svc.review_confirm(case_id, "human:reviewer", "确认团伙盗刷")

    assert out["status"] == "PENDING_APPROVAL"
    appr = await pool.fetchrow(
        "SELECT * FROM approval_record WHERE approval_id=$1", out["approval_id"])
    assert appr["decision"] == "pending" and appr["requested_action"] == "freeze"
    assert "确认团伙盗刷" in appr["opinion"]
