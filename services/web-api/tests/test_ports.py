"""阶段 0 边界接口契约测试（R-39）

验证：现有 MCP Adapter 结构满足领域端口，TransactionEvent 契约字段可校验。
纯单元测试，不连数据库/MCP——端口契约是结构期保证（runtime_checkable）。
"""
import pytest
from pydantic import ValidationError

from app.core.ports import DispositionExecutor, ExternalDataSource
from app.schemas import TransactionEvent
from app.skills.mcp_adapters import CoreClient, ExternalSourcesClient


def test_external_sources_client_satisfies_port():
    """现有 AA-MCP-02 客户端结构满足 ExternalDataSource 端口（可替换性契约）"""
    assert isinstance(ExternalSourcesClient("http://127.0.0.1:8102/mcp/"),
                      ExternalDataSource)


def test_core_client_satisfies_disposition_port():
    """现有 AA-MCP-01 客户端结构满足 DispositionExecutor 端口（L4 处置契约）"""
    assert isinstance(CoreClient("http://127.0.0.1:8101/mcp/"),
                      DispositionExecutor)


def test_transaction_event_minimal():
    """上游交易事件契约：最小字段可实例化"""
    ev = TransactionEvent(transaction_id="tx1", account_hash="acc1", amount=100.0,
                          occurred_at="2026-08-16T00:00:00Z", channel="CNP")
    assert ev.amount == 100.0
    assert ev.currency == "CNY"


def test_transaction_event_rejects_bad_channel():
    """渠道枚举守门：契约外渠道拒绝（行业 L1 输入验证）"""
    with pytest.raises(ValidationError):
        TransactionEvent(transaction_id="tx1", account_hash="acc1", amount=100.0,
                         occurred_at="2026-08-16T00:00:00Z", channel="INVALID")


def test_transaction_event_amount_bounds():
    """金额域 [0,1e7]：与处置金额同域，负值拒绝"""
    with pytest.raises(ValidationError):
        TransactionEvent(transaction_id="tx1", account_hash="acc1", amount=-1.0,
                         occurred_at="2026-08-16T00:00:00Z", channel="CNP")


def test_transaction_event_carries_graph_edges():
    """四类边实体字段（device/ip/merchant/contact）承载 UnifiedModel 边来源"""
    ev = TransactionEvent(transaction_id="tx2", account_hash="acc2", amount=50.0,
                          occurred_at="2026-08-16T00:00:00Z", channel="P2P",
                          device_fingerprint="dev1", ip_address="1.2.3.4",
                          merchant_id="m1", contact_hash="c1")
    assert ev.device_fingerprint == "dev1"
    assert ev.contact_hash == "c1"


def test_hash_embedding_provider_satisfies_port():
    """baseline 哈希向量化满足 EmbeddingProvider 端口（RAG 检索契约）"""
    from app.core.llm_adapters import HashEmbeddingProvider
    from app.core.ports import EmbeddingProvider
    assert isinstance(HashEmbeddingProvider(), EmbeddingProvider)


def test_rule_hypothesis_ranker_satisfies_port():
    """baseline 规则假设排序满足 HypothesisRanker 端口（AI 调查契约）"""
    from app.core.llm_adapters import RuleHypothesisRanker
    from app.core.ports import HypothesisRanker
    assert isinstance(RuleHypothesisRanker(), HypothesisRanker)


async def test_hash_embedding_deterministic_1024d():
    """确定性：同文本同向量，维度 1024（DA-T-10 契约）"""
    from app.core.llm_adapters import HashEmbeddingProvider
    p = HashEmbeddingProvider()
    v1 = await p.embed("跑分团伙洗钱")
    v2 = await p.embed("跑分团伙洗钱")
    assert v1 == v2
    assert len(v1) == 1024


async def test_rule_hypothesis_ranker_returns_structure():
    """baseline 返回 {pattern,basis,confidence,source}，无命中=待定+0 置信"""
    from app.core.llm_adapters import RuleHypothesisRanker
    r = RuleHypothesisRanker()
    result = await r.rank([], set())
    assert result["source"] == "rule"
    assert result["pattern"] == "待定"
    assert result["confidence"] == 0.0


async def test_dashscope_embedding_falls_back_when_no_key():
    """无 Key 时语义 embedding 降级哈希（确定性，不依赖外部）"""
    from app.core.llm_adapters import DashScopeEmbeddingProvider, LlmClient
    p = DashScopeEmbeddingProvider(client=LlmClient(api_key=""))
    v = await p.embed("测试文本")
    assert len(v) == 1024


async def test_llm_hypothesis_ranker_falls_back_when_no_key():
    """无 Key 时 LLM 假设排序降级规则（人机边界不变，02 §3.3）"""
    from app.core.llm_adapters import LlmHypothesisRanker, LlmClient
    r = LlmHypothesisRanker(client=LlmClient(api_key=""))
    result = await r.rank([], set())
    assert result["source"] == "rule"


def test_llm_client_available_flag():
    """available 降级开关：空 Key=False，有 Key=True"""
    from app.core.llm_adapters import LlmClient
    assert LlmClient(api_key="").available is False
    assert LlmClient(api_key="sk-test").available is True


def test_rule_risk_scorer_satisfies_port():
    """baseline 规则评分满足 RiskScorer 端口（L2 风控模型契约）"""
    from app.core.scoring import RuleRiskScorer
    from app.core.ports import RiskScorer
    assert isinstance(RuleRiskScorer(), RiskScorer)


def test_rule_risk_scorer_scores_in_range():
    """规则评分返回 {score∈[0,100], source=rule}"""
    from app.core.scoring import RuleRiskScorer
    s = RuleRiskScorer()
    velocity = {"velocity_1h": {"count": 0, "amount": 0},
                "velocity_24h": {"count": 0, "amount": 0}}
    result = s.score([{"source": "credit", "confidence": 0.8}], velocity)
    assert 0 <= result["score"] <= 100
    assert result["source"] == "rule"
