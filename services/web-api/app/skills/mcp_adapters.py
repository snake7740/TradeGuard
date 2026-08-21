"""MCP 客户端适配器（官方 streamable-http 通道，02 §5 / 04 §10.1）

- ExternalSourcesClient：AA-MCP-02 外部数据源（征信/舆情/投诉，mcp-external-mock :8102）
- CoreClient：AA-MCP-01 业务库 MCP（mcp-core :8101）——信号落库与处置执行走
  tg_app 写角色（02-roles.sql 权限矩阵，DA-INV-05），web-api 不越权直写。
工具返回 JSON 字符串，统一解析为 dict；工具内错误码（E-*）原样透传给调用方裁决。
"""

from __future__ import annotations

import json
import logging
from typing import cast

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger("tradeguard.mcp")


class McpToolClient:
    """最小 MCP streamable-http 调用器：一次调用一会话（无状态，稳定优先）

    阶段2 R-41 曾尝试连接复用（单 ClientSession + 锁），但 streamable-http 的
    read_stream 需后台任务持续消费，否则响应堆积导致 call_tool 全部超时（实测
    连续 3 次均 TimeoutError）；故回滚为每次建连，保证正确性优先于连接开销。
    生产形态若需连接复用，须引入 read_stream 后台消费循环（另列 R 后续项）。
    """

    def __init__(self, url: str):
        self.url = url

    async def call_tool(self, name: str, **arguments) -> dict:
        async with streamablehttp_client(self.url) as (read, write, _):  # noqa: SIM117 —— ClientSession 依赖 read/write，依赖嵌套不能合并
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
                if result.isError:
                    raise RuntimeError(f"MCP 工具 {name} 执行失败：{result.content}")
                return json.loads(result.content[0].text)


class ExternalSourcesClient(McpToolClient):
    """AA-MCP-02：四外部源查询 + stat 统计建议线（query_reason 必填，BA-BR-10/25）"""

    async def query_credit_report(self, subject_id: str, query_reason: str) -> dict:
        return await self.call_tool(
            "query_credit", subject_id=subject_id, query_reason=query_reason
        )

    async def query_sentiment(self, subject_id: str, query_reason: str) -> dict:
        return await self.call_tool(
            "query_sentiment", subject_id=subject_id, query_reason=query_reason
        )

    async def query_complaints(self, subject_id: str, query_reason: str) -> dict:
        return await self.call_tool(
            "query_complaint", subject_id=subject_id, query_reason=query_reason
        )

    async def query_enterprise(self, subject_id: str, query_reason: str) -> dict:
        """API-M-16：企业资质五维（双轨：厂商 Key 在则真实，失败/无 Key 降级 mock；
        仅线索不裁决 BA-BR-24，US-E15）"""
        return await self.call_tool(
            "query_enterprise", subject_id=subject_id, query_reason=query_reason
        )

    async def query_stat_outliers(self, values: list[float], query_reason: str,
                                   algo: str = "iforest") -> dict:
        """API-M-17~19：金额序列统计离群检测（pyod 三算法 iforest/lof/ecod，
        US-E16）。仅 advisory 参谋分不裁决（BA-BR-25）；依赖缺失返
        E-TOOL-UNAVAILABLE、样本 <5 返 E-BAD-INPUT，调用方按降级留痕不阻断"""
        return await self.call_tool(
            f"pyod_{algo}", values=values, query_reason=query_reason
        )


