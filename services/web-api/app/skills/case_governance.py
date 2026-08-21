# -*- coding: utf-8 -*-
"""案件运营治理纯函数层（BA-BR-26 优先级队列 / BA-BR-28 自动关闭标准，docs/14 v1.7）

业务定位：告警积压不是检测问题而是案件管理问题（行业共识，docs/09 v1.3 赛道对标）——
本模块提供两条治理能力的确定性内核（单测目标，06 §3）：

1. 优先级分级与超期判定（BA-BR-26）：队列按风险优先而非时间戳排布，
   分级复用既有业务边界（BA-BR-02 审批线 70 / BA-BR-01 自动线 40），零新参数；
2. 自动关闭准入标准（BA-BR-28）：零信号降噪归档须同时满足涉案金额上限，
   标准经 sys_config/Nacos 热配置（br-28-auto-close-max-amount），执行留痕带标准引用。

编排消费方：app/api/cases.py（queue/reopen 端点）与 app/skills/aggregation.py
（noise 分支准入）；本模块零外部依赖。
"""
from __future__ import annotations

from datetime import datetime, timezone

# ---------- BA-BR-26 优先级队列 ----------

# 分级边界复用既有业务阈值：≥70 触及审批线（BA-BR-02），≥40 脱离自动放行档（BA-BR-01）
TIER_HIGH_MIN = 70
TIER_MID_MIN = 40
AGING_HOURS_DEFAULT = 24  # br-26-aging-hours 缺省：活跃案件超期阈值


def priority_tier(risk_score: int) -> str:
    """风险分级派生：high（≥70）/ mid（40~69）/ low（<40）——
    边界与 BA-BR-02/BA-BR-01 业务阈值同源，队列按档排布而非按立案时序"""
    if risk_score >= TIER_HIGH_MIN:
        return "high"
    if risk_score >= TIER_MID_MIN:
        return "mid"
    return "low"


def aging_hours(updated_at: datetime, now: datetime) -> float:
    """案件自上次推进以来的滞留小时数（队列 aging 看板口径）"""
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return max(0.0, round((now - updated_at).total_seconds() / 3600, 1))


def aging_breach(hours: float, threshold_hours: int = AGING_HOURS_DEFAULT) -> bool:
    """超期判定：滞留超过阈值即标记（主管看板主动管理而非事后追责，BA-BR-26）"""
    return hours > float(threshold_hours)


# ---------- BA-BR-28 可治理自动关闭 ----------

AUTO_CLOSE_MAX_AMOUNT_DEFAULT = 5000  # br-28-auto-close-max-amount 缺省：与 BA-BR-01 涉案上限同源


def auto_close_eligible(signals_count: int, amount: float,
                        max_amount: int = AUTO_CLOSE_MAX_AMOUNT_DEFAULT) -> bool:
    """自动关闭准入：零信号 且 涉案金额低于标准上限（标准由策略管理员经配置
    通道修订，执行留痕引用当时标准值，BA-BR-28）；任一不满足转人工调查"""
    return signals_count == 0 and amount < float(max_amount)
