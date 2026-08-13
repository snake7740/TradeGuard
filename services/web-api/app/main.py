"""TradeGuard web-api 应用工厂（web-portal 后端进程，04 §10.1 三端真实调用链）

分层与依赖装配（Sprint 0 架构模板）：
  api/*（路由层，API-W-01~15 全量声明）
    └─ repositories.py（仓储层，聚合边界 03 §9.1）
         ├─ core/state_machine.py（状态机，DA-INV-01，02 §7）
         └─ core/events.py（事件发布端口/适配器，03 §9.2）
生命周期用 lifespan（FastAPI 推荐替代 on_event）；依赖经 app.state 注入，
路由不直接持有连接池之外的基础设施，便于测试替身。
契约纪律：先改 docs/openapi/tradeguard-openapi.yaml 再改本目录。
"""
import os
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import alerts, approvals, audit, cases, events_stream, health, kb
from .core.events import InMemoryPublisher
from .repositories import ApprovalRepository, CaseRepository, KbRepository

PG_DSN = os.getenv("PG_DSN", "postgresql://tg_web:tg_web_dev@localhost:5432/tradeguard")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=8)
    app.state.publisher = InMemoryPublisher()
    # TODO(US-E3-04)：Sprint 1 装配 RocketMQPublisher（namesrv=rocketmq-namesrv:9876）
    app.state.cases = CaseRepository(app.state.pool, app.state.publisher)
    app.state.approvals = ApprovalRepository(app.state.pool)
    app.state.kb = KbRepository(app.state.pool)
    yield
    await app.state.pool.close()


def create_app() -> FastAPI:
    app = FastAPI(title="TradeGuard web-api", version="0.2.0", lifespan=lifespan,
                  description="API-W-01~15 契约实现（docs/openapi/tradeguard-openapi.yaml）")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    for module in (health, alerts, cases, approvals, audit, kb, events_stream):
        app.include_router(module.router)
    return app


app = create_app()
