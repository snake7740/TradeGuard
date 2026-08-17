# -*- coding: utf-8 -*-
"""EventWorker 单元测试（工作流 A1：CaseRegistered 消费者，AA-CL-01/02 闭环修复）

不依赖 DB：FakePool/FakeAggregation 替身验证轮询编排语义——
只捞 REGISTERED、异常吞咽清单单次跳过、未知错误有限重试、单飞锁跳过、开关缺省 OFF。
"""
import asyncio
import uuid

import pytest

from app.core.event_worker import MAX_RETRIES, EventWorker, SingleFlight, worker_enabled
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


async def test_unknown_error_retried_then_gives_up(monkeypatch):
    """未知错误有限重试 MAX_RETRIES 次后放弃（阶段2 R-41：at-least-once，耗尽转人工）"""
    monkeypatch.setattr("app.core.event_worker.RETRY_BASE_DELAY", 0)  # 免线性退避等待
    agg = FakeAggregation(exc=RuntimeError("mcp 不可达"))
    w = EventWorker(FakePool(["CASE-Y"]), agg, SingleFlight())
    await w._sweep(window_minutes=None)   # 不得抛出
    assert agg.calls == ["CASE-Y"] * MAX_RETRIES  # 重试 MAX_RETRIES 次


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


# ---------- R-46 方案甲：INVESTIGATING 超时自动委托 ----------

class FakeDelegatePool:
    """委托替身池：fetch 返回预设 INVESTIGATING 行，fetchrow 复核可配状态"""

    def __init__(self, case_ids, status="INVESTIGATING"):
        self.case_ids = list(case_ids)
        self.status = status

    async def fetch(self, query, *args):
        return [{"case_id": c} for c in self.case_ids]

    async def fetchrow(self, query, *args):
        return {"status": self.status} if self.case_ids else None


class FakeInv:
    def __init__(self):
        self.calls: list[str] = []

    async def run(self, case_id):
        self.calls.append(case_id)


class FakeDisp:
    def __init__(self, exc=None):
        self.calls: list[tuple] = []
        self.exc = exc

    async def submit(self, case_id, action, amount, idempotency_key,
                     approval_ref=None):
        self.calls.append((case_id, action, idempotency_key))
        if self.exc is not None:
            raise self.exc
        return {"route": "approval_required", "approval_id": "AP-1"}


async def test_delegate_sweep_runs_investigation_then_disposition():
    """滞留案件先 AA-SK-02 调查、后 AA-SK-03 提交 freeze（幂等键 <case>:delegate）"""
    inv, disp = FakeInv(), FakeDisp()
    w = EventWorker(FakeDelegatePool(["CASE-D1"]), FakeAggregation(), SingleFlight(),
                    investigation=inv, disposition=disp)
    await w._delegate_sweep()
    assert inv.calls == ["CASE-D1"]
    assert disp.calls == [("CASE-D1", "freeze", "CASE-D1:delegate")]


async def test_delegate_skips_when_case_already_advanced():
    """锁内复核：扫描到加锁之间案件被人工/审批推进（非 INVESTIGATING）则跳过"""
    inv, disp = FakeInv(), FakeDisp()
    w = EventWorker(FakeDelegatePool(["CASE-D2"], status="PENDING_APPROVAL"),
                    FakeAggregation(), SingleFlight(), investigation=inv, disposition=disp)
    await w._delegate_sweep()
    assert inv.calls == [] and disp.calls == []


async def test_delegate_state_error_swallowed():
    """状态类异常（已被接管/不可提交）单次跳过不抛出"""
    from app.skills.disposition import DispositionStateError
    inv, disp = FakeInv(), FakeDisp(exc=DispositionStateError("案件已决策"))
    w = EventWorker(FakeDelegatePool(["CASE-D3"]), FakeAggregation(), SingleFlight(),
                    investigation=inv, disposition=disp)
    await w._delegate_sweep()              # 不得抛出
    assert inv.calls == ["CASE-D3"]        # 调查已执行，提交被状态类异常吞咽跳过
    assert disp.calls == [("CASE-D3", "freeze", "CASE-D3:delegate")]


async def test_delegate_unknown_error_swallowed_for_next_scan():
    """未知错误不抛出：案件仍滞留 INVESTIGATING，留待下轮扫描重试（幂等安全）"""
    inv, disp = FakeInv(), FakeDisp(exc=RuntimeError("mcp 不可达"))
    w = EventWorker(FakeDelegatePool(["CASE-D4"]), FakeAggregation(), SingleFlight(),
                    investigation=inv, disposition=disp)
    await w._delegate_sweep()              # 不得抛出
    assert inv.calls == ["CASE-D4"]


async def test_delegate_disabled_without_kernels_or_switch(monkeypatch):
    """代码缺省关闭：未注入双内核或 DELEGATE_AFTER=0 时不启用委托"""
    from app.core import event_worker as ew
    w1 = EventWorker(FakePool([]), FakeAggregation(), SingleFlight(),
                     investigation=FakeInv(), disposition=FakeDisp())
    assert w1._delegate_enabled() is False          # DELEGATE_AFTER 缺省 0
    monkeypatch.setattr(ew, "DELEGATE_AFTER", 900)
    w2 = EventWorker(FakePool([]), FakeAggregation(), SingleFlight())
    assert w2._delegate_enabled() is False          # 缺内核
    w3 = EventWorker(FakePool([]), FakeAggregation(), SingleFlight(),
                     investigation=FakeInv(), disposition=FakeDisp())
    assert w3._delegate_enabled() is True           # 双件齐 + 开关 on
