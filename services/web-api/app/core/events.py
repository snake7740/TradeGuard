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
import uuid
from datetime import datetime, timezone
from typing import Protocol


class EventPublisher(Protocol):
    async def publish(self, case_id: str, event: str, payload: dict, actor: str) -> dict: ...


def _envelope(case_id: str, event: str, payload: dict, actor: str) -> dict:
    """03 §9.2 消息体 Schema：case_id / trace_id / occurred_at / payload"""
    return {
        "case_id": case_id,
        "trace_id": uuid.uuid4().hex,
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

    async def publish(self, case_id: str, event: str, payload: dict, actor: str) -> dict:
        msg = _envelope(case_id, event, payload, actor)
        for q in list(self._subscribers):
            try:
                q.put_nowait(json.dumps(msg, ensure_ascii=False))
            except asyncio.QueueFull:
                pass  # 慢消费者降级丢弃，不阻塞主链路
        return msg


class RocketMQPublisher:
    """TODO(Sprint 1 US-E3-04)：经 rocketmq-client-python 投递 case-events Topic。
    Tag=事件类型；ProducerGroup=tg-web；发送失败降级 InMemory 并写 audit_log 告警。"""

    def __init__(self, namesrv: str, fallback: InMemoryPublisher) -> None:
        self._fallback = fallback

    async def publish(self, case_id: str, event: str, payload: dict, actor: str) -> dict:
        # TODO: producer.send(Message(topic="case-events", tag=event, body=json.dumps(msg)))
        return await self._fallback.publish(case_id, event, payload, actor)
