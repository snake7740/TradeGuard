"""API 请求/响应模型（与 docs/openapi/tradeguard-openapi.yaml 逐条对齐）
契约纪律（07 §5）：Schema 变更必须先改 openapi.yaml 再改本文件。
"""
from datetime import datetime

from pydantic import BaseModel, Field


class AlertIn(BaseModel):
    """API-W-01 告警受理入参"""
    # R-37 复审收口：max_length 对齐 risk_case.subject_ref varchar(64)，超长入口拒绝防截断 500
    subject_ref: str = Field(..., max_length=64, description="事件主体（account_hash 或 tx_id）")
    source_type: str = Field("demo_script", pattern="^(engine_alert|fraud_ticket|tx_anomaly|demo_script)$")
    severity: str = Field("medium", pattern="^(low|medium|high)$")


# severity 枚举 → 初始风险种子分映射（契约：low=25 / medium=55 / high=85）
SEVERITY_SCORES = {"low": 25, "medium": 55, "high": 85}


class ReviewIn(BaseModel):
    """API-W-07 中风险人工复核（SC-10，BA-BP-05）"""
    conclusion: str = Field(..., pattern="^(release|block|escalate)$",
                            description="release=排除归档；block=确认欺诈建单；escalate=升级建单")
    opinion: str = Field(..., min_length=5, max_length=500)


class DecideIn(BaseModel):
    """API-W-09 批准/驳回（SC-02/SC-03，02 §7 人类触发入口）"""
    decision: str = Field(..., pattern="^(approve|reject)$")
    opinion: str = Field(..., min_length=5, max_length=500)


class VerifyIn(BaseModel):
    """API-W-19 结果核验入参（AA-SK-04，US-E6-01/02）"""
    exec_id: str = Field(..., description="待核验处置执行凭证（DA-T-06）")


class DispositionIn(BaseModel):
    """API-W-23 处置提交入参（US-E5-02，SC-02/07/10 载体）
    调查完成后的人工提交入口：高风险无凭证 → E-DISP-AUTH 建单转待审批。"""
    action: str = Field(..., pattern="^(block|freeze|reduce|release)$",
                        description="处置动作（白名单与 API-M-11 requested_action 对齐）")
    amount: float | None = Field(None, ge=0, le=1e7,
                                 description="处置金额（reduce 限额等场景，可选）")
    idempotency_key: str | None = Field(None, max_length=120,
                                        description="幂等键（缺省由服务端按 case+action 生成）")


class KbPublishIn(BaseModel):
    """API-W-12/13 知识发布确认/驳回（DA-INV-06 人工门控）"""
    # operator 可选：缺省由端点取 X-Operator 头（当前登录角色，门户自动携带），
    # 无头时回落 human:kb_admin（兼容直调/旧客户端）；显式传值时仍受 pattern 约束
    # R-37 复审收口：operator 总长 ≤40（audit_log.actor / kb_document.reviewer
    # 均 varchar(40)，此前正则无长度上限可致截断 500）；comment 对齐 basis varchar(300)
    operator: str | None = Field(None, pattern=r"^human:[a-z_]{1,34}$")
    comment: str = Field("", max_length=300)


class TransactionEvent(BaseModel):
    """上游交易系统 → 风控 的标准交易事件契约（阶段 0，R-39，行业 L1 实时决策输入）

    与 API-W-01 AlertIn（告警受理简化入口）互补：AlertIn 面向演示的「告警」，
    本契约面向真实交易流的字段完整性（最小充分集，可随真实源扩展）。
    device/ip/merchant/contact 四字段即 UnifiedModel 四类边（03 §3）的实体来源。
    """
    transaction_id: str = Field(..., max_length=64, description="交易唯一标识（幂等键）")
    account_hash: str = Field(..., max_length=64, description="主体账户哈希（去标识）")
    amount: float = Field(..., ge=0, le=1e7, description="交易金额（与处置金额 [0,1e7] 同域）")
    currency: str = Field("CNY", max_length=8, description="币种")
    occurred_at: datetime = Field(..., description="交易发生时间（velocity 窗口计算基准）")
    channel: str = Field(..., pattern="^(CNP|CP|P2P)$", description="渠道：CNP 无卡 / CP 有卡 / P2P 转账")
    device_fingerprint: str | None = Field(None, max_length=128, description="设备指纹（SAME_DEVICE 边）")
    ip_address: str | None = Field(None, max_length=64, description="IP（SAME_IP 边）")
    merchant_id: str | None = Field(None, max_length=64, description="收款方（SAME_PAYEE 边）")
    contact_hash: str | None = Field(None, max_length=128, description="联系方式（SAME_CONTACT 边）")
