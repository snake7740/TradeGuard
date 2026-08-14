# -*- coding: utf-8 -*-
"""MCP 客户端适配器（官方 streamable-http 通道，02 §5 / 04 §10.1）

- ExternalSourcesClient：AA-MCP-02 外部数据源（征信/舆情/投诉，mcp-external-mock :8102）
- CoreClient：AA-MCP-01 业务库 MCP（mcp-core :8101）——信号落库与处置执行走
  tg_app 写角色（02-roles.sql 权限矩阵，DA-INV-05），web-api 不越权直写。
工具返回 JSON 字符串，统一解析为 dict；工具内错误码（E-*）原样透传给调用方裁决。
"""
from __future__ import annotations

import json
import logging

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger("tradeguard.mcp")


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
        return await self.call_tool("query_credit",
                                    subject_id=subject_id, query_reason=query_reason)

    async def query_sentiment(self, subject_id: str, query_reason: str) -> dict:
        return await self.call_tool("query_sentiment",
                                    subject_id=subject_id, query_reason=query_reason)

    async def query_complaints(self, subject_id: str, query_reason: str) -> dict:
        return await self.call_tool("query_complaint",
                                    subject_id=subject_id, query_reason=query_reason)


class CoreClient(McpToolClient):
    """AA-MCP-01：聚合落库（API-M-10）/处置执行（API-M-03）/建单（API-M-11）/
    证据固化（API-M-12）/关联加分（API-M-13）/核验回查（API-M-04/06）/入库申请（API-M-05）"""

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

    async def record_case_evidence(self, case_id: str, claims: list[dict]) -> dict:
        """API-M-12：证据链固化 DA-T-05（只增，BA-BR-03，US-E4-03）"""
        return await self.call_tool("record_case_evidence", case_id=case_id, claims=claims)

    async def apply_risk_bonus(self, case_id: str, points: int, basis: str) -> dict:
        """API-M-13：BA-BR-06 关联网络命中加分（同案同依据幂等）"""
        return await self.call_tool("apply_risk_bonus", case_id=case_id,
                                    points=points, basis=basis)

    async def query_disposition_result(self, exec_id: str) -> dict:
        """API-M-04：处置结果回查（AA-SK-04 核验依据）"""
        return await self.call_tool("query_disposition_result", exec_id=exec_id)

    async def query_audit_trail(self, case_id: str) -> list:
        """API-M-06：审计链回放（只读，SC-08）"""
        return await self.call_tool("query_audit_trail", case_id=case_id)

    async def submit_kb_application(self, case_id: str, category: str,
                                    title: str, content: str) -> dict:
        """API-M-05：知识入库申请（AA-SK-05，仅 pending，发布须人工，DA-INV-06）"""
        return await self.call_tool("submit_kb_application", case_id=case_id,
                                    category=category, title=title, content=content)

    async def record_agent_memory(self, case_id: str, agent_id: str,
                                  stage: str, summary: dict) -> dict:
        """API-M-14：Agent 执行摘要落 DA-T-12（agent_memory 仅 tg_app 可 INSERT）"""
        return await self.call_tool("record_agent_memory", case_id=case_id,
                                    agent_id=agent_id, stage=stage, summary=summary)


async def remember(core: CoreClient, case_id: str, agent_id: str,
                   stage: str, summary: dict) -> None:
    """DA-T-12 agent_memory 摘要写入：失败仅告警不阻断技能主流程"""
    try:
        await core.record_agent_memory(case_id, agent_id, stage, summary)
    except Exception:  # noqa: BLE001 —— 记忆写入非关键路径
        logger.exception("agent_memory 写入失败：case=%s stage=%s", case_id, stage)
