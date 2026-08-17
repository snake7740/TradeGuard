# -*- coding: utf-8 -*-
"""演示辅助路由（API-W-21）

门户"触发演示事件"入口取真实主体：从 account 表选无未结案件的主体
（随机 subject_ref 不在三源底表内，聚合会走降级路径，演示语义失真）。
"""
from fastapi import APIRouter, Query, Request

router = APIRouter(prefix="/api/demo", tags=["演示"])


@router.get("/subjects")
async def list_demo_subjects(request: Request, limit: int = Query(10, ge=1, le=50),
                             severity: str | None = Query(None,
                                                          pattern="^(low|medium|high)$")):
    """API-W-21：可用演示主体清单（无未结案件的真实 account_hash）

    过滤：存在非 ARCHIVED 案件的主体排除（避免演示撞已有案件）；
    list_flag/risk_level 一并返回供前端展示。
    severity 软过滤（BUG-01/R-46）：severity 与聚合评分无因果，随机取材会令
    "高风险"演示恒走低分自动放行——high 限定 black/block 名单主体（BA-BR-04
    黑名单垫分 75，必入"调查→审批"人机链），low 限定 none 干净主体，
    medium/缺省不过滤（兼容 v1.4.4 行为）。
    """
    flag_filter = ""
    if severity == "high":
        flag_filter = "AND a.list_flag IN ('black', 'block')"
    elif severity == "low":
        flag_filter = "AND a.list_flag = 'none' AND a.risk_level = 0"
    rows = await request.app.state.pool.fetch(
        f"""SELECT a.account_hash, a.list_flag, a.risk_level
           FROM account a
           WHERE NOT EXISTS (
               SELECT 1 FROM risk_case c
               WHERE c.subject_ref = a.account_hash AND c.status <> 'ARCHIVED')
           {flag_filter}
           ORDER BY random() LIMIT $1""", limit)
    return {"items": [{"subject_ref": r["account_hash"].strip(),
                       "list_flag": r["list_flag"], "risk_level": r["risk_level"]}
                      for r in rows]}
