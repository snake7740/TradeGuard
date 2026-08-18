"""路由层契约测试补充：补齐 test_routes.py 未直测的 10 条 REST HTTP 入口。

对齐 docs/openapi/tradeguard-openapi.yaml 的 22 路径，本文件覆盖剩余盲区：
  API-W-04  signals        案件信号清单
  API-W-06  evidence       证据链
  API-W-22  dispositions   处置执行记录
  API-W-18  investigate    触发欺诈调查
  API-W-19  verify         触发结果核验
  API-W-08  approvals      审批队列列表
  API-W-10  audit          审计链回放
  API-W-11  kb/applications 知识入库申请列表
  API-W-12  kb/publish     知识发布（人工门控 DA-INV-06）
  API-W-13  kb/reject      知识驳回

经 TestClient 走真实 lifespan（PG 5433 + 真 MCP 8101/8102）。每条路径聚焦
HTTP 层契约断言（状态码 + 响应形状 + 错误信封），细分支由服务层专题测试
（test_investigation / test_verification / test_knowledge）承载。
"""

import asyncio
import hashlib
import os
import uuid

import asyncpg  # pyright: ignore[reportMissingImports] —— 已装 .venv，静态分析器未解析 venv 站点包
import pytest
from fastapi.testclient import TestClient

from conftest import PG_DSN, TG_SUPER_DSN


@pytest.fixture(scope="module")
def client():
    os.environ["PG_DSN"] = PG_DSN  # lifespan 连测试库（tg_web@5433）
    os.environ.pop("TG_API_TOKEN", None)  # 开发直通（鉴权矩阵由 test_auth 承载）
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def _subject(tag: str) -> str:
    return hashlib.sha256(f"{tag}-{uuid.uuid4().hex}".encode()).hexdigest()[:64]


def _execute_super(query: str, *args) -> None:
    """经超级用户直写（kb_document 只增、无 DELETE 授权，测试装配需超户造 pending 单）"""

    async def go():
        conn = await asyncpg.connect(TG_SUPER_DSN)
        try:
            await conn.execute(query, *args)
        finally:
            await conn.close()

    asyncio.run(go())


def _alert(client, subject: str, severity: str | None = None) -> dict:
    body = {"subject_ref": subject}
    if severity:
        body["severity"] = severity
    r = client.post("/api/alerts", json=body)
    assert r.status_code == 202, r.text  # 契约：受理即 202
    return r.json()


def _insert_kb_pending(tag: str) -> str:
    """装配一条 pending 知识申请单（doc_id 32 位 hex，对齐 kb_document.doc_id）"""
    doc_id = uuid.uuid4().hex
    _execute_super(
        """INSERT INTO kb_document (doc_id, category, title, content, status, applicant)
           VALUES ($1, 'case', $2, $3, 'pending', 'AA-AG-05')""",
        doc_id,
        f"契约测试-{tag}-{doc_id[:6]}",
        f"复盘摘要内容 {doc_id}",
    )
    return doc_id


# ---------- API-W-04 信号清单 ----------


def test_signals_empty_for_fresh_case(client):
    reg = _alert(client, _subject("signals"))
    r = client.get(f"/api/cases/{reg['case_id']}/signals")
    assert r.status_code == 200
    assert r.json() == {"items": []}  # 新立案无信号（DA-T-04 未落）


# ---------- API-W-06 证据链 ----------


def test_evidence_empty_for_fresh_case(client):
    reg = _alert(client, _subject("evidence"))
    r = client.get(f"/api/cases/{reg['case_id']}/evidence")
    assert r.status_code == 200
    assert r.json() == {"items": []}  # DA-T-05 只增，新案件无证据


# ---------- API-W-22 处置执行记录 ----------


def test_dispositions_404_and_empty(client):
    r = client.get("/api/cases/CASE-NOPE/dispositions")
    assert r.status_code == 404  # 不存在的案件必须 404
    reg = _alert(client, _subject("dispositions"))
    r2 = client.get(f"/api/cases/{reg['case_id']}/dispositions")
    assert r2.status_code == 200
    assert r2.json() == {"items": []}


# ---------- API-W-18 触发欺诈调查 ----------


