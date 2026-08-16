# -*- coding: utf-8 -*-
"""路由层契约测试（yaml 为唯一事实源：API-W-01/02/05/07/09/14/15/16/20）

覆盖既有测试盲区：alerts 202 + severity 映射、X-Operator URL 编码中文解码落审计
（前端 axios 拦截器 encodeURIComponent 契约）、cases 分页 {total,items}、graph 新结构、
review release/block/escalate、decide approve/reject、config GET+PUT、traces 过滤、
SSE 首帧。经 TestClient 走真实 lifespan（PG 5433 + 真 MCP 8101/8102）。
"""
import asyncio
import hashlib
import json
import os
import urllib.parse
import uuid
from datetime import datetime, timezone

import asyncpg
import pytest
from fastapi.testclient import TestClient

from conftest import MCP_CORE_URL, PG_DSN

OP_ONCALL = urllib.parse.quote("风控值班员")     # 前端 axios 注入的编码中文角色名
OP_REVIEWER = urllib.parse.quote("风控复核员")
OP_APPROVER = urllib.parse.quote("审批主管")
OP_CONFIG = urllib.parse.quote("配置管理员")


@pytest.fixture(scope="module")
def client():
    os.environ["PG_DSN"] = PG_DSN                # lifespan 连测试库（tg_web@5433）
    os.environ.pop("TG_API_TOKEN", None)         # 开发直通（鉴权矩阵由 test_auth 承载）
    from app.main import create_app
    with TestClient(create_app()) as c:
        yield c


def _subject(tag: str) -> str:
    return hashlib.sha256(f"{tag}-{uuid.uuid4().hex}".encode()).hexdigest()[:64]


def _fetch(query: str, *args) -> list:
    async def go():
        conn = await asyncpg.connect(PG_DSN)
        try:
            return await conn.fetch(query, *args)
        finally:
            await conn.close()
    return asyncio.run(go())


def _execute(query: str, *args) -> None:
    async def go():
        conn = await asyncpg.connect(PG_DSN)
        try:
            await conn.execute(query, *args)
        finally:
            await conn.close()
    asyncio.run(go())


def _set_status(case_id: str, status: str) -> None:
    """测试装配直改 status（旁路应用层）：07 actor 门控要求事务内先声明 tg.actor，
    否则撞 E-ACTOR-REQUIRED。白名单触发器仍校验迁移合法性（双道防线，工作流 E）。

    ⚠ 竞态警示：勿用于推进 REGISTERED 案件——compose EventWorker 2s 轮询同库，
    停留 REGISTERED 的窗口会被抢跑（见 _reviewable_case 直插法说明）。"""
    async def go():
        conn = await asyncpg.connect(PG_DSN)
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('tg.actor', 'human:test-setup', true)")
                await conn.execute(
                    "UPDATE risk_case SET status=$2 WHERE case_id=$1", case_id, status)
        finally:
            await conn.close()
    asyncio.run(go())


def _alert(client, subject: str, severity: str | None = None,
           operator: str | None = None) -> dict:
    body = {"subject_ref": subject}
    if severity:
        body["severity"] = severity
    headers = {"X-Operator": operator} if operator else {}
    r = client.post("/api/alerts", json=body, headers=headers)
    assert r.status_code == 202, r.text          # 契约：受理即 202
    return r.json()


def _reviewable_case(client, severity: str = "high") -> str:
    """测试装配：单事务直插 INVESTIGATING（原子落位，对 EventWorker 零窗口）。

    为什么不再「API 立案 + 两步 UPDATE 推进」：compose 的 web-api 运行着
    EventWorker（TG_EVENT_WORKER=on，2s 轮询 status='REGISTERED'），与测试共用
    同一库。立案提交到测试推进完成之间，案件停留在 REGISTERED 的窗口（POST 审计
    中间件 + 两次独立 asyncpg 连接，Windows 上累计可达秒级）会被 worker 抢跑——
    自动链路一路驱至 DISPOSED（score<40 → 自动 release），随后测试的 _set_status
    撞 E-BAD-TRANSITION 假失败（对照实验：docker compose stop web-api 后本文件
    全绿；restart 即复现，7/7）。

    直插法天然免疫该竞态：两道 DB 守护触发器（04-invariants 白名单 /
    07-case-actor-gate）均为 BEFORE UPDATE OF status，不拦 INSERT；EventWorker
    只轮询 REGISTERED，生于 INVESTIGATING 的案件对其完全不可见。立案受理契约
    （202 / severity 映射 / 操作者审计）由 alerts 组测试独立覆盖，不受影响。
    """
    score = {"low": 25, "medium": 55, "high": 85}[severity]   # 与 SEVERITY_SCORES 同源
    case_id = f"CASE-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6]}"
    _execute(
        """INSERT INTO risk_case (case_id, subject_ref, status, risk_score, trace_id)
           VALUES ($1, $2, 'INVESTIGATING', $3, $4)""",
        case_id, _subject("routes-mr"), score, uuid.uuid4().hex)
    return case_id


