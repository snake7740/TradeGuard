# -*- coding: utf-8 -*-
"""KPI-06 记忆进化量化 A/B 对照实证（R-48 记忆反哺定性）

同一"无规则特征"信号（match_hypothesis 恒待定）两组对照：
  A 组（无知识）：KB 无相关文档 → 假设待定 + 显式声明，交人工复核；
  B 组（有知识）：KB 已沉淀同手法文档 → 检索命中，假设升级为文档定性并
  引用 doc_id 留痕。两组差异即"知识沉淀→调查定性效率"的可测增益
  （scripts/kpi_report.py KPI-06 的测试级缩影：待定率 1.0 → 0.0）。
"""
import os
import uuid

from app.core.state_machine import CaseEvent
from app.skills.investigation import InvestigationService, match_hypothesis
from app.skills.knowledge import index_document
from app.skills.mcp_adapters import CoreClient

# 与 tests/conftest.py 同源（隐式相对导入 linter 不允许，本地等价定义）
MCP_CORE_URL = os.getenv("TG_TEST_MCP_CORE", "http://127.0.0.1:8101/mcp/")
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1")
os.environ.setdefault("no_proxy", "localhost,127.0.0.1")


class NoKey:
    """无凭据 LLM 通道：规划走规则版，保证 A/B 输入同源确定"""

    available = False

    async def chat(self, *a, **k):  # pragma: no cover - 不应被调用
        raise AssertionError("unavailable client must not call chat")


class FlatExternal:
    """三源平返还（A/B 同源外部输入，无干扰信号）"""

    async def query_credit_report(self, subject_id, query_reason):
        return {"source": "credit-mock", "degraded": False}

    async def query_sentiment(self, subject_id, query_reason):
        return {"source": "sentiment-mock", "hits": [], "degraded": False}

    async def query_complaints(self, subject_id, query_reason):
        return {"source": "complaint-mock", "items": [], "degraded": False}


def _svc(pool, repo, pub):
    return InvestigationService(
        pool=pool, cases=repo, core=CoreClient(MCP_CORE_URL),
        pub=pub, external=FlatExternal(), llm_client=NoKey())


async def _pending_case(repo, core) -> str:
    """无规则特征信号案件：velocity/large_amount/SAME_DEVICE 均不命中 → 待定"""
    reg = await repo.register(uuid.uuid4().hex, risk_score=55, source_type="TEST")
    case_id = reg["case_id"]
    r = await repo.transition(case_id, CaseEvent.AGGREGATION_STARTED, "agent:AA-AG-02", 0)
    await repo.transition(case_id, CaseEvent.SIGNALS_AGGREGATED, "agent:AA-AG-02", r["version"])
    await core.record_case_signals(case_id, 55, [{
        "source": "tx", "type": "device_anomaly", "confidence": 0.7,
        "raw_ref": f"{case_id}:dev", "query_reason": "kpi06-ab", "velocity_json": None}])
    return case_id


async def test_kpi06_ab_contrast_kb_grounded_vs_no_kb(pool, app_pool, case_repo):
    """A/B 对照：无知识组假设待定交人工；知识组 KB 反哺定性 + doc_id 引用留痕"""
    repo, pub = case_repo
    core = CoreClient(MCP_CORE_URL)

    # 前置自证：同源信号规则假设恒待定（A/B 输入同源）
    assert match_hypothesis([{"type": "device_anomaly", "velocity_json": None}],
                            set()) == ("待定", "")

    # A 组（无知识）：KB 未沉淀该手法文档
    case_a = await _pending_case(repo, core)
    out_a = await _svc(pool, repo, pub).run(case_a)
    assert out_a["hypothesis"]["pattern"] == "待定"
    assert out_a["hypothesis"]["citations"] == []
    assert out_a["hypothesis"]["kb_note"] == "无库内匹配"

    # B 组（有知识）：AA-AG-05 沉淀同手法复盘文档（human 发布，DA-INV-06）
    doc_id = uuid.uuid4().hex
    await app_pool.execute(
        """INSERT INTO kb_document (doc_id, category, title, content, status, applicant)
           VALUES ($1, 'case', 'device_anomaly 手法复盘',
                   'device_anomaly 手法特征：设备指纹异常关联多账户，夜间集中交易，'
                   '团伙盗刷前兆；处置先例为降额观察后升级。',
                   'pending', 'AA-AG-05')""", doc_id)
    await index_document(pool, doc_id, operator="human:strategist")

    case_b = await _pending_case(repo, core)
    out_b = await _svc(pool, repo, pub).run(case_b)

    # B 组：假设被库内知识反哺升级，且引用可追溯（SC-05）
    assert out_b["hypothesis"]["pattern"] == "device_anomaly 手法复盘"
    assert out_b["hypothesis"]["citations"][0]["doc_id"] == doc_id
    assert "反哺" in out_b["hypothesis"]["kb_note"]
    # 审计可回放：basis 的 hypothesis 已是反哺定性，citations≥1
    basis = await pool.fetchval(
        "SELECT basis FROM audit_log WHERE target=$1"
        " AND action='investigation.complete'", case_b)
    assert "hypothesis=device_anomaly 手法复盘" in basis and "citations=1" in basis

    # KPI-06 缩影断言：同源输入下，知识沉淀使待定率 1.0 → 0.0（定性增益 100%）
    pending = [out_a["hypothesis"]["pattern"] == "待定",
               out_b["hypothesis"]["pattern"] == "待定"]
    assert pending == [True, False]
