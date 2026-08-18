# -*- coding: utf-8 -*-
"""第二场景 device-guard（账户盗用守护）同骨架实证矩阵（SC-DG-01~05）

场景叙事：设备指纹异常信号（device_anomaly）+ 同设备多账户（SAME_DEVICE 图谱边，
v_graph_edge 由 transaction.device_fp_hash 派生）构成账户盗用/团伙盗刷场景，
与 trade-guard（交易反欺诈：跑分/盗卡/大额）并列的第二业务场景。
本矩阵证明骨架零改动复用：状态机、BA-BR-01/02 门控、审批闭环、审计链、
处置幂等（DA-INV-03）、KB 记忆反哺（R-48）、AG-01 合规互审（R-47）、
核验归档与复盘入库（AA-SK-04/05）。元素级映射见
docs/10-场景扩展映射-device-guard.md。
"""
import os
import uuid

from app.core.state_machine import CaseEvent
from app.skills.disposition import DispositionService
from app.skills.investigation import InvestigationService
from app.skills.knowledge import index_document
from app.skills.mcp_adapters import CoreClient

MCP_CORE_URL = os.getenv("TG_TEST_MCP_CORE", "http://127.0.0.1:8101/mcp/")
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1")
os.environ.setdefault("no_proxy", "localhost,127.0.0.1")


class NoKey:
    """无凭据 LLM 通道：规划/互审走规则版（矩阵确定性）"""

    available = False

    async def chat(self, *a, **k):  # pragma: no cover - 不应被调用
        raise AssertionError("unavailable client must not call chat")


class FlatExternal:
    """三源平返还（device 场景调查外部输入，同源无干扰）"""

    async def query_credit_report(self, subject_id, query_reason):
        return {"source": "credit-mock", "degraded": False}

    async def query_sentiment(self, subject_id, query_reason):
        return {"source": "sentiment-mock", "hits": [], "degraded": False}

    async def query_complaints(self, subject_id, query_reason):
        return {"source": "complaint-mock", "items": [], "degraded": False}


def _inv_svc(pool, repo, pub):
    return InvestigationService(
        pool=pool, cases=repo, core=CoreClient(MCP_CORE_URL),
        pub=pub, external=FlatExternal(), llm_client=NoKey())


def _disp_svc(pool, repo, pub):
    return DispositionService(
        pool=pool, cases=repo, core=CoreClient(MCP_CORE_URL),
        pub=pub, llm_client=NoKey())


async def _seed_device_shared_tx(app_pool, accounts, device_fp: str):
    """多账户同设备指纹交易 → SAME_DEVICE 边（03-umodel-fallback.sql 派生源）"""
    for acct in accounts:
        await app_pool.execute(
            """INSERT INTO transaction (tx_id, account_hash, amount, mcc, channel, ts,
                                        device_fp_hash)
               VALUES ($1, $2, 300.00, '5411', 'CNP', now(), $3)""",
            f"dg-{uuid.uuid4().hex[:12]}", acct, device_fp)


async def _seed_account(app_pool, account: str, list_flag: str):
    await app_pool.execute(
        """INSERT INTO account (account_hash, risk_level, list_flag)
           VALUES ($1, 1, $2) ON CONFLICT DO NOTHING""",
        account, list_flag)


async def _device_case(repo, score: int) -> tuple[str, str]:
    """device 场景案件：立案（TEST 源，KPI 口径隔离）→ INVESTIGATING"""
    subject = uuid.uuid4().hex
    reg = await repo.register(subject, risk_score=score, source_type="TEST")
    case_id = reg["case_id"]
    r = await repo.transition(case_id, CaseEvent.AGGREGATION_STARTED, "agent:AA-AG-02", 0)
    await repo.transition(case_id, CaseEvent.SIGNALS_AGGREGATED, "agent:AA-AG-02", r["version"])
    return case_id, subject


async def _device_signal(core, case_id: str, score: int = 55):
    """device 异常信号（score 与案件同源：record_case_signals 会同步刷新风险分）"""
    await core.record_case_signals(case_id, score, [{
        "source": "tx", "type": "device_anomaly", "confidence": 0.8,
        "raw_ref": f"{case_id}:dev", "query_reason": "device-guard", "velocity_json": None}])


