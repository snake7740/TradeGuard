# -*- coding: utf-8 -*-
"""技能执行 Trace 采集（US-E7-04 可观测，04 §7）

语义对齐 OpenTelemetry span：trace_id（案件链路）+ 业务属性
（case_id/agent_id/skill_id）写入 span attribute。落地形态取确定性优先：
JSONL 追加文件（容器内 logs/traces.jsonl，路径经 TG_TRACES_FILE 可配），
供 /api/observability/traces 回放与离线评估脚本（scripts/kpi_report.py）消费；
AgentScope Studio（compose as-studio:3000）承载 Agent 侧 LLM/工具调用观测，
与本业务 Trace 经 case_id 关联（演示规模不做 OTLP 直推，生产可换导出器）。
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

logger = logging.getLogger("tradeguard.tracing")
TRACES_FILE = os.getenv("TG_TRACES_FILE", "logs/traces.jsonl")
MAX_SPANS = 500          # 内存环形缓冲上限（演示规模，重启清空不影响文件留痕）

_spans: list[dict] = []


@asynccontextmanager
async def skill_span(skill_id: str, agent_id: str, case_id: str, **attrs):
    """技能执行 span：成功/异常均落 span（status 区分），异常原样上抛不吞"""
    span = {"span_id": uuid.uuid4().hex[:16], "trace_id": case_id,
            "name": skill_id, "agent_id": agent_id, "skill_id": skill_id,
            "case_id": case_id, "attrs": attrs,
            "start_ts": round(time.time(), 3), "status": "ok"}
    try:
        yield span
    except Exception as e:
        span["status"] = "error"
        span["error"] = type(e).__name__
        raise
    finally:
        span["end_ts"] = round(time.time(), 3)
        span["duration_ms"] = round((span["end_ts"] - span["start_ts"]) * 1000, 1)
        _record(span)


def _record(span: dict):
    _spans.append(span)
    if len(_spans) > MAX_SPANS:
        del _spans[: len(_spans) - MAX_SPANS]
    try:
        os.makedirs(os.path.dirname(TRACES_FILE) or ".", exist_ok=True)
        with open(TRACES_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(span, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 —— 观测失败不阻断业务链路
        logger.exception("trace 落盘失败")


def recent_spans(limit: int = 100) -> list[dict]:
    return _spans[-limit:]


def load_spans(limit: int = 200) -> list[dict]:
    """从 JSONL 文件回放（跨重启留痕，离线评估与 Studio 关联的数据源）"""
    try:
        with open(TRACES_FILE, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
