"""事件驱动闭环消费端（AA-CL-01/02 闭环修复，工作流 A1）

背景：`CaseRegistered` 曾无任何消费者——闭环第一跳（立案→聚合）靠人工推动。
本模块补上这一跳，让五阶段闭环的"信号聚合"由事件驱动自动起步。

设计决策（Plan agent 核实后定稿）：
- **主力 = DB 轮询，MQ 尽力而为**：rocketmq-client-python 无 cp312 wheel（A2 预检
  结论），消费侧不依赖 MQ 客户端；轮询语义与 MQ 消费等价（捞 REGISTERED 即消费）。
- **只捞 REGISTERED、单次处理、失败不重试**：聚合会落 DA-T-04 信号，重跑会重复落
  信号污染评分；失败案件由异常吞咽后留待人工（`/aggregate` 端点可手动重推）。
- **进程内每 case_id 单飞锁**（SingleFlight）：worker 与手动 `/aggregate` 端点
  共用，防止轮询与人工触发并发重复聚合。
- **all_fail 案件停在 AGGREGATING 属"转人工"语义，非 bug**（E-AGG-ALL-FAIL）。
- 开关 `TG_EVENT_WORKER` 代码缺省 **OFF**（pytest TestClient 走真实 lifespan，
  缺省开启会抢跑测试用例造成 flake）；docker-compose.yml 显式置 on。
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger("tradeguard.event_worker")

POLL_INTERVAL = float(os.getenv("TG_EVENT_WORKER_INTERVAL", "2"))   # 轮询周期（秒）
POLL_WINDOW_MIN = int(os.getenv("TG_EVENT_WORKER_WINDOW", "10"))   # 增量窗口（分钟）
MAX_RETRIES = int(os.getenv("TG_EVENT_WORKER_RETRIES", "3"))       # 阶段2 R-41：有限重试
RETRY_BASE_DELAY = float(os.getenv("TG_EVENT_WORKER_RETRY_DELAY", "5"))  # 线性退避基数（秒）


class SingleFlight:
    """每 key 一把进程内锁：worker 与 /aggregate 端点共用，防同案并发重复聚合"""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def lock(self, key: str) -> asyncio.Lock:
        lk = self._locks.get(key)
        if lk is None:
            lk = self._locks[key] = asyncio.Lock()
        return lk


class EventWorker:
    """CaseRegistered 消费者：DB 轮询捞 REGISTERED 案件并驱动 AA-SK-01 聚合。

    启动时先**无窗口全扫一次**（补捞停机期积压的 REGISTERED 案件），
    之后每 POLL_INTERVAL 秒轮询近 POLL_WINDOW_MIN 分钟内新立案者。
    """

    def __init__(self, pool, aggregation, flight: SingleFlight) -> None:
        self._pool = pool
        self._agg = aggregation
        self._flight = flight
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())
            logger.info("EventWorker 已启动（轮询 %ss，窗口 %s 分钟）",
                        POLL_INTERVAL, POLL_WINDOW_MIN)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            logger.info("EventWorker 已停止")

    async def _loop(self) -> None:
        await self._sweep(window_minutes=None)          # 启动全扫：补停机期积压
        while True:
            await asyncio.sleep(POLL_INTERVAL)
            await self._sweep(window_minutes=POLL_WINDOW_MIN)

    async def _sweep(self, window_minutes: int | None) -> None:
        try:
            if window_minutes is None:
                rows = await self._pool.fetch(
                    "SELECT case_id FROM risk_case WHERE status='REGISTERED' "
                    "ORDER BY created_at")
            else:
                rows = await self._pool.fetch(
                    "SELECT case_id FROM risk_case WHERE status='REGISTERED' "
                    "AND created_at >= now() - make_interval(mins=>$1) "
                    "ORDER BY created_at", window_minutes)
        except Exception:  # noqa: BLE001 —— DB 抖动不断 worker 循环
            logger.exception("EventWorker 轮询查询失败，等待下轮")
            return
        for r in rows:
            await self._process_one(r["case_id"])

    async def _process_one(self, case_id: str) -> None:
        from ..repositories import OptimisticLockError
        from ..skills.aggregation import AggregationStateError

        lk = self._flight.lock(case_id)
        if lk.locked():
            return  # 手动端点或上一轮正在处理该案件，跳过
        async with lk:
            for attempt in range(MAX_RETRIES):
                try:
                    await self._agg.run(case_id)
                    return
                except (AggregationStateError, LookupError, OptimisticLockError):
                    # 状态已被他人推进/案件消失/version 冲突（他人已接管）：静默跳过
                    logger.info("EventWorker 跳过 %s（状态已被接管或不可聚合）", case_id)
                    return
                except Exception as e:  # noqa: BLE001 —— 有限重试（幂等落库）+ 退避
                    if attempt < MAX_RETRIES - 1:
                        delay = RETRY_BASE_DELAY * (attempt + 1)
                        logger.warning("EventWorker %s 失败（第 %s/%s 次），%ss 后重试：%s",
                                       case_id, attempt + 1, MAX_RETRIES, delay, type(e).__name__)
                        await asyncio.sleep(delay)
                    else:
                        logger.exception("EventWorker %s 重试耗尽（%s 次），转人工通道",
                                         case_id, MAX_RETRIES)


def worker_enabled() -> bool:
    """TG_EVENT_WORKER 开关：代码缺省 OFF，compose 显式 on（见模块 docstring）"""
    return os.getenv("TG_EVENT_WORKER", "").strip().lower() in ("1", "on", "true", "yes")
