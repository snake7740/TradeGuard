"""领域事件发布抽象（03 §9.2 事件目录，TA-C-06）

端口/适配器模式：
- EventPublisher（Port）：publish(case_id, event, payload, actor)，消息体固定
  case_id/trace_id/occurred_at/payload 四字段（03 §9.2）；
- RocketMQPublisher（Adapter，Sprint 1 US-E1-03/E3）：投递 Topic=case-events、Tag=事件类型；
- InMemoryPublisher（Sprint 0 默认）：进程内 fan-out 到 SSE 订阅者（API-W-14），
  保证演示链路"事件可见"不因 MQ 客户端缺位而断链。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Protocol

logger = logging.getLogger("tradeguard.events")


class EventPublisher(Protocol):
    async def publish(
        self,
        case_id: str,
        event: str,
        payload: dict,
        actor: str,
        trace_id: str | None = None,
    ) -> dict: ...


def _envelope(
    case_id: str, event: str, payload: dict, actor: str, trace_id: str | None = None
) -> dict:
    """03 §9.2 消息体 Schema：case_id / trace_id / occurred_at / payload

    trace_id 透传案件 trace_id（A4 闭环修复）：使同案事件在可观测侧可串联回放；
    调用方未提供时回落为新 uuid（兼容无案件上下文的定时器类事件）。
    """
    return {
        "case_id": case_id,
        "trace_id": trace_id or uuid.uuid4().hex,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "actor": actor,
        "payload": payload,
    }


class InMemoryPublisher:
    """进程内事件总线：fan-out 到 API-W-14 SSE 订阅队列"""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    async def publish(
        self,
        case_id: str,
        event: str,
        payload: dict,
        actor: str,
        trace_id: str | None = None,
    ) -> dict:
        msg = _envelope(case_id, event, payload, actor, trace_id)
        for q in list(self._subscribers):
            try:
                q.put_nowait(json.dumps(msg, ensure_ascii=False))
            except asyncio.QueueFull:
                pass  # 慢消费者降级丢弃，不阻塞主链路
        return msg


class RocketMQPublisher:
    """RocketMQ 适配器（US-E3-04 已决策）：可选导入 rocketmq-client-python 投递
    case-events Topic（Tag=事件类型，ProducerGroup=tg-web）；客户端未安装或启动
    失败时日志明示降级，不新增强制依赖。SSE fan-out 始终经 fallback 保留
    （API-W-14 不因 MQ 断链），subscribe/unsubscribe 委托 fallback。"""

    def __init__(self, namesrv: str, fallback: InMemoryPublisher) -> None:
        self._fallback = fallback
        self._producer = None
        try:
            from rocketmq.client import Producer  # pyright: ignore[reportMissingImports]

            producer = Producer("tg-web")
            # 版本拼写差异（实测容器内 dir(Producer) 为准）：0.5.0rc2（cp312 唯一轮子）
            # 为 set_namesrv_addr；历史版本另有 set_name_server_addr / 2.x set_name_server_address。
            # 按存在性探测，全部缺失才降级。
            for setter in (
                "set_namesrv_addr",
                "set_name_server_address",
                "set_name_server_addr",
            ):
                if hasattr(producer, setter):
                    getattr(producer, setter)(namesrv)
                    break
            else:
                raise AttributeError(
                    "rocketmq Producer 无 namesrv 设置方法（版本不兼容）"
                )
            producer.start()
            self._producer = producer
            logger.info("RocketMQPublisher 已连接 namesrv=%s", namesrv)
        except Exception as e:  # noqa: BLE001 —— 未安装/不可达一律降级
            logger.warning(
                "RocketMQ 客户端不可用（%s: %s），降级 InMemory fan-out",
                type(e).__name__,
                e,
            )

    def subscribe(self) -> asyncio.Queue:
        return self._fallback.subscribe()

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._fallback.unsubscribe(q)

    async def publish(
        self,
        case_id: str,
        event: str,
        payload: dict,
        actor: str,
        trace_id: str | None = None,
    ) -> dict:
        msg = await self._fallback.publish(
            case_id, event, payload, actor, trace_id
        )  # SSE 不断链
        if self._producer is not None:
            try:
                from rocketmq.client import Message  # pyright: ignore[reportMissingImports]

                m = Message("case-events")
                m.set_tags(event)
                m.set_body(json.dumps(msg, ensure_ascii=False))
                await asyncio.to_thread(self._producer.send_sync, m)
            except Exception:  # noqa: BLE001 —— 发送失败不阻断主链路
                logger.exception("RocketMQ 投递失败：case=%s event=%s", case_id, event)
        return msg
