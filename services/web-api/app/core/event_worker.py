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

INVESTIGATING 超时自动委托（BUG-02/R-46 方案甲）：
- 纯浏览器/人工流下，EventWorker 只承接到 INVESTIGATING，处置提交属 Agent 职责
  （02 §3.3），无事件驱动调度承接——审批队列永远空转。本模块补第二跳：滞留
  INVESTIGATING 超过 `TG_DELEGATE_INVESTIGATING_SECONDS` 的案件，由 worker 代
  Agent 依次调 AA-SK-02（investigation.run）与 AA-SK-03（disposition.submit，
  action=freeze），内核内部以 agent:AA-AG-03/04 推进状态（actor 门合规）。
- 滞留信号 = `updated_at`（repositories.transition 每次迁移刷新）。submit 幂等键
  `<case_id>:delegate`，扫描级重试安全（idempotent_hit / 证据 claim 去重）。
- 开关 `TG_DELEGATE_INVESTIGATING_SECONDS` 代码缺省 **0=OFF**（测试直插
  INVESTIGATING 案件不受影响）；compose 显式置 900（15 分钟 > 全量 pytest 8 分钟，
  杜绝同库测试竞态）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid

from .loop_engine import DEFAULT_POLICY, deadletter_record

logger = logging.getLogger("tradeguard.event_worker")

ACTOR_WORKER_AUDIT = "system:event-worker"   # DLQ 驻车留痕 actor（环设施层）

