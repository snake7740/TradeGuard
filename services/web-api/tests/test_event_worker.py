# -*- coding: utf-8 -*-
"""EventWorker 单元测试（工作流 A1：CaseRegistered 消费者，AA-CL-01/02 闭环修复）

不依赖 DB：FakePool/FakeAggregation 替身验证轮询编排语义——
只捞 REGISTERED、单次处理、失败不重试、异常吞咽清单、单飞锁跳过、开关缺省 OFF。
"""
import asyncio
import uuid

import pytest

from app.core.event_worker import EventWorker, SingleFlight, worker_enabled
from app.repositories import OptimisticLockError
from app.skills.aggregation import AggregationStateError


class FakePool:
    """fetch 返回预设行；记录查询次数"""

    def __init__(self, case_ids):
        self.case_ids = list(case_ids)
        self.queries = 0

    async def fetch(self, query, *args):
        self.queries += 1
        return [{"case_id": c} for c in self.case_ids]


class FakeAggregation:
    def __init__(self, exc=None):
        self.calls: list[str] = []
        self.exc = exc

    async def run(self, case_id):
        self.calls.append(case_id)
        if self.exc is not None:
            raise self.exc


async def test_sweep_processes_each_registered_case_once():
    agg = FakeAggregation()
    w = EventWorker(FakePool(["CASE-A", "CASE-B"]), agg, SingleFlight())
    await w._sweep(window_minutes=None)
    assert agg.calls == ["CASE-A", "CASE-B"]


async def test_swallowed_errors_are_single_shot_no_retry():
    """异常吞咽清单内：单次处理后跳过，不重试（防重复落信号）"""
    for exc in (AggregationStateError("状态不可聚合"), LookupError("CASE-X"),
                OptimisticLockError("version 冲突")):
        agg = FakeAggregation(exc=exc)
        w = EventWorker(FakePool(["CASE-X"]), agg, SingleFlight())
        await w._sweep(window_minutes=None)
        assert agg.calls == ["CASE-X"], f"{type(exc).__name__} 应只处理一次"


async def test_unknown_error_logged_but_not_retried():
    agg = FakeAggregation(exc=RuntimeError("mcp 不可达"))
    w = EventWorker(FakePool(["CASE-Y"]), agg, SingleFlight())
    await w._sweep(window_minutes=None)   # 不得抛出
    assert agg.calls == ["CASE-Y"]        # 同样单次处理，留待人工 /aggregate 重推


async def test_single_flight_skips_busy_case():
    """手动端点持锁时 worker 跳过该案件（防并发重复聚合）"""
    flight = SingleFlight()
    agg = FakeAggregation()
    w = EventWorker(FakePool(["CASE-A", "CASE-B"]), agg, flight)
    async with flight.lock("CASE-A"):     # 模拟 /aggregate 端点正在处理 CASE-A
        await w._sweep(window_minutes=None)
    assert agg.calls == ["CASE-B"]


def test_single_flight_same_key_same_lock():
    flight = SingleFlight()
    assert flight.lock("K") is flight.lock("K")
    assert flight.lock("K") is not flight.lock("OTHER")


async def test_start_stop_lifecycle():
    w = EventWorker(FakePool([]), FakeAggregation(), SingleFlight())
    await w.start()
    assert w._task is not None
    await w.stop()
    assert w._task is None
    await w.stop()                        # 幂等停止


def test_worker_enabled_default_off(monkeypatch):
    """代码缺省 OFF（pytest TestClient 走真实 lifespan 不抢跑）；显式 on 才启用"""
    monkeypatch.delenv("TG_EVENT_WORKER", raising=False)
    assert worker_enabled() is False
    for val, expected in (("on", True), ("1", True), ("true", True),
                          ("off", False), ("0", False), ("", False)):
        monkeypatch.setenv("TG_EVENT_WORKER", val)
        assert worker_enabled() is expected, val
