"""LoopEngine：有界确定性环的统一运行时契约（loop 工程落地层）

定位：把项目内三类手写环（EventWorker 处理环、escalation/metabolism 巡检环、
plan-reflect 认知环）共有的运行时设施收敛为一层——重试策略、失败归宿（DLQ）、
终止条件留痕。环本身仍由各业务模块实现（状态机/人工门纪律不下放），本层只
提供「失败怎么办」的默认答案：记录 → 累计 → 达上限驻车 → 人工复位放行。

纪律（与 docs/14 环边界同源）：
  - 驻车（parked）只是停止自动重试，不改变案件状态（状态机仍是迁移权威）；
  - 复位放行必须人工具名（resolved_by），与人工门控语义一致（BA-BR-11 同源线）；
  - DLQ 记录只增不改语义：复位不删行，仅清零 attempts 并留复位人与时间。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LoopPolicy:
    """环策略（有界：轮次/退避/死信上限全部显式，无自由循环）"""

    max_retries: int = 3          # 单轮内重试次数（与 TG_EVENT_WORKER_RETRIES 同源）
    backoff_base: float = 5.0     # 线性退避基数（秒）
    dead_letter_cap: int = 9      # 累计失败上限（3 轮×3 次）：达线驻车转人工

    def next_delay(self, attempt: int) -> float:
        return self.backoff_base * (attempt + 1)


DEFAULT_POLICY = LoopPolicy()


async def deadletter_record(pool, case_id: str, stage: str,
                            error: BaseException, attempts: int,
                            policy: LoopPolicy = DEFAULT_POLICY) -> dict[str, Any]:
    """失败归宿记录（upsert 累计）：返回 {attempts, parked, parked_now}。

    parked_now=True 表示本次累计恰好达到上限 newly 驻车（调用方据此发告警事件，
    幂等：重复调用不再置 True）。"""
    row = await pool.fetchrow(
        """INSERT INTO processing_deadletter
               (case_id, stage, error_class, error_msg, attempts, last_failed_at)
           VALUES ($1, $2, $3, $4, $5, now())
           ON CONFLICT (case_id) DO UPDATE SET
               attempts = processing_deadletter.attempts + EXCLUDED.attempts,
               error_class = EXCLUDED.error_class,
               error_msg = EXCLUDED.error_msg,
               stage = EXCLUDED.stage,
               last_failed_at = now()
           RETURNING attempts, parked""",
        case_id, stage, type(error).__name__[:80], str(error)[:300], attempts)
    cum, parked = int(row["attempts"]), bool(row["parked"])
    parked_now = False
    if not parked and cum >= policy.dead_letter_cap:
        await pool.execute(
            "UPDATE processing_deadletter SET parked=true WHERE case_id=$1",
            case_id)
        parked_now = True
    return {"attempts": cum, "parked": parked or parked_now,
            "parked_now": parked_now}


async def deadletter_list(pool, parked_only: bool = True) -> list[dict[str, Any]]:
    """DLQ 清单（人工可见性入口，/api/deadletter 数据源）"""
    where = "WHERE parked" if parked_only else ""
    rows = await pool.fetch(
        f"""SELECT case_id, stage, error_class, error_msg, attempts, parked,
                   first_failed_at, last_failed_at, resolved_by, resolved_at
            FROM processing_deadletter {where}
            ORDER BY last_failed_at DESC LIMIT 100""")
    return [dict(r) for r in rows]


async def deadletter_retry(pool, case_id: str, actor: str) -> dict[str, Any]:
    """人工复位放行（只增不改：保留历史，清零 attempts 解除驻车）。

    返回 {ok, attempts}；无记录视为无需复位（ok=False）。复位后案件重新进入
    worker 轮询候选（sweep 仅排除 parked 行）。"""
    res = await pool.execute(
        """UPDATE processing_deadletter
           SET parked=false, attempts=0, resolved_by=$2, resolved_at=now()
           WHERE case_id=$1""",
        case_id, actor[:40])
    return {"ok": res == "UPDATE 1", "case_id": case_id}
