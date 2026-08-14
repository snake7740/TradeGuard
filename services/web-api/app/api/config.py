"""API-W-16 · BA-BR 阈值动态配置（US-E1-03，契约 openapi /api/config/thresholds）"""
import json
import uuid

import asyncpg
from fastapi import APIRouter, Header, HTTPException, Request

from .common import operator_from_header

router = APIRouter(prefix="/api/config", tags=["系统"])


@router.get("/thresholds")
async def get_threshold_config(request: Request):
    """读取当前阈值与来源（nacos 优先 / db 降级），5s 热加载不重启生效"""
    return request.app.state.config.snapshot()


@router.put("/thresholds")
async def put_threshold_config(request: Request, body: dict[str, str | int | float],
                               x_operator: str | None = Header(None)):
    """阈值键值写入（SC-06 D3 顺序修正）：先写回 Nacos 权威源 → 再写 sys_config
    DB 镜像（DA-T-11）→ 审计留痕 → 触发 ConfigService 即时重载。
    Nacos 写回失败不阻断：仍写 DB 并在响应暴露 nacos_writeback=false 与 source。
    tg_web 对 sys_config 仅有 UPDATE 授权（02-roles.sql），已播种键走 UPDATE；
    UPDATE 0 时尝试 INSERT（新键），权限拒绝则回 400。"""
    if not body:
        raise HTTPException(422, detail={"code": "E-EMPTY", "message": "thresholds 不能为空"})
    operator = operator_from_header(x_operator, "human:operator")
    config = request.app.state.config
    # Nacos dataId content 为完整文档：以当前快照为基合并本次变更再整体写回
    merged = {**config.values, **{k: str(v) for k, v in body.items()}}
    nacos_ok = await config.publish(merged)
    pool = request.app.state.pool
    for key, value in body.items():
        val = str(value)
        async with pool.acquire() as conn, conn.transaction():
            updated = await conn.execute(
                """UPDATE sys_config SET value=$2, version=version+1, source='api',
                       updated_at=now() WHERE key=$1""", key, val)
            if updated == "UPDATE 0":
                try:
                    await conn.execute(
                        "INSERT INTO sys_config (key, value, source) VALUES ($1, $2, 'api')",
                        key, val)
                except asyncpg.PostgresError:
                    raise HTTPException(400, detail={
                        "code": "E-CONFIG-KEY", "message": f"配置键 {key} 不存在且无新增权限"})
    await pool.execute(
        """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
           VALUES ($1, $2, 'config.thresholds.put', 'sys_config', $3, $4)""",
        uuid.uuid4().hex, operator,
        json.dumps(body, ensure_ascii=False)[:300], uuid.uuid4().hex)
    await config.reload()
    return config.snapshot() | {"nacos_writeback": nacos_ok}
