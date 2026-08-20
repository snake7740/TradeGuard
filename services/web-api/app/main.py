"""TradeGuard web-api 应用工厂（web-portal 后端进程，04 §10.1 三端真实调用链）

分层与依赖装配（Sprint 0 架构模板）：
  api/*（路由层，API-W-01~22 全量声明）
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
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

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
    skills,
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
    follow_outcomes,
    scan_pending_escalations,
)
from .skills.investigation import InvestigationService
from .skills.knowledge import KB_DECAY_DAYS, kb_metabolism
from .skills.mcp_adapters import CoreClient, ExternalSourcesClient
from .skills.verification import (
    VERIFICATION_MINUTES,
    VerificationService,
    scan_verification_overdue,
)

logger = logging.getLogger("tradeguard.web")
# BUG-06/R-46：uvicorn 默认 log config 只配 uvicorn.* 自家 logger，root 无 handler，
# tradeguard.* 的 INFO 全部丢失（lastResort 仅输出 WARNING+）——EventWorker 启动/
# 委托/重试在容器 stdout 无痕，排障只能靠 DB 取证。basicConfig 补 root handler，
# 级别经 TG_LOG_LEVEL 覆盖（缺省 INFO）。
logging.basicConfig(
    level=os.getenv("TG_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
ESCALATION_SCAN_INTERVAL = float(
    os.getenv("ESCALATION_SCAN_INTERVAL", "30")
)  # BA-BR-13 轮询周期（秒）
KB_METABOLISM_INTERVAL = float(
    os.getenv("KB_METABOLISM_INTERVAL", "3600")
)  # E1 知识代谢轮询周期（秒，低频：降级窗口以天计，US-E11）

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
    external = ExternalSourcesClient(MCP_EXTERNAL_URL)
    app.state.aggregation = AggregationService(
        pool=app.state.pool,
        cases=app.state.cases,
        external=external,
        core=CoreClient(MCP_CORE_URL),
        config=app.state.config,
    )  # BA-BR-05 阈值热加载
    # AA-CL-01/02 闭环修复（工作流 A）：CaseRegistered 消费者——worker 与手动
    # /aggregate 端点共用每 case_id 单飞锁；worker 缺省 OFF（TG_EVENT_WORKER），
    # compose 显式置 on（core/event_worker.py 模块注释）
    app.state.flight = SingleFlight()
    app.state.event_worker = None
    # AA-SK-03 处置执行确定性内核（US-E5-01~04）：建单经 AA-MCP-01（tg_app），决策回填走 tg_web
    core = CoreClient(MCP_CORE_URL)
    app.state.disposition = DispositionService(
        pool=app.state.pool,
        cases=app.state.cases,
        core=core,
        pub=app.state.publisher,
        config=app.state.config,
    )  # SC-06 阈值热加载（D1）
    # AA-SK-02 欺诈调查确定性内核（US-E4-01~03）：图谱/黑名单/证据固化，移交审批；
    # R-47 AG-01 规划-反思注入同 external 通道（选择性深查与 AG-02 全量互补）
    app.state.investigation = InvestigationService(
        pool=app.state.pool,
        cases=app.state.cases,
        core=core,
        pub=app.state.publisher,
        config=app.state.config,
        external=external,
    )  # SC-06 阈值热加载（BR-06 加分值）
    # R-46 方案甲：worker 注入双内核，滞留 INVESTIGATING 案件超时自动委托
    # （TG_DELEGATE_INVESTIGATING_SECONDS，代码缺省 0=OFF，compose 置 900）
    if worker_enabled():
        app.state.event_worker = EventWorker(
            pool=app.state.pool,
            aggregation=app.state.aggregation,
            flight=app.state.flight,
            investigation=app.state.investigation,
            disposition=app.state.disposition,
        )
        await app.state.event_worker.start()
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

    async def _metabolism_loop():
        # E1 知识代谢（BA-BR-20）+ C2 outcome 长窗回填（US-E11/E12）：
        # 低频巡检，异常吞掉防任务退出（与 _escalation_loop 同构）
        while True:
            try:
                await kb_metabolism(
                    app.state.pool, app.state.publisher, decay_days=KB_DECAY_DAYS)
                await follow_outcomes(
                    app.state.pool, app.state.publisher, core=core)
            except Exception:  # noqa: BLE001 —— 后台巡检不中断服务
                logger.exception("E1 知识代谢/C2 outcome 回填任务异常，等待下轮")
            await asyncio.sleep(KB_METABOLISM_INTERVAL)

    metabolism_task = asyncio.create_task(_metabolism_loop())
    yield
    escalation_task.cancel()
    metabolism_task.cancel()
    try:
        await escalation_task
    except asyncio.CancelledError:
        pass
    try:
        await metabolism_task
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
        description="API-W-01~22 契约实现（docs/openapi/tradeguard-openapi.yaml）",
    )

    # BUG-05/R-46：契约外路径默认 404（{"detail":"Not Found"}）统一改写为契约
    # 错误信封——与 R-20 错误语义一致。仅命中 Starlette 路由层抛的默认 404；
    # 路由内 raise HTTPException(detail={"code":...}) 走 FastAPI 自身 handler
    # 原样透传，互不干扰（异常类型 MRO 决定分发）。
    @app.exception_handler(StarletteHTTPException)
    async def _contract_404_envelope(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404 and exc.detail == "Not Found":
            path = request.scope["path"]  # scope 同源，防 Host 头污染（R-37 同型）
            return JSONResponse(status_code=404, content={
                "code": "E-NOT-FOUND",
                "message": f"接口路径不存在：{path}（请核对 OpenAPI 契约；"
                           f"事件流为 /api/events/stream，案件事件回放为 /api/audit/{{case_id}}）",
            })
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail},
                            headers=getattr(exc, "headers", None))

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
        skills,
        demo,
    ):
        app.include_router(module.router)
    return apply_api_guards(
        app
    )  # US-E7-01：bearer 鉴权 + 写操作审计（池由 lifespan 注入）


app = create_app()