# ---------- API-W-15 健康探针 ----------

def test_health_components(client):
    r = client.get("/api/health")
    assert r.status_code == 200                  # 任一组件 DOWN 也是 200（探针语义）
    body = r.json()
    assert body["status"] in ("UP", "DEGRADED")
    assert set(body["components"]) == {"postgres", "rocketmq", "mcp-core", "mcp-external"}
    assert body["components"]["postgres"] == "UP"


# ---------- API-W-01 告警受理（202 + severity 枚举映射） ----------

def test_alert_severity_seed_mapping(client):
    for severity, score in (("low", 25), ("medium", 55), ("high", 85)):
        reg = _alert(client, _subject("routes-sev"), severity)
        assert reg["status"] == "REGISTERED" and reg["case_id"]
        detail = client.get(f"/api/cases/{reg['case_id']}").json()
        assert detail["risk_score"] == score, f"severity={severity} 应映射 {score}"
    # 缺省 severity=medium；缺省操作者 human:operator 落审计
    reg = _alert(client, _subject("routes-sev-default"))
    assert client.get(f"/api/cases/{reg['case_id']}").json()["risk_score"] == 55
    rows = _fetch("SELECT actor FROM audit_log WHERE target=$1 AND action='case.register'",
                  reg["case_id"])
    assert rows and rows[0]["actor"] == "human:operator"


def test_alert_validation_422(client):
    bad = client.post("/api/alerts", json={"subject_ref": _subject("x"), "severity": "critical"})
    assert bad.status_code == 422               # 枚举外 severity 拒收
    missing = client.post("/api/alerts", json={"severity": "low"})
    assert missing.status_code == 422           # subject_ref 必填


def test_alert_operator_header_url_decoded(client):
    """X-Operator 为 encodeURIComponent 编码中文（前端契约）→ 审计落解码后角色名"""
    reg = _alert(client, _subject("routes-op"), "low", operator=OP_ONCALL)
    rows = _fetch("SELECT actor FROM audit_log WHERE target=$1 AND action='case.register'",
                  reg["case_id"])
    assert rows and rows[0]["actor"] == "human:风控值班员"   # unquote + human: 前缀归一
    guard = _fetch("""SELECT 1 FROM audit_log
                      WHERE action='api.request' AND actor='human:风控值班员'
                        AND basis LIKE '%/api/alerts%'""")
    assert guard, "网关审计中间件也须落解码后的操作者"


# ---------- API-W-02 事件列表（分页契约 {total, items}） ----------

def test_cases_pagination_contract(client):
    for _ in range(3):
        _alert(client, _subject("routes-page"), "high")
    r = client.get("/api/cases", params={"size": 2, "page": 1})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"total", "items"}
    assert body["total"] >= 3 and len(body["items"]) <= 2
    # 过滤条件生效
    filtered = client.get("/api/cases", params={"risk_min": 80, "status": "REGISTERED"}).json()
    assert all(c["risk_score"] >= 80 and c["status"] == "REGISTERED"
               for c in filtered["items"])
    # 参数越界 422（size 上限 100 / page ≥1 / risk_min 0-100）
    assert client.get("/api/cases", params={"size": 101}).status_code == 422
    assert client.get("/api/cases", params={"page": 0}).status_code == 422
    assert client.get("/api/cases", params={"risk_min": 101}).status_code == 422


# ---------- API-W-05 图谱（GraphResponse 新结构） ----------

def test_graph_response_contract(client):
    subject = _subject("routes-graph")
    reg = _alert(client, subject, "medium")
    r = client.get(f"/api/cases/{reg['case_id']}/graph", params={"hops": 9})
    assert r.status_code == 200
    body = r.json()
    assert body["start"] == subject and body["hops"] == 2       # hops 钳位 2 跳上限
    assert body["links"] == []                                  # 新主体无关联边
    assert body["nodes"] == [{"id": subject, "type": "Account", "risk_flag": "none"}]
    assert client.get("/api/cases/CASE-NOPE/graph").status_code == 404


# ---------- API-W-07 人工复核（release / block / escalate） ----------