def test_investigate_404_and_state_guard(client):
    r = client.post("/api/cases/CASE-NOPE/investigate")
    assert r.status_code == 404  # 案件不存在
    reg = _alert(client, _subject("investigate"))
    r2 = client.post(f"/api/cases/{reg['case_id']}/investigate")
    assert r2.status_code == 409  # REGISTERED 不可调查（状态机守卫）
    assert r2.json()["detail"]["code"] == "E-BAD-STATE"


# ---------- API-W-19 触发结果核验 ----------


def test_verify_404_state_guard_422(client):
    r = client.post("/api/cases/CASE-NOPE/verify", json={"exec_id": "x"})
    assert r.status_code == 404  # 案件不存在
    reg = _alert(client, _subject("verify"))
    r2 = client.post(f"/api/cases/{reg['case_id']}/verify", json={"exec_id": "x"})
    assert r2.status_code == 409  # 非 DISPOSED 不可核验（AA-SK-04）
    r3 = client.post(f"/api/cases/{reg['case_id']}/verify", json={})
    assert r3.status_code == 422  # exec_id 必填（VerifyIn）


# ---------- API-W-08 审批队列列表 ----------


def test_approvals_list_contract(client):
    r = client.get("/api/approvals")
    assert r.status_code == 200
    assert "items" in r.json()  # 列表结构 {items: [...]}
    r2 = client.get("/api/approvals", params={"decision": "approved"})
    assert r2.status_code == 200 and "items" in r2.json()


# ---------- API-W-10 审计链回放 ----------


def test_audit_trail_contract(client):
    reg = _alert(client, _subject("audit"))
    r = client.get(f"/api/audit/{reg['case_id']}")
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(it["action"] == "case.register" for it in items)  # 立案审计必留痕


# ---------- API-W-11 知识入库申请列表 ----------


def test_kb_applications_list(client):
    r = client.get("/api/kb/applications")
    assert r.status_code == 200
    assert "items" in r.json()


# ---------- API-W-12 知识发布（人工门控 DA-INV-06） ----------


def test_kb_publish_404_and_happy_path(client):
    r = client.post(
        "/api/kb/applications/nonexistent-doc/publish",
        json={"operator": "human:kb_admin", "comment": "确认"},
    )
    assert r.status_code == 404  # doc_id 不存在
    doc_id = _insert_kb_pending("publish")
    r2 = client.post(
        f"/api/kb/applications/{doc_id}/publish",
        json={"operator": "human:kb_admin", "comment": "确认发布"},
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == "published"
    assert body["reviewer"] == "human:kb_admin"
    assert body["chunks"] >= 1  # 向量化 chunk 落库（US-E6-04）


def test_kb_publish_operator_from_header(client):
    """无 body.operator 时取 X-Operator 头（门户自动携带当前角色，URL 编码），
    审计/审核人留真人而非固定占位符；无头时回落 human:kb_admin（兼容直调）"""
    doc_id = _insert_kb_pending("hdr-op")
    r = client.post(
        f"/api/kb/applications/{doc_id}/publish",
        json={"comment": "确认发布"},
        headers={"X-Operator": "%E9%A3%8E%E6%8E%A7%E7%AD%96%E7%95%A5%E7%AE%A1%E7%90%86%E5%91%98"},
    )
    assert r.status_code == 200
    assert r.json()["reviewer"] == "human:风控策略管理员"
    doc_id2 = _insert_kb_pending("hdr-op-fallback")
    r2 = client.post(
        f"/api/kb/applications/{doc_id2}/reject",
        json={"comment": "证据不足"},
    )
    assert r2.status_code == 200
    assert r2.json()["reviewer"] == "human:kb_admin"  # 无头回落（直调/旧客户端）


# ---------- API-W-13 知识驳回 ----------


def test_kb_reject_404_happy_path_409(client):
    r = client.post(
        "/api/kb/applications/nonexistent-doc/reject",
        json={"operator": "human:kb_admin", "comment": "驳回"},
    )
    assert r.status_code == 404  # doc_id 不存在
    doc_id = _insert_kb_pending("reject")
    r2 = client.post(
        f"/api/kb/applications/{doc_id}/reject",
        json={"operator": "human:kb_admin", "comment": "证据不足"},
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "rejected"
    r3 = client.post(
        f"/api/kb/applications/{doc_id}/reject",
        json={"operator": "human:kb_admin", "comment": "重复驳回"},
    )
    assert r3.status_code == 409  # 已决不可重复操作（E-ALREADY-DECIDED）
