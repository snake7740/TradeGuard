# -*- coding: utf-8 -*-
"""MCP 客户端适配器（官方 streamable-http 通道，02 §5 / 04 §10.1）

- ExternalSourcesClient：AA-MCP-02 外部数据源（征信/舆情/投诉，mcp-external-mock :8102）
- CoreClient：AA-MCP-01 业务库 MCP（mcp-core :8101）——信号落库与处置执行走
  tg_app 写角色（02-roles.sql 权限矩阵，DA-INV-05），web-api 不越权直写。
工具返回 JSON 字符串，统一解析为 dict；工具内错误码（E-*）原样透传给调用方裁决。
"""
from __future__ import annotations

import json

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


class McpToolClient:
    """最小 MCP streamable-http 调用器：一次调用一会话（无状态，稳定优先）"""

    def __init__(self, url: str):
        self.url = url

    async def call_tool(self, name: str, **arguments) -> dict:
        async with streamablehttp_client(self.url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
                if result.isError:
                    raise RuntimeError(f"MCP 工具 {name} 执行失败：{result.content}")
                return json.loads(result.content[0].text)


class ExternalSourcesClient(McpToolClient):
    """AA-MCP-02：三外部源查询（query_reason 必填，BA-BR-10）"""

    async def query_credit_report(self, subject_id: str, query_reason: str) -> dict:
        return await self.call_tool("query_credit_report",
                                    subject_id=subject_id, query_reason=query_reason)

    async def query_sentiment(self, subject_id: str, query_reason: str) -> dict:
        return await self.call_tool("query_sentiment",
                                    subject_id=subject_id, query_reason=query_reason)

    async def query_complaints(self, subject_id: str, query_reason: str) -> dict:
        return await self.call_tool("query_complaints",
                                    subject_id=subject_id, query_reason=query_reason)


class CoreClient(McpToolClient):
    """AA-MCP-01：聚合结果落库（API-M-10）与处置执行（API-M-03）"""

    async def record_case_signals(self, case_id: str, risk_score: int, signals: list[dict]) -> dict:
        """AA-SK-01 步骤 6：信号 insert DA-T-04（只增）+ risk_score 回写 DA-T-03"""
        payload = [{k: v for k, v in s.items()
                    if k in ("signal_id", "source", "type", "confidence",
                             "raw_ref", "query_reason", "degraded", "velocity_json")}
                   for s in signals]
        for s in payload:  # datetime/复杂对象不入契约，落库 ts 取库端默认 now()
            s.pop("ts", None)
        # 直传数组：API-M-10 签名为 list[dict]（FastMCP 对 JSON 字符串参数会预解析，
        # 传字符串反被解成 list 造成服务端校验失败）
        return await self.call_tool("record_case_signals", case_id=case_id,
                                    risk_score=risk_score, signals=payload)

    async def execute_disposition(self, case_id: str, action: str, amount: float | None,
                                  idempotency_key: str, approval_ref: str | None = None) -> dict:
        """API-M-03：处置执行（审批门控 DA-INV-02 + 幂等 DA-INV-03）"""
        return await self.call_tool("execute_disposition", case_id=case_id, action=action,
                                    amount=amount, idempotency_key=idempotency_key,
                                    approval_ref=approval_ref)

    async def create_approval_request(self, case_id: str, action: str,
                                      amount: float | None, reason: str) -> dict:
        """API-M-11：处置审批工单创建（E-DISP-AUTH 门控建单，SC-02）"""
        return await self.call_tool("create_approval_request", case_id=case_id, action=action,
                                    amount=amount, reason=reason)