async def _audit_actions(pool, case_id: str) -> list[str]:
    rows = await pool.fetch(
        "SELECT action FROM audit_log WHERE target=$1 OR basis LIKE '%'||$1||'%'", case_id)
    return [r["action"] for r in rows]


# ---------- SC-DG-01 同设备团伙定性 + KB 引用（假设引擎/图谱边零改动复用） ----------

async def test_sc_dg01_same_device_gang_hypothesis_with_kb(pool, app_pool, case_repo):
    """SAME_DEVICE 边 → 规则假设「团伙盗刷」（match_hypothesis 零改动命中）；
    预发布团伙盗刷手法文档 → 调查结论引用 doc_id（SC-05 引用纪律复用）。"""
    repo, pub = case_repo
    core = CoreClient(MCP_CORE_URL)
    doc_id = uuid.uuid4().hex
    await app_pool.execute(
        """INSERT INTO kb_document (doc_id, category, title, content, status, applicant)
           VALUES ($1, 'case', '团伙账户盗用同设备手法复盘',
                   '团伙盗刷 手法特征：同设备指纹关联多账户，团伙账户盗用，'
                   '夜间集中交易；处置先例为先冻结后降额。',
                   'pending', 'AA-AG-05')""", doc_id)
    await index_document(pool, doc_id, operator="human:strategist")

    case_id, subject = await _device_case(repo, score=55)
    other = uuid.uuid4().hex
    await _seed_device_shared_tx(app_pool, [subject, other], uuid.uuid4().hex)
    await _device_signal(core, case_id)

    out = await _inv_svc(pool, repo, pub).run(case_id)

    assert out["hypothesis"]["pattern"] == "团伙盗刷"     # SAME_DEVICE → 规则定性
    assert out["graph"]["edges"] >= 1                     # 图谱边进入影响面
    assert out["hypothesis"]["citations"][0]["doc_id"] == doc_id  # KB 引用可追溯


# ---------- SC-DG-02 黑名单邻居加分（BA-BR-06 复用，幂等） ----------

async def test_sc_dg02_blacklist_neighbor_bonus_idempotent(pool, app_pool, case_repo):
    """同设备黑名单邻居（2 跳内 list_flag=black）→ +30 加分；同依据重投不叠加。"""
    repo, pub = case_repo
    core = CoreClient(MCP_CORE_URL)
    case_id, subject = await _device_case(repo, score=55)
    black = uuid.uuid4().hex
    await _seed_account(app_pool, black, "black")
    await _seed_device_shared_tx(app_pool, [subject, black], uuid.uuid4().hex)
    await _device_signal(core, case_id)

    out = await _inv_svc(pool, repo, pub).run(case_id)

    # 黑名单命中写入调查结论证据（影响面团伙边界可回放）
    claim = await pool.fetchval(
        "SELECT claim FROM case_evidence WHERE case_id=$1"
        " AND source_ref='AA-AG-03:investigation'", case_id)
    assert "黑名单命中 1 主体" in claim
    score = await pool.fetchval("SELECT risk_score FROM risk_case WHERE case_id=$1", case_id)
    assert score == 85                                        # 55 + 30（BA-BR-06 复用）
    again = await core.apply_risk_bonus(case_id, 30, "BA-BR-06 关联网络命中黑名单主体")
    assert again["applied"] is False                          # 幂等不叠加


# ---------- SC-DG-03 中风险边界（BA-BR-01 零改动复用） ----------

async def test_sc_dg03_mid_risk_auto_disposition_refused(pool, case_repo):
    """device 场景中风险案件（40≤55<70）无凭证自动处置被拒（E-DISP-SCOPE），
    仅审计留痕不建单——人机边界对新场景同样生效。"""
    repo, pub = case_repo
    case_id, _ = await _device_case(repo, score=55)

    out = await _disp_svc(pool, repo, pub).submit(case_id, "freeze", None, f"{case_id}:freeze")

    assert out["route"] == "refused_mid_risk" and out["code"] == "E-DISP-SCOPE"
    n = await pool.fetchval(
        "SELECT count(*) FROM approval_record WHERE case_id=$1", case_id)
    assert n == 0                                              # 不建单，仅审计留痕
    assert "disposition.refused" in await _audit_actions(pool, case_id)


# ---------- SC-DG-04 高风险全链闭环 + AG-01 互审 + 核验归档 + 幂等重投 ----------

