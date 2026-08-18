# -*- coding: utf-8 -*-
"""R-49 动态处置分派测试：AG-03 调查结论 → AG-04 动作协商。

覆盖：rule_dispatch 确定性三档 + 团伙无佐证不升档边界、dispatch_action
LLM 白名单采纳 / 非法输出降级 / 无 Key 降级、EventWorker 委托路径
动作随调查结论（影响账户数 / KB 引用）动态变化（替换硬编码 freeze）。
"""
import asyncio

from app.core.event_worker import EventWorker, SingleFlight
from app.skills.planner import (
    DISPATCH_ACTIONS,
    dispatch_action,
    rule_dispatch,
)


class NoKey:
    """无 Key 环境：available=False，chat 不得被调用"""

    available = False

    async def chat(self, *a, **k):  # pragma: no cover - 不应被调用
        raise AssertionError("unavailable client must not call chat")


class StubLlm:
    """可控 LLM stub：available=True，chat 返回预制文本"""

    def __init__(self, reply: str):
        self.reply = reply

    @property
    def available(self) -> bool:
        return True

    async def chat(self, messages, temperature=0.2) -> str:
        return self.reply


# ---------- rule_dispatch 确定性档位 ----------


def test_rule_dispatch_block_for_gang_with_citation():
    """影响账户 ≥3 且 KB 引用 ≥1（团伙规模 + 手法佐证）→ block，优先于分数档"""
    assert rule_dispatch(50, 5, 2) == "block"
    assert rule_dispatch(90, 3, 1) == "block"


def test_rule_dispatch_freeze_for_high_score():
    """risk_score ≥ 审批线 70 → freeze"""
    assert rule_dispatch(75, 1, 0) == "freeze"
    assert rule_dispatch(70, 2, 0) == "freeze"


def test_rule_dispatch_reduce_for_mid_risk():
    """中风险且无团伙佐证 → reduce"""
    assert rule_dispatch(50, 1, 0) == "reduce"
    assert rule_dispatch(69, 2, 0) == "reduce"


def test_rule_dispatch_gang_without_citation_not_block():
    """影响账户 ≥3 但无 KB 佐证：团伙规模证据不足，不升 block（防过度处置）"""
    assert rule_dispatch(85, 4, 0) == "freeze"
    assert rule_dispatch(50, 9, 0) == "reduce"


# ---------- dispatch_action LLM 协商与降级 ----------


def test_dispatch_no_key_falls_back_to_rule():
    """无 Key：直接返回规则档位（闭环不断）"""
    assert asyncio.run(dispatch_action(75, 0, 0, client=NoKey())) == "freeze"
    assert asyncio.run(dispatch_action(50, 5, 2, client=NoKey())) == "block"


def test_dispatch_llm_whitelisted_action_wins():
    """LLM 合法白名单动作优先于规则档（语义协商价值）"""
    out = asyncio.run(dispatch_action(
        50, 0, 0,
        client=StubLlm('{"action": "block", "reason": "团伙作案扩大影响面"}')))
    assert out == "block"


def test_dispatch_llm_illegal_output_falls_back():
    """LLM 输出非法（非 JSON / 白名单外动作）→ 降级规则档"""
    assert asyncio.run(dispatch_action(
        75, 0, 0, client=StubLlm("我认为应该立即拦截"))) == "freeze"
    assert asyncio.run(dispatch_action(
        75, 0, 0, client=StubLlm('{"action": "kill"}'))) == "freeze"
    assert DISPATCH_ACTIONS == ("block", "freeze", "reduce")


# ---------- EventWorker 委托接线：动作随调查结论动态变化 ----------
#
# 接线测试替身 dispatch_action 为确定性 rule 档：LLM 协商/降级已有上方单测
# 覆盖，此处验证参数传递与提交动作，防配 Key 环境真实外呼导致非确定。


class _Pool:
    def __init__(self, risk_score):
        self.risk_score = risk_score

    async def fetch(self, query, *args):
        return [{"case_id": "C1"}]

    async def fetchrow(self, query, *args):
        return {"status": "INVESTIGATING", "risk_score": self.risk_score}


class _Inv:
    """调查替身：返回可控 impact/hypothesis（AG-03 结论形状同 investigation.run）"""

    def __init__(self, accounts, citations):
        self.accounts = accounts
        self.citations = citations

    async def run(self, case_id):
        return {
            "impact": {"accounts": self.accounts, "amount_24h": 1200.0},
            "hypothesis": {
                "pattern": "团伙盗刷",
                "citations": [{"doc_id": f"D{i}"} for i in range(self.citations)],
            },
        }


class _Disp:
    def __init__(self):
        self.calls: list[tuple] = []

    async def submit(self, case_id, action, amount, idempotency_key,
                     approval_ref=None):
        self.calls.append((case_id, action, idempotency_key))
        return {"route": "approval_required"}


class _Agg:
    async def run(self, case_id):  # pragma: no cover - 委托路径不触聚合
        raise AssertionError("delegate sweep must not call aggregation")


def _patch_dispatch_to_rule(monkeypatch):
    """替身 dispatch_action → 确定性 rule 档，并记录调用参数供接线断言"""
    seen: list[tuple[int, int, int]] = []

    async def fake(risk_score, impact_accounts, citations, client=None):
        seen.append((risk_score, impact_accounts, citations))
        return rule_dispatch(risk_score, impact_accounts, citations)

    monkeypatch.setattr("app.skills.planner.dispatch_action", fake)
    return seen


async def test_delegate_action_follows_investigation_gang_conclusion(monkeypatch):
    """调查结论为团伙规模（账户 5 + 引用 2）→ 委托提单动作 block（R-49）"""
    seen = _patch_dispatch_to_rule(monkeypatch)
    disp = _Disp()
    w = EventWorker(_Pool(risk_score=60), _Agg(), SingleFlight(),
                    investigation=_Inv(accounts=5, citations=2), disposition=disp)
    await w._delegate_sweep()
    assert seen == [(60, 5, 2)]                      # AG-03 结论完整传入
    assert disp.calls == [("C1", "block", "C1:delegate")]


async def test_delegate_action_reduces_for_solo_mid_risk(monkeypatch):
    """单主体中风险无佐证 → 委托提单动作 reduce（不再硬编码 freeze）"""
    seen = _patch_dispatch_to_rule(monkeypatch)
    disp = _Disp()
    w = EventWorker(_Pool(risk_score=55), _Agg(), SingleFlight(),
                    investigation=_Inv(accounts=1, citations=0), disposition=disp)
    await w._delegate_sweep()
    assert seen == [(55, 1, 0)]
    assert disp.calls == [("C1", "reduce", "C1:delegate")]
