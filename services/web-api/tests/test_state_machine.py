"""状态机单元测试（R-22 补债，US-E5-06 验收基线，06 §3）

覆盖：18 条合法迁移全量参数化 + 非法迁移拒绝 + human_only actor 守卫（Right-BICEP）。
"""
import pytest

from app.core.state_machine import (
    TRANSITIONS, CaseEvent, CaseState, InvalidTransition, allowed_events, next_state,
)

AGENT = "agent:AA-AG-01"
HUMAN = "human:approver-01"


@pytest.mark.parametrize("t", TRANSITIONS, ids=lambda t: f"{t.source.value}+{t.event.value}->{t.target.value}")
def test_all_18_transitions_positive(t):
    """Right：迁移表每条 (from,event) 都到达定义的目标状态"""
    actor = HUMAN if t.human_only else AGENT
    assert next_state(t.source, t.event, actor) == t.target


def test_transition_count_is_18():
    """与 02 §7 stateDiagram + BA-BP 展开逐条对账（防迁移表悄悄增删）"""
    assert len(TRANSITIONS) == 18
    assert len({t.source for t in TRANSITIONS}) >= 10


@pytest.mark.parametrize("state,event", [
    (CaseState.REGISTERED, CaseEvent.APPROVAL_APPROVED),        # 未聚合不得审批
    (CaseState.AGGREGATING, CaseEvent.DISPOSITION_EXECUTED),    # 未批准不得执行处置
    (CaseState.PENDING_APPROVAL, CaseEvent.DISPOSITION_SUBMITTED),  # 未批准不得提交处置
    (CaseState.ARCHIVED, CaseEvent.SIGNALS_AGGREGATED),         # 终态不可复活
    (CaseState.VERIFIED, CaseEvent.VERIFICATION_FAILED),        # 已核验不得再失败
])
def test_invalid_transition_rejected(state, event):
    """Error/Boundary：DA-INV-01 非法迁移必须抛 E-BAD-TRANSITION"""
    with pytest.raises(InvalidTransition) as ei:
        next_state(state, event, AGENT)
    assert ei.value.code == "E-BAD-TRANSITION"


@pytest.mark.parametrize("t", [t for t in TRANSITIONS if t.human_only])
def test_human_only_guard_rejects_agent(t):
    """Inverse：human_only 迁移被 agent actor 触发必须拒绝（SC-02 门控根基）"""
    with pytest.raises(InvalidTransition) as ei:
        next_state(t.source, t.event, AGENT)
    assert ei.value.code == "E-HUMAN-ONLY"


@pytest.mark.parametrize("t", [t for t in TRANSITIONS if t.human_only])
def test_human_only_guard_accepts_human(t):
    """human:* 前缀操作者可触发人类入口"""
    assert next_state(t.source, t.event, HUMAN) == t.target


def test_human_only_transitions_are_6():
    """对账：审批 2 + 调查中复核 2 + MANUAL_REVIEW 复核 2 = 6 条人类入口"""
    assert sum(1 for t in TRANSITIONS if t.human_only) == 6


def test_allowed_events_terminal_state_empty():
    """ARCHIVED 为终态，无后续事件"""
    assert allowed_events(CaseState.ARCHIVED) == []


def test_full_happy_path_chain():
    """端到端正链：立案→聚合→调查→批准→处置→核验→归档（02 §7 主路径）"""
    s = CaseState.REGISTERED
    chain = [
        (CaseEvent.AGGREGATION_STARTED, AGENT),
        (CaseEvent.SIGNALS_AGGREGATED, AGENT),
        (CaseEvent.INVESTIGATION_COMPLETED, AGENT),
        (CaseEvent.APPROVAL_APPROVED, HUMAN),
        (CaseEvent.DISPOSITION_SUBMITTED, AGENT),
        (CaseEvent.DISPOSITION_EXECUTED, AGENT),
        (CaseEvent.VERIFICATION_PASSED, AGENT),
        (CaseEvent.CASE_ARCHIVED, AGENT),
    ]
    for event, actor in chain:
        s = next_state(s, event, actor)
    assert s == CaseState.ARCHIVED