async def test_sc_dg04_full_chain_gate_review_verify_archive(pool, app_pool, case_repo,
                                                             verification):
    """device 场景全链：调查定性（团伙盗刷）→ 高风险冻结建单（AG-01 互审并入）
    → 人工批准 → 执行 → 核验一致归档 → 复盘入库申请；全动作审计可回放；
    同幂等键重投返回首次凭证不重复执行（DA-INV-03）。"""
    repo, pub = case_repo
    ver_svc, _, _ = verification
    core = CoreClient(MCP_CORE_URL)
    case_id, subject = await _device_case(repo, score=82)
    await _seed_device_shared_tx(app_pool, [subject, uuid.uuid4().hex], uuid.uuid4().hex)
    await _device_signal(core, case_id, score=82)

    inv = await _inv_svc(pool, repo, pub).run(case_id)
    assert inv["hypothesis"]["pattern"] == "团伙盗刷"

    disp = _disp_svc(pool, repo, pub)
    gate = await disp.submit(case_id, "freeze", None, f"{case_id}:freeze")
    assert gate["route"] == "approval_required"
    opinion = await pool.fetchval(
        "SELECT opinion FROM approval_record WHERE approval_id=$1", gate["approval_id"])
    assert "AG-01 互审" in opinion                      # R-47 互审对 device 场景同样生效

    approved = await disp.approve(gate["approval_id"], "human:approver", "设备团伙证据充分")
    assert approved["route"] == "executed"

    ver = await ver_svc.verify(case_id, approved["exec_id"])
    assert ver["consistency_check"] is True
    assert ver["case_status"] == "ARCHIVED"
    kb_doc = await pool.fetchrow(
        "SELECT status, applicant FROM kb_document WHERE doc_id=$1", ver["kb_application"])
    assert kb_doc["status"] == "pending" and kb_doc["applicant"] == "AA-AG-05"  # 复盘入库申请

    actions = await _audit_actions(pool, case_id)      # 全链审计回放（BA-BR-09）
    for a in ("investigation.complete", "disposition.reviewed", "approval.create",
              "approval.decide", "disposition.submit", "verification.run"):
        assert a in actions, f"审计链缺 {a}"

    replay = await core.execute_disposition(           # SC-DG-05 幂等重投（并入全链）
        case_id, "freeze", None, f"{case_id}:freeze:{gate['approval_id']}",
        approval_ref=gate["approval_id"])
    assert replay["code"] == "E-IDEMPOTENT-CONFLICT"
    n = await pool.fetchval(
        "SELECT count(*) FROM disposition_record WHERE case_id=$1", case_id)
    assert n == 1                                      # 不重复执行（DA-INV-03）


# ---------- SC-DG-05 KB 记忆反哺（device 场景差异化闭环，R-48 复用） ----------

async def test_sc_dg05_kb_feedback_grounded_device_scenario(pool, app_pool, case_repo):
    """device_anomaly 信号无图谱边 → 规则假设待定；KB 沉淀 device 手法文档后，
    同源信号新案件被反哺定性——第二场景的记忆进化闭环（KPI-06 同机制实证）。"""
    repo, pub = case_repo
    core = CoreClient(MCP_CORE_URL)
    case_a, _ = await _device_case(repo, score=55)
    await _device_signal(core, case_a)
    out_a = await _inv_svc(pool, repo, pub).run(case_a)
    assert out_a["hypothesis"]["pattern"] == "待定"          # 无知识：交人工定性

    doc_id = uuid.uuid4().hex
    await app_pool.execute(
        """INSERT INTO kb_document (doc_id, category, title, content, status, applicant)
           VALUES ($1, 'case', 'device_anomaly 手法复盘',
                   'device_anomaly 手法特征：设备指纹异常，跨账户异地登录尝试，'
                   '账户盗用前兆；处置先例为限权观察。',
                   'pending', 'AA-AG-05')""", doc_id)
    await index_document(pool, doc_id, operator="human:strategist")

    case_b, _ = await _device_case(repo, score=55)
    await _device_signal(core, case_b)
    out_b = await _inv_svc(pool, repo, pub).run(case_b)
    assert out_b["hypothesis"]["pattern"] == "device_anomaly 手法复盘"  # 反哺定性
    assert out_b["hypothesis"]["citations"][0]["doc_id"] == doc_id
