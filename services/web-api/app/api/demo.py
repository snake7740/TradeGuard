# -*- coding: utf-8 -*-
"""演示辅助路由（API-W-21）

门户"触发演示事件"入口取真实主体：从 account 表选无未结案件的主体
（随机 subject_ref 不在三源底表内，聚合会走降级路径，演示语义失真）。
"""
from fastapi import APIRouter, Query, Request

router = APIRouter(prefix="/api/demo", tags=["演示"])


@router.get("/subjects")
async def list_demo_subjects(request: Request, limit: int = Query(10, ge=1, le=50)):
    """API-W-21：可用演示主体清单（无未结案件的真实 account_hash）

    过滤：存在非 ARCHIVED 案件的主体排除（避免演示撞已有案件）；
    list_flag/risk_level 一并返回供前端展示（不做硬过滤，保留选择权）。
    """
    rows = await request.app.state.pool.fetch(
        """SELECT a.account_hash, a.list_flag, a.risk_level
           FROM account a
           WHERE NOT EXISTS (
               SELECT 1 FROM risk_case c
               WHERE c.subject_ref = a.account_hash AND c.status <> 'ARCHIVED')
           ORDER BY random() LIMIT $1""", limit)
    return {"items": [{"subject_ref": r["account_hash"].strip(),
                       "list_flag": r["list_flag"], "risk_level": r["risk_level"]}
                      for r in rows]}
