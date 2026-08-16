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

import asyncpg  # pyright: ignore[reportMissingImports] —— 已装 .venv，静态分析器未解析 venv 站点包
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import (
    alerts,
    approvals,
    audit,
    cases,
    config,
    demo,
    events_stream,
    health,
    kb,
    observability,
)
from .api_guards import apply_api_guards
from .core.config_service import ConfigService
from .core.event_worker import EventWorker, SingleFlight, worker_enabled
from .core.events import InMemoryPublisher, RocketMQPublisher
from .repositories import ApprovalRepository, CaseRepository, KbRepository
from .skills.aggregation import AggregationService
from .skills.disposition import (
    ESCALATION_MINUTES,
    DispositionService,
    scan_pending_escalations,
)
from .skills.investigation import InvestigationService
from .skills.mcp_adapters import CoreClient, ExternalSourcesClient
from .skills.verification import (
    VERIFICATION_MINUTES,
    VerificationService,
    scan_verification_overdue,
)

logger = logging.getLogger("tradeguard.web")
ESCALATION_SCAN_INTERVAL = float(
    os.getenv("ESCALATION_SCAN_INTERVAL", "30")
)  # BA-BR-13 轮询周期（秒）

PG_DSN = os.getenv("PG_DSN", "postgresql://tg_web:tg_web_dev@localhost:5432/tradeguard")
# MCP 客户端（httpx）自动读取代理配置：回环/服务名地址需旁路，否则被代理拦截返回 502
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,mcp-core,mcp-external-mock")
os.environ.setdefault("no_proxy", "localhost,127.0.0.1,mcp-core,mcp-external-mock")
MCP_CORE_URL = os.getenv("MCP_CORE_URL", "http://127.0.0.1:8101/mcp/")
MCP_EXTERNAL_URL = os.getenv("MCP_EXTERNAL_URL", "http://127.0.0.1:8102/mcp/")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=8)
    # US-E3-04 已决策：RocketMQPublisher 包装 InMemory fan-out（SSE 不断链），
    # rocketmq 客户端为运行时可选依赖，未安装时日志明示降级（core/events.py）
    app.state.publisher = RocketMQPublisher(
        os.getenv("ROCKETMQ_NAMESRV", "rocketmq-namesrv:9876"),
        fallback=InMemoryPublisher(),
    )
    app.state.cases = CaseRepository(app.state.pool, app.state.publisher)
    app.state.approvals = ApprovalRepository(app.state.pool)
    app.state.kb = KbRepository(app.state.pool)
    app.state.config = ConfigService(pool=app.state.pool)  # US-E1-03 阈值热加载
    await app.state.config.start()
    # AA-SK-01 确定性聚合内核（US-E3-03/04）：外部源经 AA-MCP-02，落库经 AA-MCP-01（tg_app 写角色）
    app.state.aggregation = AggregationService(
        pool=app.state.pool,
        cases=app.state.cases,
        external=ExternalSourcesClient(MCP_EXTERNAL_URL),
        core=CoreClient(MCP_CORE_URL),
        config=app.state.config,
    )  # BA-BR-05 阈值热加载
    # AA-CL-01/02 闭环修复（工作流 A）：CaseRegistered 消费者——worker 与手动
    # /aggregate 端点共用每 case_id 单飞锁；worker 缺省 OFF（TG_EVENT_WORKER），
    # compose 显式置 on（core/event_worker.py 模块注释）
    app.state.flight = SingleFlight()
    app.state.event_worker = None
    if worker_enabled():
        app.state.event_worker = EventWorker(
            pool=app.state.pool,
            aggregation=app.state.aggregation,
            flight=app.state.flight,
        )
        await app.state.event_worker.start()
    # AA-SK-03 处置执行确定性内核（US-E5-01~04）：建单经 AA-MCP-01（tg_app），决策回填走 tg_web
    core = CoreClient(MCP_CORE_URL)
    app.state.disposition = DispositionService(
        pool=app.state.pool,
        cases=app.state.cases,
        core=core,
        pub=app.state.publisher,
        config=app.state.config,
    )  # SC-06 阈值热加载（D1）
    # AA-SK-02 欺诈调查确定性内核（US-E4-01~03）：图谱/黑名单/证据固化，移交审批
    app.state.investigation = InvestigationService(
        pool=app.state.pool,
        cases=app.state.cases,
        core=core,
        pub=app.state.publisher,
        config=app.state.config,
    )  # SC-06 阈值热加载（BR-06 加分值）
    # AA-SK-04 核验审计确定性内核（US-E6-01/02）：一致归档/不一致反向处置
    app.state.verification = VerificationService(
        pool=app.state.pool, cases=app.state.cases, core=core, pub=app.state.publisher
    )

    async def _escalation_loop():
        # BA-BR-13 审批时效升级（SC-09）+ BA-BR-08 核验超时提醒：定时扫描，异常吞掉防任务退出
        # SC-06 热值（D1）：超时分钟数每轮实时读取，变更不重启生效（scan_* 签名不变）
        def _cfg_int(key: str, default: int) -> int:
            try:
                return int(app.state.config.values[key])
            except (AttributeError, KeyError, TypeError, ValueError):
                return default

        while True:
            try:
                await scan_pending_escalations(
                    app.state.pool,
                    app.state.publisher,
                    minutes=_cfg_int("br-13-approval-timeout-min", ESCALATION_MINUTES),
                )
                await scan_verification_overdue(
                    app.state.pool,
                    app.state.publisher,
                    minutes=_cfg_int(
                        "br-08-verification-timeout-min", VERIFICATION_MINUTES
                    ),
                )
            except Exception:  # noqa: BLE001 —— 后台巡检不中断服务
                logger.exception("BA-BR-13/08 定时扫描异常，等待下轮")
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


# R-37：CORS 收敛——通配 * 改为显式白名单（门户 8300 / Vite dev 5173），
# 可用 TG_CORS_ORIGINS（逗号分隔）按环境覆盖；生产门户同源反代，实际不依赖 CORS
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "TG_CORS_ORIGINS", "http://localhost:8300,http://localhost:5173"
    ).split(",")
    if o.strip()
]


def create_app() -> FastAPI:
    app = FastAPI(
        title="TradeGuard web-api",
        version="0.4.0",
        lifespan=lifespan,
        description="API-W-01~20 契约实现（docs/openapi/tradeguard-openapi.yaml）",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for module in (
        health,
        alerts,
        cases,
        approvals,
        audit,
        kb,
        events_stream,
        config,
        observability,
        demo,
    ):
        app.include_router(module.router)
    return apply_api_guards(
        app
    )  # US-E7-01：bearer 鉴权 + 写操作审计（池由 lifespan 注入）


app = create_app()
