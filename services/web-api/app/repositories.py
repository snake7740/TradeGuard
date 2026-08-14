"""仓储层（Repository 模式，03 §9.1 聚合边界即代码模块边界）

设计要点：
- 一个聚合根一个仓储：CaseRepository / ApprovalRepository / KbRepository；
- CaseRepository.transition：状态机（DA-INV-01）+ 乐观锁（DA-T-03 version）+
  审计留痕（BA-BR-09）+ 领域事件发布（03 §9.2）四步同事务语义——
  这是 Sprint 1 全部写路径（E3/E4/E5）的模板；
- 只增表（risk_signal/case_evidence/audit_log）仅暴露 insert/select，无 update/delete 方法，
  与 02-roles.sql 权限层（DA-INV-05）双重守护。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import asyncpg

from .core.events import EventPublisher
from .core.state_machine import CaseEvent, CaseState, InvalidTransition, next_state


class OptimisticLockError(Exception):
    """DA-T-03 version 冲突：并发迁移覆盖防护（US-E5-06）"""

    code = "E-VERSION-CONFLICT"


class CaseRepository:
    """RiskCase 聚合根仓储（DA-T-03）"""

    def __init__(self, pool: asyncpg.Pool, publisher: EventPublisher) -> None:
        self._pool = pool
        self._pub = publisher

    async def list(self, status: str | None = None, risk_min: int | None = None,
                   page: int = 1, size: int = 20) -> tuple[int, list[dict]]:
        """API-W-02 分页列表：返回 (total, items)，统一 created_at 倒序"""
        where: list[str] = []
        args: list = []
        if status:
            args.append(status)
            where.append(f"status=${len(args)}")
        if risk_min is not None:
            args.append(risk_min)
            where.append(f"risk_score>=${len(args)}")
        cond = f" WHERE {' AND '.join(where)}" if where else ""
        total = await self._pool.fetchval(f"SELECT count(*) FROM risk_case{cond}", *args)
        rows = await self._pool.fetch(
            f"SELECT * FROM risk_case{cond} ORDER BY created_at DESC "
            f"LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}",
            *args, size, (page - 1) * size)
        return total, [_case_row(r) for r in rows]

    async def get(self, case_id: str) -> dict | None:
        r = await self._pool.fetchrow("SELECT * FROM risk_case WHERE case_id=$1", case_id)
        if not r:
            return None
        return _case_row(r) | {"context_json": json.loads(r["context_json"] or "{}"),
                               "version": r["version"],
                               "trace_id": r["trace_id"]}

    async def register(self, subject_ref: str, risk_score: int, source_type: str,
                       actor: str = "human:operator") -> dict:
        """立案（API-W-01）：INSERT risk_case + audit_log 同事务，并发布 CaseRegistered"""
        case_id = f"CASE-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6]}"
        trace_id = uuid.uuid4().hex
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """INSERT INTO risk_case (case_id, subject_ref, status, risk_score, trace_id)
                   VALUES ($1, $2, 'REGISTERED', $3, $4)""",
                case_id, subject_ref, risk_score, trace_id)
            await conn.execute(
                """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
                   VALUES ($1, $2, 'case.register', $3, $4, $5)""",
                uuid.uuid4().hex, actor, case_id,
                f"source={source_type},severity={risk_score}", trace_id)
        await self._pub.publish(case_id, "CaseRegistered",
                                {"subject_ref": subject_ref, "risk_score": risk_score}, actor,
                                trace_id=trace_id)
        return {"case_id": case_id, "status": "REGISTERED", "trace_id": trace_id}

    async def transition(self, case_id: str, event: CaseEvent, actor: str,
                         expected_version: int, basis: str = "") -> dict:
        """状态迁移写路径模板：状态机校验→乐观锁 UPDATE→审计→事件发布

        E（闭环修复）：事务内先声明 tg.actor（is_local=true，同连接同事务），
        供存储层 trg_case_actor_gate 第二道防线校验人类门控（02 §7，kb_human_gate 同款）。
        """
        r = await self._pool.fetchrow("SELECT status, version, trace_id FROM risk_case WHERE case_id=$1", case_id)
        if not r:
            raise LookupError(case_id)
        target = next_state(CaseState(r["status"]), event, actor)  # 非法迁移直接抛 InvalidTransition
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('tg.actor', $1, true)", actor)
            updated = await conn.execute(
                """UPDATE risk_case SET status=$1, version=version+1, updated_at=now()
                   WHERE case_id=$2 AND version=$3""",
                target.value, case_id, expected_version)
            if updated != "UPDATE 1":
                raise OptimisticLockError("案件数据已被其他操作更新，请刷新页面后重试")
            await conn.execute(
                """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                uuid.uuid4().hex, actor, f"case.transition.{event.value}", case_id,
                basis or f"{r['status']}->{target.value}", r["trace_id"])
        await self._pub.publish(case_id, event.value,
                                {"from": r["status"], "to": target.value}, actor,
                                trace_id=r["trace_id"])
        return {"case_id": case_id, "status": target.value, "version": expected_version + 1}

    async def signals(self, case_id: str) -> list[dict]:
        rows = await self._pool.fetch(
            "SELECT * FROM risk_signal WHERE case_id=$1 ORDER BY ts", case_id)
        out = [dict(x) for x in rows]
        for s in out:  # asyncpg 对 jsonb 返回 JSON 文本，仓储层统一反序列化
            if isinstance(s.get("velocity_json"), str):
                s["velocity_json"] = json.loads(s["velocity_json"])
        return out

    async def evidence(self, case_id: str) -> list[dict]:
        rows = await self._pool.fetch(
            "SELECT * FROM case_evidence WHERE case_id=$1 ORDER BY ts", case_id)
        return [dict(x) for x in rows]

    async def audit_trail(self, case_id: str) -> list[dict]:
        rows = await self._pool.fetch(
            "SELECT * FROM audit_log WHERE target=$1 ORDER BY ts", case_id)
        return [dict(x) for x in rows]


class ApprovalRepository:
    """ApprovalRecord 仓储（DA-T-07，AG-04 创建、人类回填）"""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list(self, decision: str) -> list[dict]:
        # UX 加固：join 案件主表补风险评分/涉事主体（审批人决策依据），
        # 保持 created_at ASC（FIFO：等待最久的工单置顶，与超时升级语义一致）
        rows = await self._pool.fetch(
            """SELECT a.*, c.risk_score, c.subject_ref
               FROM approval_record a LEFT JOIN risk_case c ON c.case_id = a.case_id
               WHERE a.decision=$1 ORDER BY a.created_at""", decision)
        return [dict(r) | {"created_at": r["created_at"].isoformat()} for r in rows]


class KbRepository:
    """KbDocument 仓储（DA-T-09，DA-INV-06 发布人工门控）"""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def applications(self, status: str = "pending") -> list[dict]:
        rows = await self._pool.fetch(
            "SELECT * FROM kb_document WHERE status=$1 ORDER BY ts", status)
        return [dict(r) | {"ts": r["ts"].isoformat()} for r in rows]


def _case_row(r: asyncpg.Record) -> dict:
    return {
        "case_id": r["case_id"], "subject_ref": r["subject_ref"], "status": r["status"],
        "risk_score": r["risk_score"], "current_agent": r["current_agent"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
    }
