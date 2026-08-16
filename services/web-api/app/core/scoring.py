# -*- coding: utf-8 -*-
"""风险评分适配器（阶段3，R-42）

baseline：RuleRiskScorer 封装 aggregation.score_signals（规则加权评分）。
生产替换 GBDT/图特征模型仅换实现（RiskScorer 端口），调用方零改动。
延迟 import 使本模块无 import 副作用，契约测试可独立加载。
"""
from __future__ import annotations


class RuleRiskScorer:
    """规则加权评分（baseline）：RiskScorer 端口实例"""

    def score(self, signals: list[dict], velocity: dict, **kwargs) -> dict:
        from app.skills.aggregation import score_signals
        return {"score": score_signals(signals, velocity, **kwargs), "source": "rule"}


__all__ = ["RuleRiskScorer"]
