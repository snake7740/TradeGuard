"""G4-3 第三方框架互操作实测探针（新智基座维度2闭合项，docs/13 §6 G4-3）。

视角：本脚本模拟「外部组织的 Agent 平台」——独立 venv（.venv-interop，
langgraph + langchain-mcp-adapters），不改 TradeGuard 任何代码，仅以公开
MCP 端点消费，验证三件事：

  1. 发现：MultiServerMCPClient 经 streamable-http 连接 AA-MCP-01（8101）
     与 AA-MCP-02（8102），装载工具清单；
  2. 消费：把 MCP 工具挂进 LangGraph StateGraph 单节点执行一次只读
     调查工具 query_related_graph（API-M-02，空图也应返回合法结构）；
  3. 治理边界：对 AA-MCP-02 外部源不带 query_reason 调用，验证
     E-REASON-REQUIRED 事由门禁对第三方框架同样生效（BA-BR-10）。

用法（隔离环境，勿用项目 .venv）：
  .venv-interop\\Scripts\\python.exe scripts\\interop_probe.py > interop_probe.log
退出码：0 = 三项全过；1 = 任一失败。
"""
from __future__ import annotations

import asyncio
import json
import sys

from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

# 端点不带尾斜杠：服务端真实端点为 /mcp，带斜杠会 307 重定向，
# Windows docker-proxy 对重定向后的 POST 转发返 502（实测取证，直连 /mcp 即通）
CORE_URL = "http://127.0.0.1:8101/mcp"
EXTERNAL_URL = "http://127.0.0.1:8102/mcp"
SUBJECT = "interop-probe-subject-0001"


class ProbeState(TypedDict, total=False):
    graph_result: str
    reason_gate_result: str


async def main() -> int:
    failures: list[str] = []
    client = MultiServerMCPClient({
        "tradeguard-core": {"url": CORE_URL, "transport": "streamable_http"},
        "tradeguard-external": {"url": EXTERNAL_URL, "transport": "streamable_http"},
    })

    # 1. 发现：工具装载
    tools = await client.get_tools()
    names = sorted(t.name for t in tools)
    print(f"[1] 工具装载：{len(names)} 个 → {names}")
    if "query_related_graph" not in names:
        failures.append("发现：AA-MCP-01 query_related_graph 未装载")

    by_name = {t.name: t for t in tools}

    # 2. 消费：LangGraph 单节点跑只读调查工具
    async def investigate(state: ProbeState) -> ProbeState:
        tool = by_name["query_related_graph"]
        state["graph_result"] = await tool.ainvoke(
            {"account_hash": SUBJECT, "hops": 2})
        return state

    builder = StateGraph(ProbeState)
    builder.add_node("investigate", investigate)
    builder.add_edge(START, "investigate")
    builder.add_edge("investigate", END)
    graph = builder.compile()
    final = await graph.ainvoke({})
    try:
        payload = json.loads(final["graph_result"])
        ok = isinstance(payload.get("edges"), list) and "topology_stats" in payload
        print(f"[2] LangGraph 消费 query_related_graph：edges={len(payload.get('edges', []))}"
              f"，topology_stats={'有' if ok else '缺'}")
        if not ok:
            failures.append(f"消费：返回结构非契约形态 {final['graph_result'][:120]}")
    except Exception as exc:
        failures.append(f"消费：返回非 JSON / 调用失败 {exc}")

    # 3. 治理边界：无 query_reason 的外部源调用应被事由门禁拦截
    async def gate_probe(state: ProbeState) -> ProbeState:
        ext = by_name.get("query_credit_report")
        if ext is None:
            state["reason_gate_result"] = "SKIP: query_credit_report 未装载"
            return state
        state["reason_gate_result"] = await ext.ainvoke(
            {"account_hash": SUBJECT, "query_reason": ""})
        return state

    b2 = StateGraph(ProbeState)
    b2.add_node("gate", gate_probe)
    b2.add_edge(START, "gate")
    b2.add_edge("gate", END)
    gate_out = (await b2.compile().ainvoke({}))["reason_gate_result"]
    print(f"[3] 事由门禁对第三方：{gate_out[:160]}")
    if gate_out.startswith("SKIP"):
        failures.append("治理：外部源工具未装载，无法验证事由门禁")
    elif "E-REASON-REQUIRED" not in gate_out:
        failures.append(f"治理：空事由未被拦截（期望 E-REASON-REQUIRED）：{gate_out[:120]}")

    print("结论：" + ("三项全过 ✓" if not failures else "失败 → " + "；".join(failures)))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
