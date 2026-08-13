"""API 请求/响应模型（与 docs/openapi/tradeguard-openapi.yaml 逐条对齐）
契约纪律（07 §5）：Schema 变更必须先改 openapi.yaml 再改本文件。
"""
from pydantic import BaseModel, Field


class AlertIn(BaseModel):
    """API-W-01 告警受理入参"""
    subject_ref: str = Field(..., description="事件主体（account_hash 或 tx_id）")
    source_type: str = Field("demo_script", pattern="^(engine_alert|fraud_ticket|tx_anomaly|demo_script)$")
    severity: int = Field(50, ge=0, le=100)


class ReviewIn(BaseModel):
    """API-W-07 中风险人工复核（SC-10，BA-BP-05）"""
    decision: str = Field(..., pattern="^(confirm|dismiss)$", description="confirm=确认欺诈转审批；dismiss=排除欺诈归档")
    comment: str = Field("", max_length=500)
    operator: str = Field("human:risk_officer", pattern=r"^human:[a-z_]+$")


class DecideIn(BaseModel):
    """API-W-09 批准/驳回（SC-02/SC-03，02 §7 人类触发入口）"""
    decision: str = Field(..., pattern="^(approved|rejected)$")
    comment: str = Field("", max_length=500)
    approver: str = Field("human:approver", pattern=r"^human:[a-z_]+$")


class VerifyIn(BaseModel):
    """API-W-19 结果核验入参（AA-SK-04，US-E6-01/02）"""
    exec_id: str = Field(..., description="待核验处置执行凭证（DA-T-06）")


class KbPublishIn(BaseModel):
    """API-W-12/13 知识发布确认/驳回（DA-INV-06 人工门控）"""
    operator: str = Field("human:kb_admin", pattern=r"^human:[a-z_]+$")
    comment: str = Field("", max_length=500)
