"""API-W-16 · BA-BR 阈值动态配置（US-E1-03，契约 openapi /api/config/thresholds）"""

import json
import re
import uuid

import asyncpg  # pyright: ignore[reportMissingImports] —— 已装 .venv，静态分析器未解析 venv 站点包
from fastapi import APIRouter, Header, HTTPException, Request

from .common import operator_from_header

router = APIRouter(prefix="/api/config", tags=["系统"])

# R-37：阈值键白名单（BA-BR 命名规范 br-NN-slug，00 §3 / 08 数据字典）——
# 拒绝任意键注入（此前任何键都会被合并写回 Nacos 权威源并 INSERT 进 sys_config）
# 复审收口：`\Z` 锚定——Python `$` 允许尾部单个换行通过（"br-01-x\n" 会命中）
KEY_PATTERN = re.compile(r"^br-[0-9]{2}-[a-z0-9][a-z0-9-]{0,60}\Z")
VALUE_MAX = 100000.0  # 阈值合理上界（金额类 br-07 已远低于此）


@router.get("/thresholds")
async def get_threshold_config(request: Request):
    """读取当前阈值与来源（nacos 优先 / db 降级），5s 热加载不重启生效"""
    return request.app.state.config.snapshot()


@router.put("/thresholds")
async def put_threshold_config(
    request: Request,
    body: dict[str, str | int | float],
    x_operator: str | None = Header(None),
):
    """阈值键值写入（SC-06 D3 顺序修正）：先写回 Nacos 权威源 → 再写 sys_config
    DB 镜像（DA-T-11）→ 审计留痕 → 触发 ConfigService 即时重载。
    Nacos 写回失败不阻断：仍写 DB 并在响应暴露 nacos_writeback=false 与 source。
    tg_web 对 sys_config 仅有 UPDATE 授权（02-roles.sql），已播种键走 UPDATE；
    UPDATE 0 时尝试 INSERT（新键），权限拒绝则回 400。"""
    if not body:
        raise HTTPException(
            422, detail={"code": "E-EMPTY", "message": "thresholds 不能为空"}
        )
    # R-37：键白名单 + 值域校验（防任意键注入 Nacos/sys_config 与非数值阈值污染）
    for key, value in body.items():
        if not KEY_PATTERN.match(key):
            raise HTTPException(
                422,
                detail={
                    "code": "E-CONFIG-KEY-FORMAT",
                    "message": f"配置键 {key} 不符合 br-NN-slug 白名单格式（R-37）",
                },
            )
        try:
            num = float(value)
        except (TypeError, ValueError):
            raise HTTPException(
                422,
                detail={
                    "code": "E-CONFIG-VALUE",
                    "message": f"配置键 {key} 的值必须可解析为数值",
                },
            ) from None
        if not (0 <= num <= VALUE_MAX):
            raise HTTPException(
                422,
                detail={
                    "code": "E-CONFIG-VALUE-RANGE",
                    "message": f"配置键 {key} 的值超出 [0, {VALUE_MAX:g}] 合理区间",
                },
            )
    pool = request.app.state.pool
    # R-37 复审收口：键存在性闸门前置于 Nacos 写回——格式合法但未播种的键
    # （如 br-99-x / 换行键）必须先 400 拒绝，不能先写 Nacos 权威文档污染快照
    # （tg_web 无 sys_config INSERT 权限，DB 层 400 是兜底；闸门前移避免脏写权威源，
    # ConfigService._reload 整文档无过滤装载，污染键会驻留 snapshot 并被后续写回持久化）
    for key in body:
        exists = await pool.fetchval(
            "SELECT EXISTS(SELECT 1 FROM sys_config WHERE key=$1)", key
        )
        if not exists:
            raise HTTPException(
                400,
                detail={
                    "code": "E-CONFIG-KEY",
                    "message": f"配置键 {key} 不存在且无新增权限",
                },
            )
    operator = operator_from_header(x_operator, "human:operator")
    config = request.app.state.config
    # Nacos dataId content 为完整文档：以当前快照为基合并本次变更再整体写回
    merged = {**config.values, **{k: str(v) for k, v in body.items()}}
    nacos_ok = await config.publish(merged)
    for key, value in body.items():
        val = str(value)
        async with pool.acquire() as conn, conn.transaction():
            updated = await conn.execute(
                """UPDATE sys_config SET value=$2, version=version+1, source='api',
                       updated_at=now() WHERE key=$1""",
                key,
                val,
            )
            if updated == "UPDATE 0":
                try:
                    await conn.execute(
                        "INSERT INTO sys_config (key, value, source) VALUES ($1, $2, 'api')",
                        key,
                        val,
                    )
                except asyncpg.PostgresError:
                    raise HTTPException(
                        400,
                        detail={
                            "code": "E-CONFIG-KEY",
                            "message": f"配置键 {key} 不存在且无新增权限",
                        },
                    ) from None
    await pool.execute(
        """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
           VALUES ($1, $2, 'config.thresholds.put', 'sys_config', $3, $4)""",
        uuid.uuid4().hex,
        operator,
        json.dumps(body, ensure_ascii=False)[:300],
        uuid.uuid4().hex,
    )
    await config.reload()
    return config.snapshot() | {"nacos_writeback": nacos_ok}