class CoreClient(McpToolClient):
    """AA-MCP-01：聚合落库（API-M-10）/处置执行（API-M-03）/建单（API-M-11）/
    证据固化（API-M-12）/关联加分（API-M-13）/核验回查（API-M-04/06）/入库申请（API-M-05）"""

    async def record_case_signals(
        self, case_id: str, risk_score: int, signals: list[dict]
    ) -> dict:
        """AA-SK-01 步骤 6：信号 insert DA-T-04（只增）+ risk_score 回写 DA-T-03"""
        payload = [
            {
                k: v
                for k, v in s.items()
                if k
                in (
                    "signal_id",
                    "source",
                    "type",
                    "confidence",
                    "raw_ref",
                    "query_reason",
                    "degraded",
                    "velocity_json",
                )
            }
            for s in signals
        ]
        for s in payload:  # datetime/复杂对象不入契约，落库 ts 取库端默认 now()
            s.pop("ts", None)
        # 直传数组：API-M-10 签名为 list[dict]（FastMCP 对 JSON 字符串参数会预解析，
        # 传字符串反被解成 list 造成服务端校验失败）
        return await self.call_tool(
            "record_case_signals",
            case_id=case_id,
            risk_score=risk_score,
            signals=payload,
        )

    async def execute_disposition(
        self,
        case_id: str,
        action: str,
        amount: float | None,
        idempotency_key: str,
        approval_ref: str | None = None,
    ) -> dict:
        """API-M-03：处置执行（审批门控 DA-INV-02 + 幂等 DA-INV-03）"""
        return await self.call_tool(
            "execute_disposition",
            case_id=case_id,
            action=action,
            amount=amount,
            idempotency_key=idempotency_key,
            approval_ref=approval_ref,
        )

    async def create_approval_request(
        self, case_id: str, action: str, amount: float | None, reason: str
    ) -> dict:
        """API-M-11：处置审批工单创建（E-DISP-AUTH 门控建单，SC-02）"""
        return await self.call_tool(
            "create_approval_request",
            case_id=case_id,
            action=action,
            amount=amount,
            reason=reason,
        )

    async def record_case_evidence(self, case_id: str, claims: list[dict]) -> dict:
        """API-M-12：证据链固化 DA-T-05（只增，BA-BR-03，US-E4-03）"""
        return await self.call_tool(
            "record_case_evidence", case_id=case_id, claims=claims
        )

    async def apply_risk_bonus(self, case_id: str, points: int, basis: str) -> dict:
        """API-M-13：BA-BR-06 关联网络命中加分（同案同依据幂等）"""
        return await self.call_tool(
            "apply_risk_bonus", case_id=case_id, points=points, basis=basis
        )

    async def query_disposition_result(self, exec_id: str) -> dict:
        """API-M-04：处置结果回查（AA-SK-04 核验依据）"""
        return await self.call_tool("query_disposition_result", exec_id=exec_id)

    async def query_related_graph(self, account_hash: str, hops: int = 2) -> dict:
        """API-M-02：关联图谱 + topology_stats 拓扑线索（B1，仅研判不裁决，
        DA-INV-07，US-E9）；返回 {"edges": [...], "topology_stats": {...}}"""
        return await self.call_tool(
            "query_related_graph", account_hash=account_hash, hops=hops
        )

    async def query_audit_trail(self, case_id: str) -> list:
        """API-M-06：审计链回放（只读，SC-08）"""
        # mcp-core 返回 json.dumps([...]) 数组 → json.loads 得 list（call_tool 注解为 dict，此处 cast）
        return cast(list, await self.call_tool("query_audit_trail", case_id=case_id))

    async def submit_kb_application(
        self, case_id: str, category: str, title: str, content: str
    ) -> dict:
        """API-M-05：知识入库申请（AA-SK-05，仅 pending，发布须人工，DA-INV-06）"""
        return await self.call_tool(
            "submit_kb_application",
            case_id=case_id,
            category=category,
            title=title,
            content=content,
        )

    async def record_agent_memory(
        self, case_id: str, agent_id: str, stage: str, summary: dict
    ) -> dict:
        """API-M-14：Agent 执行摘要落 DA-T-12（agent_memory 仅 tg_app 可 INSERT）"""
        return await self.call_tool(
            "record_agent_memory",
            case_id=case_id,
            agent_id=agent_id,
            stage=stage,
            summary=summary,
        )

    async def query_transactions(self, account_hash: str, hours: int = 24,
                                 limit: int = 100) -> list[dict]:
        """API-M-01：主体流水回查（只读）。mcp-core 返回 json.dumps(list[dict])
        字符串（amount 为 str）→ call_tool 已解析；此处容错 str/dict 两形态"""
        rows = await self.call_tool(
            "query_transactions", account_hash=account_hash, hours=hours, limit=limit
        )
        if isinstance(rows, str):
            rows = json.loads(rows)
        return cast(list, rows)


async def remember(
    core: CoreClient, case_id: str, agent_id: str, stage: str, summary: dict
) -> None:
    """DA-T-12 agent_memory 摘要写入：失败仅告警不阻断技能主流程"""
    try:
        await core.record_agent_memory(case_id, agent_id, stage, summary)
    except Exception:  # noqa: BLE001 —— 记忆写入非关键路径
        logger.exception("agent_memory 写入失败：case=%s stage=%s", case_id, stage)
