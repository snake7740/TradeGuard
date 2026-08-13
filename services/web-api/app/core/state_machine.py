"""案件状态机（02 §7 审批与回滚设计 + 03 §9.2 事件目录，守护 DA-INV-01）

设计要点：
- 纯函数式迁移表：(状态, 事件) → 目标状态，非法迁移直接抛 InvalidTransition（DA-INV-01）；
- 事件命名对齐 03 §9.2 领域事件目录（Tag 即事件类型）；
- actor 守卫：人类触发入口（审批/复核）仅允许 human:* 操作者（04 §10.1）；
- 本模块零外部依赖，06 §3 状态机单元测试直接对其编写（先测后码）。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CaseState(str, Enum):
    """risk_case.status 12 态（与 01-schema.sql DA-T-03 CHECK 约束逐字一致）"""

    REGISTERED = "REGISTERED"            # 已立案
    AGGREGATING = "AGGREGATING"          # 信号聚合中（BA-BP-02）
    INVESTIGATING = "INVESTIGATING"      # 调查取证中（BA-BP-03）
    PENDING_APPROVAL = "PENDING_APPROVAL"  # 待审批（触及 BA-BR-01/02 边界）
    APPROVED = "APPROVED"                # 已批准
    REJECTED = "REJECTED"                # 已驳回
    MANUAL_REVIEW = "MANUAL_REVIEW"      # 人工复核（驳回回滚 BA-BR-07）
    DISPOSING = "DISPOSING"              # 处置执行中（AA-SK-03）
    DISPOSED = "DISPOSED"                # 已执行
    VERIFIED = "VERIFIED"                # 已核验（AA-SK-04）
    ROLLBACK = "ROLLBACK"                # 回滚执行（核验不一致→反向处置→升级 P0 转人工）
    ARCHIVED = "ARCHIVED"                # 已归档（BA-BP-04）


class CaseEvent(str, Enum):
    """状态迁移触发事件（03 §9.2 目录 + web-api 人类操作扩展）"""

    AGGREGATION_STARTED = "AggregationStarted"        # AA-AG-02 承接 CaseRegistered
    SIGNALS_AGGREGATED = "SignalsAggregated"          # 聚合完成→风险分级裁决入调查
    NOISE_DISMISSED = "NoiseDismissed"                # 低风险误报降噪放行（BA-BP-02 出口）
    INVESTIGATION_REQUESTED = "InvestigationRequested"  # 主控发起调查
    INVESTIGATION_COMPLETED = "InvestigationCompleted"  # 调查完成→处置建议转审批
    APPROVAL_APPROVED = "ApprovalApproved"            # 人类批准（SC-02）
    APPROVAL_REJECTED = "ApprovalRejected"            # 人类驳回（SC-03）
    DISPOSITION_SUBMITTED = "DispositionSubmitted"    # AA-SK-03 提交处置
    DISPOSITION_EXECUTED = "DispositionExecuted"      # AA-SK-03 幂等执行完成
    ROLLBACK_TO_REVIEW = "RollbackToReview"           # 驳回→人工复核（BA-BR-07）
    VERIFICATION_PASSED = "VerificationPassed"        # AA-SK-04 核验通过
    VERIFICATION_FAILED = "VerificationFailed"        # 核验不一致→反向处置
    ROLLBACK_EXECUTED = "RollbackExecuted"            # 反向处置完成→升级 P0 转人工
    REVIEW_CONFIRMED = "ReviewConfirmed"              # 人工复核确认欺诈（SC-10，web-api 扩展）
    REVIEW_DISMISSED = "ReviewDismissed"              # 人工复核排除欺诈（SC-10，web-api 扩展）
    CASE_ARCHIVED = "CaseArchived"                    # 结案归档（BA-BP-04）


@dataclass(frozen=True)
class Transition:
    source: CaseState
    event: CaseEvent
    target: CaseState
    human_only: bool = False  # True → actor 必须以 human: 开头（审批官/复核员，02 §7）


# 迁移表：02 §7 stateDiagram + BA-BP-02/03/05 全路径展开
TRANSITIONS: tuple[Transition, ...] = (
    Transition(CaseState.REGISTERED, CaseEvent.AGGREGATION_STARTED, CaseState.AGGREGATING),
    Transition(CaseState.AGGREGATING, CaseEvent.SIGNALS_AGGREGATED, CaseState.INVESTIGATING),
    Transition(CaseState.AGGREGATING, CaseEvent.NOISE_DISMISSED, CaseState.ARCHIVED),
    # BA-CAP-05 低风险自动通道（SC-01）：风险分<40 且涉案<5000（BA-BR-01）时聚合后直接提交
    # 放行处置，免审批单；边界守卫在聚合裁决层（app/skills/aggregation.py triage）全链路审计。
    Transition(CaseState.AGGREGATING, CaseEvent.DISPOSITION_SUBMITTED, CaseState.DISPOSING),
    Transition(CaseState.AGGREGATING, CaseEvent.INVESTIGATION_REQUESTED, CaseState.INVESTIGATING),
    Transition(CaseState.INVESTIGATING, CaseEvent.INVESTIGATION_COMPLETED, CaseState.PENDING_APPROVAL),
    Transition(CaseState.INVESTIGATING, CaseEvent.REVIEW_CONFIRMED, CaseState.PENDING_APPROVAL, human_only=True),
    Transition(CaseState.INVESTIGATING, CaseEvent.REVIEW_DISMISSED, CaseState.ARCHIVED, human_only=True),
    Transition(CaseState.PENDING_APPROVAL, CaseEvent.APPROVAL_APPROVED, CaseState.APPROVED, human_only=True),
    Transition(CaseState.PENDING_APPROVAL, CaseEvent.APPROVAL_REJECTED, CaseState.REJECTED, human_only=True),
    Transition(CaseState.APPROVED, CaseEvent.DISPOSITION_SUBMITTED, CaseState.DISPOSING),
    Transition(CaseState.DISPOSING, CaseEvent.DISPOSITION_EXECUTED, CaseState.DISPOSED),
    Transition(CaseState.REJECTED, CaseEvent.ROLLBACK_TO_REVIEW, CaseState.MANUAL_REVIEW),
    Transition(CaseState.DISPOSED, CaseEvent.VERIFICATION_PASSED, CaseState.VERIFIED),
    Transition(CaseState.DISPOSED, CaseEvent.VERIFICATION_FAILED, CaseState.ROLLBACK),
    Transition(CaseState.ROLLBACK, CaseEvent.ROLLBACK_EXECUTED, CaseState.MANUAL_REVIEW),
    Transition(CaseState.MANUAL_REVIEW, CaseEvent.REVIEW_CONFIRMED, CaseState.PENDING_APPROVAL, human_only=True),
    Transition(CaseState.MANUAL_REVIEW, CaseEvent.REVIEW_DISMISSED, CaseState.ARCHIVED, human_only=True),
    Transition(CaseState.VERIFIED, CaseEvent.CASE_ARCHIVED, CaseState.ARCHIVED),
)

_INDEX = {(t.source, t.event): t for t in TRANSITIONS}


class InvalidTransition(Exception):
    """DA-INV-01：非法状态迁移（含 actor 守卫失败）"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def next_state(current: CaseState, event: CaseEvent, actor: str) -> CaseState:
    """按迁移表计算下一状态；非法迁移 / 越权触发一律拒绝"""
    t = _INDEX.get((current, event))
    if t is None:
        raise InvalidTransition(
            "E-BAD-TRANSITION", f"非法迁移：{current.value} + {event.value} 不在状态机定义内（DA-INV-01）")
    if t.human_only and not actor.startswith("human:"):
        raise InvalidTransition(
            "E-HUMAN-ONLY", f"{event.value} 为人类触发入口，actor={actor} 无权执行（02 §7）")
    return t.target


def allowed_events(current: CaseState) -> list[CaseEvent]:
    return [ev for (st, ev) in _INDEX if st == current]
