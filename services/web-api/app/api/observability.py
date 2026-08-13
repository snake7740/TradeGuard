# -*- coding: utf-8 -*-
"""API-W-20 技能执行 Trace 回放（US-E7-04 可观测，04 §7）

GET /api/observability/traces —— 从 JSONL 留痕文件回放最近 span
（跨重启可读），供门户观测页与离线评估脚本消费；trace_id=case_id
与 AgentScope Studio（as-studio:3000）的 Agent 侧观测关联。
"""
from fastapi import APIRouter, Query

from ..core.tracing import load_spans

router = APIRouter(prefix="/api/observability", tags=["系统"])


@router.get("/traces")
async def get_traces(limit: int = Query(200, ge=1, le=1000, description="回放条数上限"),
                     case_id: str | None = Query(None, description="按案件过滤")):
    """回放技能执行 span（最新在后）；case_id 过滤用于单案件全链回放（SC-08 留痕）"""
    spans = load_spans(limit if case_id is None else 1000)
    if case_id:
        spans = [s for s in spans if s.get("case_id") == case_id][-limit:]
    return {"count": len(spans), "spans": spans}
