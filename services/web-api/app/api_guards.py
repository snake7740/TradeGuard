# -*- coding: utf-8 -*-
"""网关守卫：bearer 鉴权 + API 审计（US-E7-01，04 §10.1）

鉴权策略：TG_API_TOKEN 配置时强制 Bearer 校验（每请求读取 env，支持热配置）；
/api/health 探活与 SSE 事件流豁免；未配置即开发直通（本地演示零配置）。
审计延伸（BA-BR-09）：写操作（POST/PUT/DELETE）落 audit_log action=api.request、
target=api，操作者取 X-Operator 头（人机边界，02 §3.3），异常吞掉不阻断请求。
"""
from __future__ import annotations

import logging
import os
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("tradeguard.guards")
WRITE_METHODS = ("POST", "PUT", "DELETE", "PATCH")
EXEMPT_PATHS = ("/api/health", "/api/events/stream")


async def _guard_middleware(request: Request, call_next):
    path = request.url.path
    # 1. bearer 鉴权（TG_API_TOKEN 每请求读取；豁免探活与 SSE）
    token = os.getenv("TG_API_TOKEN")
    if token and path.startswith("/api/") and path not in EXEMPT_PATHS:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {token}":
            return JSONResponse(
                status_code=401,
                content={"detail": {"code": "E-UNAUTHORIZED",
                                    "message": "缺少或非法 Bearer 凭证（US-E7-01）"}})
    response = await call_next(request)
    # 2. 写操作审计（读操作不落痕，降低审计噪音）
    if (request.method in WRITE_METHODS and path.startswith("/api/")
            and request.app.state.pool is not None):
        actor = request.headers.get("X-Operator", "api:anonymous")
        try:
            await request.app.state.pool.execute(
                """INSERT INTO audit_log (log_id, actor, action, target, basis)
                   VALUES ($1, $2, $3, 'api', $4)""",
                uuid.uuid4().hex, actor, "api.request",
                f"{request.method} {path} status={response.status_code}")
        except Exception:  # noqa: BLE001 —— 审计失败不阻断业务请求
            logger.exception("API 审计落库失败：%s %s", request.method, path)
    return response


def apply_api_guards(app, pool=None):
    """装配守卫：pool 缺省时经 request.app.state.pool 惰性取池（lifespan 装配后生效）"""
    if pool is not None:
        app.state.pool = pool
    else:
        app.state.setdefault_pool = None  # 占位：真实池由 lifespan 注入
        if not hasattr(app.state, "pool"):
            app.state.pool = None
    app.middleware("http")(_guard_middleware)
    return app
