# -*- coding: utf-8 -*-
"""技能执行 Trace 采集（US-E7-04 可观测，04 §7）

语义对齐 OpenTelemetry span：trace_id（案件链路）+ 业务属性
（case_id/agent_id/skill_id）写入 span attribute。双通道落地：
1. JSONL 追加文件（容器内 logs/traces.jsonl，路径经 TG_TRACES_FILE 可配），
   供 /api/observability/traces 回放与离线评估脚本（scripts/kpi_report.py）消费；
2. OTLP/HTTP 直推 AgentScope Studio（TG_OTLP_ENDPOINT，如 http://as-studio:3000，
   端点 /v1/traces 实测接受标准 OTLP JSON）——best-effort：上报失败仅降级
   JSONL 单通道，不阻断业务链路（观测面故障永不影响处置面）。
案件编号 → traceId 取 md5 确定性映射（OTLP 要求 16 字节 hex），原始 case_id
保留在 span attribute，Studio 内按业务属性检索与审计回放对齐。

Studio v1.0.9 OTLP 入口字段口径（源码实证 dist/server/src/otel/{router,processor}.js）：
顶层键读 camelCase `resourceSpans`，嵌套层与 span 字段只读 snake_case
（scope_spans / trace_id / span_id / start_time_unix_nano / end_time_unix_nano），
attribute 值只读 snake_case `string_value`（camelCase stringValue 会被静默丢弃）；
按 `gen_ai.conversation.id` 属性分组会话。_to_otlp() 按此混合口径输出。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import urllib.request
import uuid
from contextlib import asynccontextmanager

logger = logging.getLogger("tradeguard.tracing")
# 基于模块文件定位服务根（app/core/tracing.py 上溯三层）：与工作目录无关，
# 宿主 pytest / 容器 uvicorn 两种启动方式写同一份 logs/traces.jsonl
SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TRACES_FILE = os.getenv("TG_TRACES_FILE", os.path.join(SERVICE_ROOT, "logs", "traces.jsonl"))
MAX_SPANS = 500          # 内存环形缓冲上限（演示规模，重启清空不影响文件留痕）
OTLP_ENDPOINT = os.getenv("TG_OTLP_ENDPOINT", "").rstrip("/")   # 空=不启用直推
_otlp_state: dict = {}   # 一次性连通提示（避免每 span 重复日志噪音）

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
    if OTLP_ENDPOINT:  # Studio 直推：独立线程 best-effort，慢/断不拖技能主链路
        threading.Thread(target=_otlp_export, args=(span,), daemon=True).start()


def _to_otlp(span: dict) -> dict:
    """业务 span → OTLP/HTTP JSON（Studio v1.0.9 实测口径，非标准 proto3 映射）

    字段口径为 Studio 源码实证（见模块 docstring）：顶层 resourceSpans 用
    camelCase（router.js JSON.parse 直读），嵌套 scope_spans 与 span 字段、
    attribute 值一律 snake_case（processor.js 逐字段读取）；标准 OTLP JSON
    的 camelCase 嵌套字段会被静默丢弃（200 但不入库）。
    gen_ai.conversation.id=case_id 供 Studio 按案件分组会话。
    """
    def _sv(v):
        return {"string_value": str(v)}

    attrs = [
        {"key": "case_id", "value": _sv(span["trace_id"])},
        {"key": "gen_ai.conversation.id", "value": _sv(span["trace_id"])},
    ]
    for k in ("agent_id", "skill_id"):
        if span.get(k):
            attrs.append({"key": k, "value": _sv(span[k])})
    for k, v in (span.get("attrs") or {}).items():
        attrs.append({"key": k, "value": _sv(v)})
    if span.get("error"):
        attrs.append({"key": "error", "value": _sv(span["error"])})
    return {"resourceSpans": [{
        "resource": {"attributes": [
            {"key": "service.name", "value": _sv("tradeguard-web-api")}]},
        "scope_spans": [{"scope": {"name": "tradeguard.tracing"}, "spans": [{
            "trace_id": hashlib.md5(span["trace_id"].encode()).hexdigest(),
            "span_id": span["span_id"], "name": span["name"], "kind": 1,
            "start_time_unix_nano": str(int(span["start_ts"] * 1e9)),
            "end_time_unix_nano": str(int(span["end_ts"] * 1e9)),
            "attributes": attrs,
            "status": {"code": 2} if span.get("error") else {},
        }]}]}]}


def _otlp_export(span: dict):
    try:
        req = urllib.request.Request(
            OTLP_ENDPOINT + "/v1/traces",
            data=json.dumps(_to_otlp(span), ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            resp.read()
        if not _otlp_state.get("ok"):
            _otlp_state["ok"] = True
            logger.info("OTLP trace 上报已连通：%s/v1/traces（AgentScope Studio）", OTLP_ENDPOINT)
    except Exception:  # noqa: BLE001 —— Studio 不可达仅降级 JSONL 单通道
        if not _otlp_state.get("warned"):
            _otlp_state["warned"] = True
            logger.warning("OTLP 上报不可用（%s），trace 降级为仅 JSONL 留痕", OTLP_ENDPOINT)


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
