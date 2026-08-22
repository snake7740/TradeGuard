# -*- coding: utf-8 -*-
"""mcp-core execute_disposition 门控加固活体验证（工作流 C1/C2/C3）

经真实 CoreClient→mcp-core(:8101) 实链路验证：
  C1 凭证严格验真（伪造/跨案/逆动作对豁免）
  C2 高风险 ROLLBACK 态 release 豁免、中风险段无凭证任何动作拒绝（含 release，纵深缺口闭合）
  C3 query_disposition_result / create_approval_request 存在性（E-NOT-FOUND）
"""
import uuid

import pytest

from app.core.state_machine import CaseEvent
from app.skills.mcp_adapters import CoreClient
from conftest import MCP_CORE_URL


async def _case(repo, score: int) -> str:
    reg = await repo.register(uuid.uuid4().hex, risk_score=score, source_type="TEST")
    return reg["case_id"]


async def _to_investigating(repo, case_id: str) -> None:
    r = await repo.transition(case_id, CaseEvent.AGGREGATION_STARTED, "agent:AA-AG-02", 0)
    await repo.transition(case_id, CaseEvent.SIGNALS_AGGREGATED, "agent:AA-AG-02", r["version"])


# ---------- C1：approval_ref 严格验真 ----------

async def test_c1_forged_approval_ref_refused(case_repo):
    """伪造凭证：不存在 approval_ref 一律 E-DISP-AUTH（验真缺位是 v1.4.3 前最大漏洞）"""
    repo, _ = case_repo
    core = CoreClient(MCP_CORE_URL)
    case_id = await _case(repo, score=82)
    await _to_investigating(repo, case_id)
    out = await core.execute_disposition(
        case_id, "block", None, f"{case_id}:forged", approval_ref=uuid.uuid4().hex)
    assert out["code"] == "E-DISP-AUTH"


async def test_c1_cross_case_approval_ref_refused(pool, case_repo, disposition):
    """张冠李戴：A 案已批准凭证用于 B 案处置 → E-DISP-AUTH"""
    repo, _ = case_repo
    svc = disposition[0]
    core = CoreClient(MCP_CORE_URL)
    # A 案：合法链取得已批准凭证（freeze 需证据链 DA-INV-04）
    case_a = await _case(repo, score=82)
    await _to_investigating(repo, case_a)
    await core.record_case_evidence(
        case_a, [{"claim": "C1 跨案测试证据", "source_ref": "test:c1", "confidence": 0.9}])
    gate = await svc.submit(case_a, "freeze", None, f"{case_a}:freeze")
    await svc.approve(gate["approval_id"], "human:approver", "同意A案冻结")
    # B 案：持 A 案凭证执行 → 案件不匹配被拒（先补证据链，越过 DA-INV-04 前置）
    case_b = await _case(repo, score=82)
    await _to_investigating(repo, case_b)
    await core.record_case_evidence(
        case_b, [{"claim": "C1 跨案测试 B 证据", "source_ref": "test:c1-b", "confidence": 0.9}])
    out = await core.execute_disposition(
        case_b, "freeze", None, f"{case_b}:stolen", approval_ref=gate["approval_id"])
    assert out["code"] == "E-DISP-AUTH"


async def test_c1_inverse_action_pair_exemption(pool, case_repo, disposition):
    """逆动作对豁免（C1 枢纽）：批准 freeze 即含纠正授权——同一凭证可执行反向
    release（核验回滚上下文，02 §7 审批生命周期的一部分）"""
    repo, _ = case_repo
    svc = disposition[0]
    core = CoreClient(MCP_CORE_URL)
    case_id = await _case(repo, score=82)
    await _to_investigating(repo, case_id)
    await core.record_case_evidence(
        case_id, [{"claim": "C1 逆对测试证据", "source_ref": "test:c1-inv", "confidence": 0.9}])
    gate = await svc.submit(case_id, "freeze", None, f"{case_id}:freeze")
    approved = await svc.approve(gate["approval_id"], "human:approver", "同意冻结")
    assert approved["route"] == "executed"
    # 同一凭证反向 release：requested_action=freeze 的逆对 == release → 放行
    out = await core.execute_disposition(
        case_id, "release", None, f"{case_id}:rollback", approval_ref=gate["approval_id"])
    assert "code" not in out and out["status"] == "executed"
    rows = await pool.fetch(
        "SELECT action FROM disposition_record WHERE case_id=$1 ORDER BY ts", case_id)
    assert [r["action"] for r in rows] == ["freeze", "release"]


