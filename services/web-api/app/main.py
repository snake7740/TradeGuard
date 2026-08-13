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
import asyncio
import logging
import os
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import alerts, approvals, audit, cases, config, events_stream, health, kb
from .core.config_service import ConfigService
from .core.events import InMemoryPublisher
from .repositories import ApprovalRepository, CaseRepository, KbRepository
from .skills.aggregation import AggregationService
from .skills.disposition import DispositionService, scan_pending_escalations
from .skills.mcp_adapters import CoreClient, ExternalSourcesClient

logger = logging.getLogger("tradeguard.web")
ESCALATION_SCAN_INTERVAL = float(os.getenv("ESCALATION_SCAN_INTERVAL", "30"))  # BA-BR-13 轮询周期（秒）

PG_DSN = os.getenv("PG_DSN", "postgresql://tg_web:tg_web_dev@localhost:5432/tradeguard")
# MCP 客户端（httpx）自动读取代理配置：回环/服务名地址需旁路，否则被代理拦截返回 502
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,mcp-core,mcp-external-mock")
os.environ.setdefault("no_proxy", "localhost,127.0.0.1,mcp-core,mcp-external-mock")
MCP_CORE_URL = os.getenv("MCP_CORE_URL", "http://127.0.0.1:8101/mcp/")
MCP_EXTERNAL_URL = os.getenv("MCP_EXTERNAL_URL", "http://127.0.0.1:8102/mcp/")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=8)
    app.state.publisher = InMemoryPublisher()
    # TODO(US-E3-04)：Sprint 1 装配 RocketMQPublisher（namesrv=rocketmq-namesrv:9876）
    app.state.cases = CaseRepository(app.state.pool, app.state.publisher)
    app.state.approvals = ApprovalRepository(app.state.pool)
    app.state.kb = KbRepository(app.state.pool)
    # AA-SK-01 确定性聚合内核（US-E3-03/04）：外部源经 AA-MCP-02，落库经 AA-MCP-01（tg_app 写角色）
    app.state.aggregation = AggregationService(
        pool=app.state.pool, cases=app.state.cases,
        external=ExternalSourcesClient(MCP_EXTERNAL_URL), core=CoreClient(MCP_CORE_URL))
    # AA-SK-03 处置执行确定性内核（US-E5-01~04）：建单经 AA-MCP-01（tg_app），决策回填走 tg_web
    app.state.disposition = DispositionService(
        pool=app.state.pool, cases=app.state.cases,
        core=CoreClient(MCP_CORE_URL), pub=app.state.publisher)
    app.state.config = ConfigService(pool=app.state.pool)   # US-E1-03 阈值热加载
    await app.state.config.start()

    async def _escalation_loop():
        # BA-BR-13 审批时效升级扫描（SC-09）：定时标记超时未决工单，异常吞掉防任务退出
        while True:
            try:
                await scan_pending_escalations(app.state.pool, app.state.publisher)
            except Exception:  # noqa: BLE001 —— 后台巡检不中断服务
                logger.exception("BA-BR-13 升级扫描异常，等待下轮")
            await asyncio.sleep(ESCALATION_SCAN_INTERVAL)

    escalation_task = asyncio.create_task(_escalation_loop())
    yield
    escalation_task.cancel()
    try:
        await escalation_task
    except asyncio.CancelledError:
        pass
    await app.state.config.stop()
    await app.state.pool.close()


def create_app() -> FastAPI:
    app = FastAPI(title="TradeGuard web-api", version="0.2.0", lifespan=lifespan,
                  description="API-W-01~15 契约实现（docs/openapi/tradeguard-openapi.yaml）")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    for module in (health, alerts, cases, approvals, audit, kb, events_stream, config):
        app.include_router(module.router)
    return app


app = create_app()
