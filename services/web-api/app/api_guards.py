# -*- coding: utf-8 -*-
"""网关守卫：bearer 鉴权 + API 审计（US-E7-01，04 §10.1）

鉴权策略：TG_API_TOKEN 配置时强制 Bearer 校验（每请求读取 env，支持热配置）；
仅 /api/health 探活豁免（R-37：SSE 事件流纳入鉴权，令牌经门户 nginx 反代注入，
前端无感知）；未配置 token 时开发直通（仅限测试进程，容器部署经 compose :? 强制注入）。
比对采用 secrets.compare_digest 常量时间算法（R-37，消除时序旁路）。
审计延伸（BA-BR-09）：写操作（POST/PUT/DELETE）落 audit_log action=api.request、
target=api，操作者取 X-Operator 头（人机边界，02 §3.3），异常吞掉不阻断请求；
鉴权失败的写请求补 api.denied 拒绝留痕（R-37）。
"""
from __future__ import annotations

import logging
import os
import secrets
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from .api.common import operator_from_header

logger = logging.getLogger("tradeguard.guards")
WRITE_METHODS = ("POST", "PUT", "DELETE", "PATCH")
EXEMPT_PATHS = ("/api/health",)   # R-37：SSE 纳入鉴权（门户 nginx 注入令牌），仅探活豁免


async def _audit(request: Request, action: str, basis: str, actor: str = "api:anonymous"):
    """守卫侧审计落库（best-effort）：池未装配/写失败均不阻断请求"""
    if request.app.state.pool is None:
        return
    try:
        await request.app.state.pool.execute(
            """INSERT INTO audit_log (log_id, actor, action, target, basis)
               VALUES ($1, $2, $3, 'api', $4)""",
            uuid.uuid4().hex, actor[:40], action, basis[:300])  # basis varchar(300) 防截断 500
    except Exception:  # noqa: BLE001 —— 审计失败不阻断业务请求
        logger.exception("API 审计落库失败：%s", action)


async def _guard_middleware(request: Request, call_next):
    # R-37 复审收口（CVE-2026-48710）：必须取 scope["path"]（FastAPI 路由同源，
    # uvicorn/httptools 解码的原始路径）而非 request.url.path——starlette<1.0.1
    # 的 URL 解析拼接 Host 头，恶意 Host（如 `x@evil.com/`）可把 url.path 污染成
    # `//api/...` 使 startswith('/api/') 前缀判断失效而整体跳过令牌校验，
    # 路由却仍按 scope 原始路径命中 /api/* → 等效未认证全量访问。
    path = request.scope.get("path", "")
    # 1. bearer 鉴权（TG_API_TOKEN 每请求读取；仅探活豁免）
    token = os.getenv("TG_API_TOKEN")
    if token and path.startswith("/api/") and path not in EXEMPT_PATHS:
        auth = request.headers.get("Authorization", "")
        expected = f"Bearer {token}"
        # R-37 复审收口：先 utf-8 编码再比较——compare_digest 对含非 ASCII 的 str
        # 抛 TypeError（等长非 ASCII 头会致 500 且跳过 api.denied 留痕）；bytes 比较
        # 长度不等恒返回 False，恒时语义不变
        if (len(auth) != len(expected)
                or not secrets.compare_digest(auth.encode("utf-8"),
                                              expected.encode("utf-8"))):
            if request.method in WRITE_METHODS:   # R-37：鉴权失败的写请求留痕可追责
                await _audit(request, "api.denied",
                             f"{request.method} {path} status=401（凭证缺失/非法）")
            return JSONResponse(
                status_code=401,
                content={"detail": {"code": "E-UNAUTHORIZED",
                                    "message": "缺少或非法 Bearer 凭证（US-E7-01）"}})
    response = await call_next(request)
    # 2. 写操作审计（读操作不落痕，降低审计噪音）
    if (request.method in WRITE_METHODS and path.startswith("/api/")
            and request.app.state.pool is not None):
        try:
            actor = operator_from_header(request.headers.get("X-Operator"), "api:anonymous")
        except HTTPException:
            actor = "api:anonymous"   # R-37：操作者头非法不豁免审计，以匿名身份继续留痕
        await _audit(request, "api.request",
                     f"{request.method} {path} status={response.status_code}", actor)
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