# ---------- C2：高风险 release 豁免收紧 + 中风险段 ----------

async def test_c2_rollback_state_release_exempt(case_repo):
    """高风险无凭证 release：仅案件处于 ROLLBACK 态豁免（核验反向处置上下文）"""
    repo, _ = case_repo
    core = CoreClient(MCP_CORE_URL)
    case_id = await _case(repo, score=82)
    r = await repo.transition(case_id, CaseEvent.AGGREGATION_STARTED, "agent:AA-AG-02", 0)
    r = await repo.transition(case_id, CaseEvent.SIGNALS_AGGREGATED, "agent:AA-AG-02", r["version"])
    r = await repo.transition(case_id, CaseEvent.INVESTIGATION_COMPLETED, "agent:AA-AG-03", r["version"])
    r = await repo.transition(case_id, CaseEvent.APPROVAL_APPROVED, "human:approver", r["version"])
    r = await repo.transition(case_id, CaseEvent.DISPOSITION_SUBMITTED, "agent:AA-AG-04", r["version"])
    r = await repo.transition(case_id, CaseEvent.DISPOSITION_EXECUTED, "agent:AA-AG-04", r["version"])
    await repo.transition(case_id, CaseEvent.VERIFICATION_FAILED, "agent:AA-AG-05", r["version"])
    assert (await repo.get(case_id))["status"] == "ROLLBACK"
    out = await core.execute_disposition(case_id, "release", None, f"{case_id}:rb-exempt")
    assert "code" not in out and out["status"] == "executed"


async def test_c2_high_risk_release_no_rollback_state_refused(case_repo):
    """同是高风险无凭证 release，案件非 ROLLBACK 态不豁免（INVESTIGATING）→ E-DISP-AUTH"""
    repo, _ = case_repo
    core = CoreClient(MCP_CORE_URL)
    case_id = await _case(repo, score=82)
    await _to_investigating(repo, case_id)
    out = await core.execute_disposition(case_id, "release", None, f"{case_id}:release-na")
    assert out["code"] == "E-DISP-AUTH"


@pytest.mark.parametrize("action", ["block", "release"])
async def test_c2_mid_risk_no_approval_any_action_refused(action, case_repo):
    """中风险段（40-69）无凭证任何动作（含 release）→ E-DISP-SCOPE（mcp-core 层直验）：
    纵深缺口闭合——AgentTeams worker 经 mcporter 直调本工具不得绕过 web 层
    放行中风险 release，与 BA-BR-01「一律转人工复核」逐字同源"""
    repo, _ = case_repo
    core = CoreClient(MCP_CORE_URL)
    case_id = await _case(repo, score=55)
    await _to_investigating(repo, case_id)
    out = await core.execute_disposition(case_id, action, None, f"{case_id}:mid-{action}")
    assert out["code"] == "E-DISP-SCOPE"


# ---------- C3：存在性校验（E-NOT-FOUND，不再 500/FK 裸抛） ----------

async def test_c3_query_disposition_result_not_found(case_repo):
    core = CoreClient(MCP_CORE_URL)
    out = await core.query_disposition_result(uuid.uuid4().hex)
    assert out["code"] == "E-NOT-FOUND"


async def test_c3_create_approval_request_case_not_found(case_repo):
    core = CoreClient(MCP_CORE_URL)
    out = await core.create_approval_request("CASE-NOPE-404", "block", None, "不存在案件建单")
    assert out["code"] == "E-NOT-FOUND"
