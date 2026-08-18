# -*- coding: utf-8 -*-
"""R-47 Agent 互审测试（AG-04 处置建议 → AG-01 合规审查，SC-02 建单路径）

覆盖：规则互审证据分档（空→escalate / 单薄+重处置→concerns / 充分→pass）、
LLM 互审 JSON 解析与 verdict 白名单、降级保底（不可用/失败/非法输出→规则版）、
approval_required 端到端嵌入（审批单 opinion 互审标记、case_evidence cross-review
claim、audit disposition.reviewed 留痕；建单与状态转移不受互审影响，02 §3.3）。
"""
import json
import uuid
from typing import Any

from app.core.state_machine import CaseEvent
from app.skills import planner as P
from app.skills.disposition import DispositionService


class StubLlm:
    """可控 LLM stub：available=True，chat 返回预制文本或抛异常"""

    def __init__(self, reply: str | None = None, fail: bool = False):
        self.reply = reply
        self.fail = fail

    @property
    def available(self) -> bool:
        return True

    async def chat(self, messages, temperature=0.2) -> str:
        if self.fail:
            raise RuntimeError("stub llm down")
        assert self.reply is not None
        return self.reply


class NoKey:
    """无凭据通道：available=False，互审必须走规则版且不触网"""

    available = False

    async def chat(self, *a, **k):  # pragma: no cover - 不应被调用
        raise AssertionError("unavailable client must not call chat")


def _ev(n: int) -> list[dict[str, Any]]:
    return [{"claim": f"证据{i}", "source_ref": f"S{i}", "confidence": 0.8}
            for i in range(n)]


# ---------- 规则互审：证据充分性 / 处置恰当性分档 ----------

def test_rule_review_empty_evidence_escalates():
    v = P.rule_review("freeze", None, 82, [])
    assert v.verdict == "escalate"
    assert "证据链为空" in v.findings[0]


def test_rule_review_single_evidence_heavy_action_concerns():
    v = P.rule_review("freeze", None, 82, _ev(1))
    assert v.verdict == "concerns"
    assert any("重处置" in f for f in v.findings)


def test_rule_review_rich_evidence_passes():
    v = P.rule_review("freeze", 100.0, 82, _ev(3))
    assert v.verdict == "pass" and v.source == "rule"


# ---------- LLM 互审：解析 / 白名单 / 降级 ----------

def test_review_llm_parses_verdict():
    import asyncio
    raw = json.dumps({"verdict": "escalate",
                      "findings": ["证据链为空，处置依据不足"],
                      "summary": "建议审批官先核实"}, ensure_ascii=False)
    v = asyncio.run(P.review_disposition("freeze", None, 82, [], client=StubLlm(raw)))
    assert v.source == "llm" and v.verdict == "escalate" and v.findings


def test_review_llm_invalid_verdict_degrades_to_rule():
    import asyncio
    v = asyncio.run(P.review_disposition("freeze", None, 82, _ev(3),
                                         client=StubLlm(json.dumps({"verdict": "ok"}))))
    assert v.source == "rule" and v.verdict == "pass"


def test_review_llm_failure_degrades_to_rule():
    import asyncio
    v = asyncio.run(P.review_disposition("freeze", None, 82, [],
                                         client=StubLlm(fail=True)))
    assert v.source == "rule" and v.verdict == "escalate"


def test_review_unavailable_client_never_calls_chat():
    import asyncio
    v = asyncio.run(P.review_disposition("freeze", None, 82, [], client=NoKey()))
    assert v.source == "rule"


# ---------- 端到端：approval_required 建单路径嵌入互审（规则版确定性） ----------

async def test_submit_cross_review_embedded_in_approval_gate(pool, disposition):
    """score=82 无凭证 freeze → 建单前 AG-01 互审：审批单 opinion 带互审标记、
    证据链落 cross-review claim、审计 disposition.reviewed；状态机照常推进
    PENDING_APPROVAL（人机边界：互审只建议不决策）。"""
    svc, repo, pub = disposition
    deterministic = DispositionService(
        pool=pool, cases=repo, core=svc.core, pub=pub, llm_client=NoKey())

    reg = await repo.register(uuid.uuid4().hex, risk_score=82, source_type="TEST")
    case_id = reg["case_id"]
    r = await repo.transition(case_id, CaseEvent.AGGREGATION_STARTED, "agent:AA-AG-02", 0)
    await repo.transition(case_id, CaseEvent.SIGNALS_AGGREGATED, "agent:AA-AG-02", r["version"])
    await deterministic.core.record_case_evidence(
        case_id, [{"claim": "持卡人否认交易且设备指纹关联多账户",
                   "source_ref": "AA-AG-03:test-evidence", "confidence": 0.9}])

    gate = await deterministic.submit(case_id, "freeze", None, f"{case_id}:freeze")

    # 建单与状态转移不受互审影响（02 §3.3：LLM 只建议，决策权在人工审批）
    assert gate["route"] == "approval_required" and gate["code"] == "E-DISP-AUTH"
    assert (await repo.get(case_id))["status"] == "PENDING_APPROVAL"
    # 审批单 opinion 并入互审标记（1 条证据 + freeze → 规则版 concerns）
    rec = await pool.fetchrow(
        "SELECT opinion FROM approval_record WHERE approval_id=$1", gate["approval_id"])
    assert "AG-01 互审" in rec["opinion"] and "concerns" in rec["opinion"]
    # 互审结论固化证据链（DA-T-05 只增，claim+source_ref 幂等去重）
    claims = await pool.fetch(
        "SELECT claim FROM case_evidence WHERE case_id=$1"
        " AND source_ref='AA-AG-01:cross-review'", case_id)
    assert claims and "互审" in claims[0]["claim"]
    # 审计留痕（BA-BR-09，可回放）
    audit = await pool.fetchval(
        "SELECT basis FROM audit_log WHERE target=$1 AND action='disposition.reviewed'",
        case_id)
    assert audit and "verdict=concerns" in audit
