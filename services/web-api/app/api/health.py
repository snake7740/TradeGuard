"""API-W-15 健康探针 + 组件就绪检查（US-E1-01 验收）"""
from fastapi import APIRouter, Request

router = APIRouter(tags=["observability"])


@router.get("/api/health")
async def health(request: Request):
    checks = {"postgres": "DOWN"}
    try:
        async with request.app.state.pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        checks["postgres"] = "UP"
    except Exception:
        pass
    up = all(v == "UP" for v in checks.values())
    return {"status": "UP" if up else "DEGRADED", "components": checks}
