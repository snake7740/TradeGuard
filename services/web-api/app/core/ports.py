# -*- coding: utf-8 -*-
"""领域端口（Port）——边界接口契约（阶段 0，R-39）

将「可替换性」从文档承诺固化为代码契约：下游处置执行、外部数据源二者以
Protocol 定义端口，现有 MCP Adapter（CoreClient / ExternalSourcesClient）为
其实例。生产接真实系统时仅换 Adapter，端口与调用方零改动。

对标行业分层风控（04 §2 技术选型 + 09 社区对标）：
- DispositionExecutor ≈ L4 处置执行的下游契约（幂等 + 回执 + 对账 + 审批门控）
- ExternalDataSource  ≈ L2 外部数据/特征源的降级契约（鉴权 + 限流 + 超时 + 事由）

契约要求（Adapter 必须满足，写入 docstring 而非仅文档）：
- 幂等（DA-INV-03）：同 idempotency_key 重复调用返回同 exec_id，不重复执行
- 回执：返回执行态；失败态可重试/对账
- 对账：exec_id 可回查最终状态（AA-SK-04 核验依据）
- 门控（DA-INV-02）：高风险动作须审批，approval_ref 验真
- 鉴权：外部数据源凭据经网关透传，调用方零密钥（04 §4.1/§5）
- 事由（BA-BR-10）：征信/舆情查询强制携带 query_reason
- 降级：数据源不可用返回 degraded 标记，不抛断链

方法名与现有 Adapter 对齐（execute_disposition / query_disposition_result /
query_credit_report / query_sentiment / query_complaints），使现有实现零改动即满足端口；
真实系统 Adapter 实现同名方法即可无缝替换。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DispositionExecutor(Protocol):
    """下游处置执行端口（行业 L4，真实账户系统 Adapter 必须满足上述契约）"""

    async def execute_disposition(self, case_id: str, action: str, amount: float | None,
                                  idempotency_key: str,
                                  approval_ref: str | None = None) -> dict:
        """执行处置动作（freeze/block/reduce/release）。
        返回 DispositionResult 语义：{exec_id, action, status, reason}。
        status ∈ executed/failed/refused；failed 须可重试，refused 为门控拒绝。"""
        ...

    async def query_disposition_result(self, exec_id: str) -> dict:
        """按 exec_id 回查处置最终状态（AA-SK-04 核验与对账依据）"""
        ...


@runtime_checkable
class ExternalDataSource(Protocol):
    """外部数据源端口（行业 L2，真实数据源 Adapter 必须满足上述契约）"""

    async def query_credit_report(self, subject_id: str, query_reason: str) -> dict:
        """征信评分/逾期查询（BA-BR-10 事由必填）"""
        ...

    async def query_sentiment(self, subject_id: str, query_reason: str) -> dict:
        """涉诈舆情查询"""
        ...

    async def query_complaints(self, subject_id: str, query_reason: str) -> dict:
        """客服投诉查询"""
        ...




@runtime_checkable
class EmbeddingProvider(Protocol):
    """向量化端口（行业 L2 RAG/知识库检索，阶段 1，R-40）

    契约：同文本多次调用返回同维向量（确定性/可复现）；维度稳定（DA-T-10）。
    hash 基线（字符三元组哈希）为无外部依赖实现，生产替换语义 embedding
    （如 DashScope text-embedding-v3）仅换实现，调用方零改动。
    """

    async def embed(self, text: str) -> list[float]:
        """文本 → 定长向量（DA-T-10 pgvector 存储/检索用）"""
        ...


@runtime_checkable
class HypothesisRanker(Protocol):
    """根因假设排序端口（行业 L3 调查，AI 实质，阶段 1，R-40）

    契约：规则做候选召回 + 确定性兜底，LLM 做排序 + 生成可审计推理链；
    无 LLM 时降级规则版（人机边界不变：LLM 只建议、不做决策，02 §3.3）。
    返回 {pattern, basis, confidence, source[, reasoning]}。
    """

    async def rank(self, signals: list[dict], graph_edge_types: set[str],
                   kb_hints: str = "") -> dict:
        """按信号 + 图谱边 + 知识库提示（命中摘要），排序根因假设并给出可审计依据"""
        ...


@runtime_checkable
class RiskScorer(Protocol):
    """风险评分端口（行业 L2 风控模型，阶段3，R-42）

    契约：返回 {score, source}，score ∈ [0,100]；规则加权作 baseline，
    生产替换 GBDT/图特征模型仅换实现，调用方零改动。
    """

    def score(self, signals: list[dict], velocity: dict, **kwargs) -> dict:
        """信号 + velocity 特征 → {score, source}"""
        ...


__all__ = ["DispositionExecutor", "ExternalDataSource",
           "EmbeddingProvider", "HypothesisRanker", "RiskScorer"]

