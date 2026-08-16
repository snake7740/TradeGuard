"""仓储层集成测试（乐观锁 DA-T-03 + 双守护 DA-INV-01 + 审计链 BA-BR-09）

直连运行中的 postgres（tg_web 账号）；每个用例自建案件、自清理无依赖（FIRST）。
"""
import pytest

from app.core.state_machine import CaseEvent
from app.repositories import OptimisticLockError


async def _new_case(repo):
    return await repo.register(subject_ref="acct-test-pytest", risk_score=55, source_type="TEST")


async def test_register_creates_case_with_audit(case_repo):
    repo, pub = case_repo
    r = await _new_case(repo)
    assert r["status"] == "REGISTERED"
    got = await repo.get(r["case_id"])
    assert got["version"] == 0
    trail = await repo.audit_trail(r["case_id"])
    assert any(a["action"] == "case.register" for a in trail)
    assert pub.published[-1]["event"] == "CaseRegistered"
    # A4：事件透传案件 trace_id（可观测侧同案串联回放）
    assert pub.published[-1]["trace_id"] == r["trace_id"]


async def test_transition_events_carry_case_trace_id(case_repo):
    """A4：状态迁移事件同样携带案件 trace_id（_envelope 缺省回落 uuid 不再适用）"""
    repo, pub = case_repo
    r = await _new_case(repo)
    out = await repo.transition(r["case_id"], CaseEvent.AGGREGATION_STARTED, "agent:AA-AG-02", 0)
    msg = pub.published[-1]
    assert msg["event"] == "AggregationStarted" and msg["trace_id"] == r["trace_id"]
    assert out["version"] == 1


async def test_transition_happy_path_version_increment(case_repo):
    repo, pub = case_repo
    r = await _new_case(repo)
    out = await repo.transition(r["case_id"], CaseEvent.AGGREGATION_STARTED, "agent:AA-AG-02", 0)
    assert out["status"] == "AGGREGATING" and out["version"] == 1
    out = await repo.transition(r["case_id"], CaseEvent.SIGNALS_AGGREGATED, "agent:AA-AG-02", 1)
    assert out["status"] == "INVESTIGATING" and out["version"] == 2


async def test_transition_illegal_rejected_and_state_unchanged(case_repo):
    """应用层守护第一道：REGISTERED 直接审批 → 拒绝且无迁移留痕

    R-37 复审加固：尾随"状态相等"断言在 compose EventWorker 同库抢跑
    REGISTERED 案件时会被并发推进干扰（拒绝断言本身恒成立——自动链路
    到不了 PENDING_APPROVAL，APPROVAL_APPROVED 从任何中间态均非法）。
    改断言"审计链无 ApprovalApproved 迁移痕"：被拒迁移在 next_state 处
    先抛、不进事务，原子无部分写入的语义不受并发推进影响，竞态免疫。"""
    repo, _ = case_repo
    r = await _new_case(repo)
    from app.core.state_machine import InvalidTransition
    with pytest.raises(InvalidTransition) as ei:
        await repo.transition(r["case_id"], CaseEvent.APPROVAL_APPROVED, "human:x", 0)
    assert ei.value.code == "E-BAD-TRANSITION"
    trail = await repo.audit_trail(r["case_id"])
    assert not any(a["action"] == "case.transition.ApprovalApproved" for a in trail)


async def test_optimistic_lock_conflict(case_repo):
    """DA-T-03：过期 version 提交必须 E-VERSION-CONFLICT"""
    repo, _ = case_repo
    r = await _new_case(repo)
    await repo.transition(r["case_id"], CaseEvent.AGGREGATION_STARTED, "agent:AA-AG-02", 0)
    with pytest.raises(OptimisticLockError):
        await repo.transition(r["case_id"], CaseEvent.SIGNALS_AGGREGATED, "agent:AA-AG-02", 0)  # 过期版本


