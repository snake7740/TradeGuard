# -*- coding: utf-8 -*-
"""US-E7-01 网关守卫测试：bearer 鉴权中间件 + API 审计中间件

鉴权策略（04 §10.1）：TG_API_TOKEN 配置时强制 Bearer 校验（/api/health 与
SSE 豁免）；未配置即开发直通（本地演示零配置）。审计中间件对写操作落
audit_log（action=api.request，target=api），BA-BR-09 全动作留痕延伸。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api_guards import apply_api_guards


class FakePool:
    """审计落库替身：记录 execute 调用（仅验证中间件行为，不触真库）"""

    def __init__(self):
        self.executed = []

    async def execute(self, q, *args):
        self.executed.append((q, args))
        return "INSERT 0 1"


def _build_app(pool):
    app = FastAPI()

    @app.get("/api/health")
    async def health():
        return {"status": "UP"}

    @app.get("/api/cases")
    async def cases():
        return {"items": []}

    @app.post("/api/cases/{case_id}/review")
    async def review(case_id: str):
        return {"case_id": case_id}

    return apply_api_guards(app, pool)


def test_auth_required_when_token_configured(monkeypatch):
    monkeypatch.setenv("TG_API_TOKEN", "dev-secret")
    client = TestClient(_build_app(FakePool()))

    assert client.get("/api/cases").status_code == 401            # 缺凭证拒绝
    bad = client.get("/api/cases", headers={"Authorization": "Bearer wrong"})
    assert bad.status_code == 401
    ok = client.get("/api/cases", headers={"Authorization": "Bearer dev-secret"})
    assert ok.status_code == 200
    assert client.get("/api/health").status_code == 200           # 探活豁免


def test_auth_bypass_when_token_unset(monkeypatch):
    monkeypatch.delenv("TG_API_TOKEN", raising=False)
    client = TestClient(_build_app(FakePool()))
    assert client.get("/api/cases").status_code == 200            # 开发直通


def test_audit_middleware_records_write_ops(monkeypatch):
    monkeypatch.delenv("TG_API_TOKEN", raising=False)
    pool = FakePool()
    client = TestClient(_build_app(pool))

    client.get("/api/cases")                                        # 读操作不审计
    assert pool.executed == []
    client.post("/api/cases/CASE-X/review",
                headers={"X-Operator": "human:reviewer"})
    assert len(pool.executed) == 1                                  # 写操作落审计
    q, args = pool.executed[0]
    assert "api.request" in args and "human:reviewer" in args