POLL_INTERVAL = float(os.getenv("TG_EVENT_WORKER_INTERVAL", "2"))   # 轮询周期（秒）
POLL_WINDOW_MIN = int(os.getenv("TG_EVENT_WORKER_WINDOW", "10"))   # 增量窗口（分钟）
MAX_RETRIES = int(os.getenv("TG_EVENT_WORKER_RETRIES", "3"))       # 阶段2 R-41：有限重试
RETRY_BASE_DELAY = float(os.getenv("TG_EVENT_WORKER_RETRY_DELAY", "5"))  # 线性退避基数（秒）
DELEGATE_AFTER = int(os.getenv("TG_DELEGATE_INVESTIGATING_SECONDS", "0"))  # R-46：0=OFF
DELEGATE_SCAN_INTERVAL = float(os.getenv("TG_DELEGATE_SCAN_INTERVAL", "30"))  # 委托扫描周期（秒）
DELEGATE_BATCH = int(os.getenv("TG_DELEGATE_BATCH", "10"))         # 单轮委托上限


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

    R-46 方案甲：investigation/disposition 内核注入时，另以
    DELEGATE_SCAN_INTERVAL 周期扫描滞留 INVESTIGATING 超 DELEGATE_AFTER 秒的
    案件，代 Agent 完成调查与处置提交（详见模块 docstring）。
    """

    def __init__(self, pool, aggregation, flight: SingleFlight,
                 investigation=None, disposition=None, pub=None) -> None:
        self._pool = pool
        self._agg = aggregation
        self._flight = flight
        self._inv = investigation
        self._disp = disposition
        self._pub = pub  # DLQ 驻车告警事件通道（LoopEngine 可见性，可缺省）
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())
            logger.info("EventWorker 已启动（轮询 %ss，窗口 %s 分钟%s）",
                        POLL_INTERVAL, POLL_WINDOW_MIN,
                        f"，INVESTIGATING 委托 {DELEGATE_AFTER}s/{DELEGATE_SCAN_INTERVAL}s"
                        if self._delegate_enabled() else "")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            logger.info("EventWorker 已停止")

    def _delegate_enabled(self) -> bool:
        return (DELEGATE_AFTER > 0 and self._inv is not None and self._disp is not None)

    async def _loop(self) -> None:
        await self._sweep(window_minutes=None)          # 启动全扫：补停机期积压
        last_delegate = 0.0
        while True:
            await asyncio.sleep(POLL_INTERVAL)
            await self._sweep(window_minutes=POLL_WINDOW_MIN)
            now = asyncio.get_running_loop().time()
            if self._delegate_enabled() and now - last_delegate >= DELEGATE_SCAN_INTERVAL:
                last_delegate = now
                await self._delegate_sweep()

    async def _park_notice(self, case_id: str, error: Exception,
                           attempts: int) -> None:
        """驻车告知（审计 + 告警事件，best-effort 不阻断 worker 环）"""
        try:
            await self._pool.execute(
                """INSERT INTO audit_log (log_id, actor, action, target, basis)
                   VALUES ($1, $2, 'worker.deadletter', $3, $4)""",
                uuid.uuid4().hex, ACTOR_WORKER_AUDIT, case_id,
                f"聚合环累计失败 {attempts} 次达 DLQ 上限，驻车待人工复位"
                f"（{type(error).__name__}）"[:300])
            if self._pub is not None:
                await self._pub.publish(
                    case_id, "E-WORKER-DLQ",
                    {"case_id": case_id, "stage": "aggregation",
                     "attempts": attempts, "error": type(error).__name__},
                    ACTOR_WORKER_AUDIT)
        except Exception:  # noqa: BLE001 —— 告知失败不影响驻车事实
            logger.exception("DLQ 驻车告知失败：%s", case_id)

    async def _delegate_sweep(self) -> None:
        try:
            rows = await self._pool.fetch(
                "SELECT case_id FROM risk_case "
                "WHERE status='INVESTIGATING' "
                "AND source_type <> 'TEST' "
                "AND updated_at < now() - make_interval(secs=>$1) "
                "ORDER BY updated_at LIMIT $2", DELEGATE_AFTER, DELEGATE_BATCH)
        except Exception:  # noqa: BLE001 —— DB 抖动不断 worker 循环
            logger.exception("EventWorker 委托扫描查询失败，等待下轮")
            return
        for r in rows:
            await self._delegate_one(r["case_id"])

    async def _delegate_one(self, case_id: str) -> None:
        from ..skills.disposition import DispositionStateError
        from ..skills.investigation import InvestigationStateError

        lk = self._flight.lock(case_id)
        if lk.locked():
            return  # 手动端点或上一轮正在处理该案件，跳过
        async with lk:
            try:
                # 锁内复核：扫描到加锁之间案件可能已被人工/审批推进
                row = await self._pool.fetchrow(
                    "SELECT status, risk_score FROM risk_case WHERE case_id=$1", case_id)
                if row is None or row["status"] != "INVESTIGATING":
                    return
                inv_out = await self._inv.run(case_id) or {}  # AA-SK-02（agent:AA-AG-03）
                # R-49：AG-03 结论（影响面/手法佐证）→ AG-04 动作动态协商，
                # 替换硬编码 freeze；LLM 不可用降级确定性档位
                from ..skills import planner as planner_mod
                action = await planner_mod.dispatch_action(
                    risk_score=row["risk_score"],
                    impact_accounts=(inv_out.get("impact") or {}).get("accounts", 0),
                    citations=len(
                        (inv_out.get("hypothesis") or {}).get("citations") or []),
                )
                gate = await self._disp.submit(         # AA-SK-03（agent:AA-AG-04）
                    case_id, action, None, f"{case_id}:delegate")
                logger.info("EventWorker 委托 %s 完成：route=%s action=%s（R-49 动态分派）",
                            case_id, gate.get("route"), action)
            except (InvestigationStateError, DispositionStateError, LookupError):
                logger.info("EventWorker 委托跳过 %s（状态已被接管或不可提交）", case_id)
            except Exception:  # noqa: BLE001 —— 失败留待下轮扫描（幂等键/证据去重保证安全）
                logger.exception("EventWorker 委托 %s 失败，留待下轮扫描", case_id)

    async def _sweep(self, window_minutes: int | None) -> None:
        # LoopEngine DLQ：驻车案件（重试累计达上限）排除在轮询候选外，
        # 不再无限重试；人工经 /api/deadletter/{case_id}/retry 复位后恢复候选。
        parked_excl = (" AND NOT EXISTS (SELECT 1 FROM processing_deadletter d"
                       " WHERE d.case_id=risk_case.case_id AND d.parked)")
        # TEST 源排除（10-case-source.sql）：合成案件归测试显式驱动，
        # 生产自动环不消费，消除共享库下轮询与测试迁移的竞态。
        test_excl = " AND source_type <> 'TEST'"
        try:
            if window_minutes is None:
                rows = await self._pool.fetch(
                    "SELECT case_id FROM risk_case WHERE status='REGISTERED'"
                    f"{test_excl}{parked_excl} ORDER BY created_at")
            else:
                rows = await self._pool.fetch(
                    "SELECT case_id FROM risk_case WHERE status='REGISTERED' "
                    "AND created_at >= now() - make_interval(mins=>$1)"
                    f"{test_excl}{parked_excl} ORDER BY created_at", window_minutes)
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
                        # LoopEngine DLQ 默认策略：重试耗尽不再只留日志——
                        # 累计失败入 processing_deadletter，达上限（default 9）
                        # 驻车停扫 + 审计留痕 + E-WORKER-DLQ 告警事件；人工经
                        # /api/deadletter/{case_id}/retry 复位后恢复候选。
                        dlq = await deadletter_record(
                            self._pool, case_id, "aggregation", e, MAX_RETRIES)
                        logger.exception(
                            "EventWorker %s 重试耗尽（%s 次），DLQ 累计 %s/%s%s",
                            case_id, MAX_RETRIES, dlq["attempts"],
                            DEFAULT_POLICY.dead_letter_cap,
                            "，已驻车转人工（/api/deadletter）" if dlq["parked"] else "")
                        if dlq["parked_now"]:
                            await self._park_notice(case_id, e, dlq["attempts"])


def worker_enabled() -> bool:
    """TG_EVENT_WORKER 开关：代码缺省 OFF，compose 显式 on（见模块 docstring）"""
    return os.getenv("TG_EVENT_WORKER", "").strip().lower() in ("1", "on", "true", "yes")
