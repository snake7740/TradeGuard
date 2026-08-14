# -*- coding: utf-8 -*-
"""AA-MCP-02 契约测试（US-E3-02，06 §3 契约层：每个 MCP 工具 ≥2 用例 成功/失败）

对运行中的 mcp-external-mock（localhost:8102）经官方 MCP streamable-http 通道实测：
① 成功 Schema（source/subject_id/query_reason/degraded 四键在场）；
② 错误码表（缺查询事由 → E-REASON-REQUIRED，BA-BR-10）；
③ 确定性（同主体两次调用载荷一致，合成数据可复现承诺）。
"""
import pytest

from app.skills.mcp_adapters import ExternalSourcesClient
from tests.conftest import MCP_EXTERNAL_URL

TOOLS = ("query_credit", "query_sentiment", "query_complaint")


@pytest.fixture(scope="module")
def client():
    return ExternalSourcesClient(MCP_EXTERNAL_URL)


@pytest.mark.parametrize("tool", TOOLS)
async def test_missing_reason_rejected(tool, client):
    """错误码表：外部源查询缺 query_reason 必须返回 E-REASON-REQUIRED（BA-BR-10）"""
    result = await client.call_tool(tool, subject_id="acct-contract-test")
    assert result.get("code") == "E-REASON-REQUIRED"


@pytest.mark.parametrize("tool", TOOLS)
async def test_success_schema(tool, client):
    """成功 Schema：防腐层输入四键在场，degraded 标志显式"""
    result = await client.call_tool(tool, subject_id="acct-contract-test",
                                    query_reason="contract-test US-E3-02")
    for key in ("source", "subject_id", "query_reason", "degraded"):
        assert key in result, f"{tool} 响应缺 {key}"
    assert result["degraded"] is False
    assert result["query_reason"] == "contract-test US-E3-02"


@pytest.mark.parametrize("tool", TOOLS)
async def test_deterministic_payload(tool, client):
    """合成数据确定性：同主体两次调用载荷逐字段一致（演示可复现）"""
    a = await client.call_tool(tool, subject_id="acct-contract-test", query_reason="r1")
    b = await client.call_tool(tool, subject_id="acct-contract-test", query_reason="r1")
    assert a == b
