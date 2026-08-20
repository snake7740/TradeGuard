# -*- coding: utf-8 -*-
"""docs/14 增强契约测试（§4 契约层：yaml 为唯一事实源，逐字守护）

覆盖：
  1. OpenAPI SseEvent 枚举 +4 新事件逐字同步（E-INV-HYPOTHESIS / E-REVIEW-DEBATE /
     E-KB-DECAY / E-OUTCOME-FOLLOW，docs/14 §1.3）；
  2. query_related_graph 工具返回 Schema 含 topology_stats（B1，test_mcp_gate 同源）；
  3. 控辩 DebateRecord 输出 Schema（C1，DA-INV-09 落库形状）；
  4. audit precheck 只读端点 Schema（D1，6 项检查清单）。
"""
import dataclasses
import os
import re
import uuid
from dataclasses import asdict

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.skills.planner import DebateRecord, rule_debate
from conftest import MCP_CORE_URL, PG_DSN

OPENAPI = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                       "docs", "openapi", "tradeguard-openapi.yaml")
NEW_EVENTS = ("E-INV-HYPOTHESIS", "E-REVIEW-DEBATE", "E-KB-DECAY", "E-OUTCOME-FOLLOW")


# ---------- 1. SseEvent 枚举逐字同步（契约锚点） ----------

def test_openapi_sse_event_enum_contains_new_events():
    text = open(OPENAPI, encoding="utf-8").read()
    m = re.search(r"SseEvent:.*?enum:\s*\[(.*?)\]", text, re.S)
    assert m, "OpenAPI SseEvent 枚举缺失（契约锚点）"
    enum = [s.strip() for s in m.group(1).split(",")]
    for ev in NEW_EVENTS:
        assert ev in enum, f"SseEvent 枚举缺 {ev}（docs/14 §1.3 逐字同步要求）"
    assert len(enum) == len(set(enum)), "SseEvent 枚举存在重复项"


# ---------- 2. topology_stats 工具 Schema（B1，mcp-core 实链路） ----------

async def test_query_related_graph_topology_schema():
    from app.skills.mcp_adapters import CoreClient
    core = CoreClient(MCP_CORE_URL)
    out = await core.query_related_graph("f" * 64, hops=1)   # 无关联主体：空边也须有完整结构
    assert set(out) >= {"edges", "topology_stats"}
    ts = out["topology_stats"]
    assert set(ts) == {"nodes", "edges", "star_density", "cycle_count",
                       "bipartite_concentration", "suspicion", "degraded"}
    assert 0.0 <= ts["suspicion"] <= 1.0                     # 线索分域约束（DA-INV-07）


# ---------- 3. DebateRecord 输出 Schema（C1） ----------

def test_debate_record_schema_keys():
    rec = rule_debate("freeze", 2000.0, 80, [{"claim": "x"}])
    assert set(asdict(rec)) == {"source", "prosecution", "defense",
                                "adjudication", "verdict", "summary"}
    assert rec.verdict in ("pass", "concerns", "escalate")
    assert all(isinstance(x, str) for x in rec.prosecution + rec.defense)
    assert "summary" in {f.name for f in dataclasses.fields(DebateRecord)}  # dataclass 形状守护


# ---------- 4. audit precheck Schema（D1，只读扩展） ----------

@pytest.fixture(scope="module")
def client():
    os.environ["PG_DSN"] = PG_DSN
    os.environ.pop("TG_API_TOKEN", None)
    from app.main import create_app
    with TestClient(create_app()) as c:
        yield c


def test_precheck_schema_not_found(client):
    r = client.get("/api/audit/CASE-NOT-EXIST/precheck")
    assert r.status_code == 200 and r.json()["code"] == "E-NOT-FOUND"


def test_precheck_schema_items(client):
    case_id = f"CASE-PC-{uuid.uuid4().hex[:6]}"

    async def _seed():
        conn = await asyncpg.connect(PG_DSN)
        try:
            await conn.execute(
                """INSERT INTO risk_case (case_id, subject_ref, status, risk_score,
                                          trace_id)
                   VALUES ($1, $2, 'INVESTIGATING', 60, $3)""",
                case_id, uuid.uuid4().hex, uuid.uuid4().hex)
        finally:
            await conn.close()
    import asyncio
    asyncio.run(_seed())

    r = client.get(f"/api/audit/{case_id}/precheck")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"case_id", "status", "items", "passed"}
    ids = [it["id"] for it in body["items"]]
    assert ids == ["signals_present", "evidence_chain", "hypothesis_fixed",
                   "cross_review_done", "debate_recorded", "disposition_anchored"]
    assert all(set(it) >= {"id", "name", "status", "basis"} for it in body["items"])
    assert all(it["status"] in ("ok", "warn", "fail") for it in body["items"])
    assert isinstance(body["passed"], bool)