def test_review_release_archives_with_decoded_actor(client):
    case_id = _reviewable_case(client, "medium")
    r = client.post(f"/api/cases/{case_id}/review",
                    json={"conclusion": "release", "opinion": "确认误报，排除欺诈"},
                    headers={"X-Operator": OP_REVIEWER})
    assert r.status_code == 200 and r.json()["status"] == "ARCHIVED"
    rows = _fetch("""SELECT actor FROM audit_log
                     WHERE target=$1 AND action='case.transition.ReviewDismissed'""", case_id)
    assert rows and rows[0]["actor"] == "human:风控复核员"


def test_review_validation_and_human_guard(client):
    case_id = _reviewable_case(client)
    short = client.post(f"/api/cases/{case_id}/review",
                        json={"conclusion": "release", "opinion": "短"})
    assert short.status_code == 422                             # opinion minLength 5
    bad = client.post(f"/api/cases/{case_id}/review",
                      json={"conclusion": "dismiss", "opinion": "结论枚举外拒收"})
    assert bad.status_code == 422
    agent = client.post(f"/api/cases/{case_id}/review",
                        json={"conclusion": "release", "opinion": "代理越权触发复核"},
                        headers={"X-Operator": "agent:AA-AG-99"})
    assert agent.status_code == 409                             # human_only 守卫（02 §7）
    assert agent.json()["detail"]["code"] == "E-HUMAN-ONLY"


def test_review_block_creates_approval(client):
    case_id = _reviewable_case(client)
    r = client.post(f"/api/cases/{case_id}/review",
                    json={"conclusion": "block", "opinion": "确认欺诈，申请冻结"},
                    headers={"X-Operator": OP_REVIEWER})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "PENDING_APPROVAL" and body["approval_id"]
    rec = _fetch("SELECT decision FROM approval_record WHERE approval_id=$1",
                 body["approval_id"])
    assert rec and rec[0]["decision"] == "pending"              # US-E5-04 自动建单


def test_review_escalate_marks_context(client):
    case_id = _reviewable_case(client)
    r = client.post(f"/api/cases/{case_id}/review",
                    json={"conclusion": "escalate", "opinion": "风险升级，提请上级审批"},
                    headers={"X-Operator": OP_REVIEWER})
    assert r.status_code == 200 and r.json()["approval_id"]
    detail = client.get(f"/api/cases/{case_id}").json()
    assert detail["context_json"].get("escalated") is True      # 升级标记入共享状态
    rows = _fetch("""SELECT basis FROM audit_log
                     WHERE target=$1 AND action='case.transition.ReviewConfirmed'""", case_id)
    assert rows and "escalated=true" in rows[0]["basis"]        # 审计留痕升级建单


# ---------- API-W-09 审批决策（approve / reject） ----------

def _blocked_approval(client) -> tuple[str, str]:
    case_id = _reviewable_case(client)
    r = client.post(f"/api/cases/{case_id}/review",
                    json={"conclusion": "block", "opinion": "确认欺诈，申请冻结"},
                    headers={"X-Operator": OP_REVIEWER})
    return case_id, r.json()["approval_id"]


def test_decide_reject_rolls_back_to_review(client):
    case_id, approval_id = _blocked_approval(client)
    r = client.post(f"/api/approvals/{approval_id}/decide",
                    json={"decision": "reject", "opinion": "证据不足，驳回重审"},
                    headers={"X-Operator": OP_APPROVER})
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "rejected" and body["case_status"] == "MANUAL_REVIEW"
    rec = _fetch("SELECT approver, opinion FROM approval_record WHERE approval_id=$1",
                 approval_id)
    assert rec[0]["approver"] == "human:审批主管"               # X-Operator 解码回填
    assert rec[0]["opinion"] == "证据不足，驳回重审"
    again = client.post(f"/api/approvals/{approval_id}/decide",
                        json={"decision": "reject", "opinion": "重复决策必须拒绝"},
                        headers={"X-Operator": OP_APPROVER})
    assert again.status_code == 409                             # 已决工单禁止重复回填
    assert again.json()["detail"]["code"] == "E-ALREADY-DECIDED"


def test_decide_validation_422(client):
    _, approval_id = _blocked_approval(client)
    bad = client.post(f"/api/approvals/{approval_id}/decide",
                      json={"decision": "confirm", "opinion": "枚举外决策拒收"})
    assert bad.status_code == 422
    short = client.post(f"/api/approvals/{approval_id}/decide",
                        json={"decision": "approve", "opinion": "短"})
    assert short.status_code == 422                             # opinion minLength 5