async def test_human_only_enforced_in_repository(case_repo):
    """human_only 守卫贯穿到仓储层（agent 不得批准）"""
    repo, _ = case_repo
    r = await _new_case(repo)
    await repo.transition(r["case_id"], CaseEvent.AGGREGATION_STARTED, "agent:a", 0)
    await repo.transition(r["case_id"], CaseEvent.INVESTIGATION_REQUESTED, "agent:a", 1)
    await repo.transition(r["case_id"], CaseEvent.INVESTIGATION_COMPLETED, "agent:a", 2)
    from app.core.state_machine import InvalidTransition
    with pytest.raises(InvalidTransition) as ei:
        await repo.transition(r["case_id"], CaseEvent.APPROVAL_APPROVED, "agent:AA-AG-01", 3)
    assert ei.value.code == "E-HUMAN-ONLY"


async def test_db_trigger_second_guardrail(pool, case_repo):
    """存储层守护第二道（07-case-actor-gate.sql，工作流 E）：绕过应用层直改 status
    必须被触发器拒绝。actor_gate 先于 transition_guard 触发（按触发器名排序），
    故未声明 tg.actor 先撞 E-ACTOR-REQUIRED；声明 agent actor 后再撞白名单
    E-BAD-TRANSITION——双道防线语义都保留。"""
    repo, _ = case_repo
    r = await _new_case(repo)
    cid = r["case_id"]
    await repo.transition(cid, CaseEvent.AGGREGATION_STARTED, "agent:a", 0)
    await repo.transition(cid, CaseEvent.INVESTIGATION_REQUESTED, "agent:a", 1)
    await repo.transition(cid, CaseEvent.INVESTIGATION_COMPLETED, "agent:a", 2)
    # 第一道（actor 门控）：未声明 tg.actor → E-ACTOR-REQUIRED
    with pytest.raises(Exception, match="E-ACTOR-REQUIRED"):
        await pool.execute(
            "UPDATE risk_case SET status='VERIFIED' WHERE case_id=$1", cid)
    # 第二道（白名单）：声明 agent actor 后直改 → E-BAD-TRANSITION
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('tg.actor', 'agent:intruder', true)")
        with pytest.raises(Exception, match="E-BAD-TRANSITION"):
            await conn.execute(
                "UPDATE risk_case SET status='VERIFIED' WHERE case_id=$1", cid)
    assert (await repo.get(cid))["status"] == "PENDING_APPROVAL"


async def test_db_actor_gate_blocks_human_pair_for_agent(pool, case_repo):
    """工作流 E：五对人类守卫对的存储层拒绝——agent actor 直改
    PENDING_APPROVAL→APPROVED 必须被 trg_case_actor_gate 拒绝（E-HUMAN-ONLY-DB）。"""
    repo, _ = case_repo
    r = await _new_case(repo)
    cid = r["case_id"]
    await repo.transition(cid, CaseEvent.AGGREGATION_STARTED, "agent:a", 0)
    await repo.transition(cid, CaseEvent.INVESTIGATION_REQUESTED, "agent:a", 1)
    await repo.transition(cid, CaseEvent.INVESTIGATION_COMPLETED, "agent:a", 2)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('tg.actor', 'agent:intruder', true)")
        with pytest.raises(Exception, match="E-HUMAN-ONLY-DB"):
            await conn.execute(
                "UPDATE risk_case SET status='APPROVED' WHERE case_id=$1", cid)
    assert (await repo.get(cid))["status"] == "PENDING_APPROVAL"


async def test_audit_trail_records_every_transition(case_repo):
    """BA-BR-09：每次迁移同事务留痕，回放顺序可复现（SC-08 基础）"""
    repo, _ = case_repo
    r = await _new_case(repo)
    cid = r["case_id"]
    await repo.transition(cid, CaseEvent.AGGREGATION_STARTED, "agent:a", 0)
    await repo.transition(cid, CaseEvent.NOISE_DISMISSED, "agent:a", 1)
    trail = await repo.audit_trail(cid)
    actions = [a["action"] for a in trail]
    assert actions == ["case.register", "case.transition.AggregationStarted",
                       "case.transition.NoiseDismissed"]
    assert (await repo.get(cid))["status"] == "ARCHIVED"
