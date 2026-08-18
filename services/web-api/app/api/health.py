"""API-W-15 健康探针 + 组件就绪检查（US-E1-01 验收）

探活范围：postgres（SELECT 1）、RocketMQ namesrv（TCP 可达）、mcp-core /
mcp-external（HTTP 可达，FastMCP 对裸 GET 返回 4xx 亦视为存活）。
任一组件 DOWN → status=DEGRADED，但 HTTP 始终 200（探针语义：进程存活）。
"""
import asyncio
import os
import urllib.error
import urllib.request

from fastapi import APIRouter, Request

router = APIRouter(tags=["系统"])

# 缺省值与 main.py 同源（容器内服务名），避免两处 getenv 缺省漂移
ROCKETMQ_NAMESRV = os.getenv("ROCKETMQ_NAMESRV", "rocketmq-namesrv:9876")
MCP_CORE_URL = os.getenv("MCP_CORE_URL", "http://127.0.0.1:8101/mcp/")
MCP_EXTERNAL_URL = os.getenv("MCP_EXTERNAL_URL", "http://127.0.0.1:8102/mcp/")


async def _check_pg(pool) -> str:
    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return "UP"
    except Exception:
        return "DOWN"


async def _check_tcp(addr: str, timeout: float = 2.0) -> str:
    host, _, port = addr.partition(":")
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, int(port or 9876)), timeout)
        writer.close()
        return "UP"
    except Exception:
        return "DOWN"


def _http_alive(url: str, timeout: float = 2.0) -> str:
    try:
        with urllib.request.urlopen(url, timeout=timeout):  # nosec B310 —— 探活地址来自 env 配置
            return "UP"
    except urllib.error.HTTPError:
        return "UP"  # 4xx/5xx 说明服务进程在响应（FastMCP 对 GET 回 405/406）
    except Exception:
        return "DOWN"


@router.get("/api/health")
async def health(request: Request):
    pg, mq, core, ext = await asyncio.gather(
        _check_pg(request.app.state.pool),
        _check_tcp(ROCKETMQ_NAMESRV),
        asyncio.to_thread(_http_alive, MCP_CORE_URL),
        asyncio.to_thread(_http_alive, MCP_EXTERNAL_URL))
    checks = {"postgres": pg, "rocketmq": mq, "mcp-core": core, "mcp-external": ext}
    up = all(v == "UP" for v in checks.values())
    return {"status": "UP" if up else "DEGRADED", "components": checks}