def test_decide_approve_executes_disposition(client):
    case_id, approval_id = _blocked_approval(client)

    async def _evidence():                                      # 冻结前置证据链（DA-INV-04）
        from app.skills.mcp_adapters import CoreClient
        return await CoreClient(MCP_CORE_URL).record_case_evidence(
            case_id, [{"claim": "持卡人否认交易", "source_ref": "AA-AG-03:test",
                       "confidence": 0.9}])
    asyncio.run(_evidence())

    r = client.post(f"/api/approvals/{approval_id}/decide",
                    json={"decision": "approve", "opinion": "证据充分，同意冻结"},
                    headers={"X-Operator": OP_APPROVER})
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "approved" and body["route"] == "executed"
    assert client.get(f"/api/cases/{case_id}").json()["status"] == "DISPOSED"


# ---------- API-W-16 阈值配置（GET + PUT） ----------

def test_config_get_put_roundtrip(client):
    snap = client.get("/api/config/thresholds").json()
    assert {"source", "values", "updated_at"} <= set(snap)
    key = "br-14-velocity-1h-count"
    cur = snap["values"].get(key, "10")
    r = client.put("/api/config/thresholds", json={key: cur},
                   headers={"X-Operator": OP_CONFIG})
    assert r.status_code == 200
    assert r.json()["values"][key] == cur                       # 写库→reload→快照同步
    rows = _fetch("""SELECT actor, basis FROM audit_log
                     WHERE action='config.thresholds.put' ORDER BY ts DESC LIMIT 1""")
    assert rows[0]["actor"] == "human:配置管理员" and key in rows[0]["basis"]


def test_config_put_rejections(client):
    empty = client.put("/api/config/thresholds", json={})
    assert empty.status_code == 422                             # 空体拒收
    unknown = client.put("/api/config/thresholds", json={"br-99-nonexistent": "1"})
    assert unknown.status_code == 400                           # 新键无 INSERT 授权
    assert unknown.json()["detail"]["code"] == "E-CONFIG-KEY"


# ---------- API-W-20 Trace 回放 + API-W-17 聚合路由 ----------

def test_traces_filter_by_case(client):
    reg = _alert(client, _subject("routes-trace"), "medium")
    agg = client.post(f"/api/cases/{reg['case_id']}/aggregate")
    assert agg.status_code == 200                               # 真实 MCP 全链路聚合
    r = client.get("/api/observability/traces", params={"case_id": reg["case_id"]})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == len(body["spans"]) >= 1
    assert all(s["case_id"] == reg["case_id"] for s in body["spans"])
    assert any(s["skill_id"] == "AA-SK-01" for s in body["spans"])  # 聚合 span 留痕
    capped = client.get("/api/observability/traces", params={"limit": 1}).json()
    assert capped["count"] == len(capped["spans"]) <= 1


# ---------- API-W-14 SSE 事件流（首帧 + 事件帧） ----------

async def test_sse_first_frame_and_event():
    """手动 ASGI 驱动（httpx.ASGITransport 缓冲整个响应，无法消费流式 SSE）"""
    import contextlib

    from fastapi import FastAPI

    from app.api.events_stream import router
    from app.core.events import InMemoryPublisher

    app = FastAPI()
    app.include_router(router)
    app.state.publisher = InMemoryPublisher()

    scope = {"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
             "path": "/api/events/stream", "raw_path": b"/api/events/stream",
             "query_string": b"", "root_path": "", "headers": [],
             "client": ("test", 123), "server": ("test", 80)}

    chunks: list[bytes] = []
    headers_seen: dict = {}
    got_connected = asyncio.Event()
    got_data = asyncio.Event()

    async def receive():
        await asyncio.Event().wait()                 # 永不断连（收尾靠 cancel）

    async def send(message):
        if message["type"] == "http.response.start":
            headers_seen["status"] = message["status"]
            headers_seen["headers"] = dict(message.get("headers", []))
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))
            text = b"".join(chunks).decode("utf-8")
            if ": connected" in text:
                got_connected.set()
            if "data:" in text:
                got_data.set()

    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(got_connected.wait(), timeout=5)   # 首帧注释即连接确认
        assert headers_seen["status"] == 200
        assert headers_seen["headers"][b"content-type"].startswith(b"text/event-stream")
        await app.state.publisher.publish(
            "CASE-SSE", "CaseRegistered", {"risk_score": 55}, "human:operator")
        await asyncio.wait_for(got_data.wait(), timeout=5)
        text = b"".join(chunks).decode("utf-8")
        data_line = next(l for l in text.splitlines() if l.startswith("data:"))
        msg = json.loads(data_line[5:])
        assert msg["event"] == "CaseRegistered"
        assert msg["case_id"] == "CASE-SSE"
    finally:
        task.cancel()                                # 生成器 finally 触发 unsubscribe
        with contextlib.suppress(asyncio.CancelledError):
            await task
