"""API-W-16 · BA-BR 阈值动态配置（US-E1-03，契约 openapi /api/config/thresholds）"""
from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/config", tags=["系统"])


@router.get("/thresholds")
async def get_threshold_config(request: Request):
    """读取当前阈值与来源（nacos 优先 / db 降级），5s 热加载不重启生效"""
    return request.app.state.config.snapshot()
