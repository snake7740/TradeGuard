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

    async def list(self, status: str | None, limit: int) -> list[dict]:
        q = "SELECT * FROM risk_case"
        args: list = []
        if status:
            q += " WHERE status=$1 ORDER BY risk_score DESC LIMIT $2"
            args = [status, limit]
        else:
            q += " ORDER BY created_at DESC LIMIT $1"
            args = [limit]
        rows = await self._pool.fetch(q, *args)
        return [_case_row(r) for r in rows]

    async def get(self, case_id: str) -> dict | None:
        r = await self._pool.fetchrow("SELECT * FROM risk_case WHERE case_id=$1", case_id)
        if not r:
            return None
        return _case_row(r) | {"context_json": r["context_json"], "version": r["version"],
                               "trace_id": r["trace_id"]}

    async def register(self, subject_ref: str, risk_score: int, source_type: str) -> dict:
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
                   VALUES ($1, 'human:operator', 'case.register', $2, $3, $4)""",
                uuid.uuid4().hex, case_id, f"source={source_type},severity={risk_score}", trace_id)
        await self._pub.publish(case_id, "CaseRegistered",
                                {"subject_ref": subject_ref, "risk_score": risk_score}, "human:operator")
        return {"case_id": case_id, "status": "REGISTERED", "trace_id": trace_id}

    async def transition(self, case_id: str, event: CaseEvent, actor: str,
                         expected_version: int, basis: str = "") -> dict:
        """状态迁移写路径模板：状态机校验→乐观锁 UPDATE→审计→事件发布"""
        r = await self._pool.fetchrow("SELECT status, version, trace_id FROM risk_case WHERE case_id=$1", case_id)
        if not r:
            raise LookupError(case_id)
        target = next_state(CaseState(r["status"]), event, actor)  # 非法迁移直接抛 InvalidTransition
        async with self._pool.acquire() as conn, conn.transaction():
            updated = await conn.execute(
                """UPDATE risk_case SET status=$1, version=version+1, updated_at=now()
                   WHERE case_id=$2 AND version=$3""",
                target.value, case_id, expected_version)
            if updated != "UPDATE 1":
                raise OptimisticLockError(f"{case_id} version 冲突（期望 {expected_version}）")
            await conn.execute(
                """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                uuid.uuid4().hex, actor, f"case.transition.{event.value}", case_id,
                basis or f"{r['status']}->{target.value}", r["trace_id"])
        await self._pub.publish(case_id, event.value,
                                {"from": r["status"], "to": target.value}, actor)
        return {"case_id": case_id, "status": target.value, "version": expected_version + 1}

    async def signals(self, case_id: str) -> list[dict]:
        rows = await self._pool.fetch(
            "SELECT * FROM risk_signal WHERE case_id=$1 ORDER BY ts", case_id)
        return [dict(x) for x in rows]

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
        rows = await self._pool.fetch(
            "SELECT * FROM approval_record WHERE decision=$1 ORDER BY created_at", decision)
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
