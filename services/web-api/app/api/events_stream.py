"""领域事件实时推送（API-W-14 SSE，全体演示载体）
Sprint 0：订阅进程内事件总线（InMemoryPublisher）；Sprint 1 切 RocketMQ
消费者后仅需替换事件来源，本路由协议不变（端口/适配器收益）。
"""
import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter(tags=["events"])


@router.get("/api/events/stream")
async def event_stream(request: Request):
    """API-W-14：SSE 推送领域事件（03 §9.2 消息体 Schema）"""
    bus = request.app.state.publisher

    async def gen():
        q = bus.subscribe()
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"  # SSE 心跳，防代理断链
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
