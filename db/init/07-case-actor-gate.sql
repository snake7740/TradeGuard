-- =============================================================
-- 07-case-actor-gate.sql · 闭环修复轮（v1.4.4，工作流 E）
-- risk_case 状态变更人类门控（02 §7 人机边界的存储层第二道防线）。
-- 依赖：应用层 repositories.transition() 已在事务内 set_config('tg.actor',...)
-- （knowledge.py kb_human_gate 同款模式）。本文件须在该代码就位后启用。
-- 幂等可重跑；新卷经 initdb 自动执行，运行卷手工收敛。
-- =============================================================

-- 检查顺序：actor 非空（E-ACTOR-REQUIRED）→ 白名单（E-BAD-TRANSITION，
-- 04 文件的 case_transition_guard 先挂、独立触发器）→ 五对人类守卫（E-HUMAN-ONLY-DB）。
-- 五对均为 human_only 迁移且 from 状态出口无 agent 歧义：
--   PENDING_APPROVAL→APPROVED/REJECTED（审批决策）、
--   MANUAL_REVIEW→PENDING_APPROVAL/ARCHIVED（人工复核）、
--   INVESTIGATING→ARCHIVED（调查中复核排除，唯一出口 REVIEW_DISMISSED）。
CREATE OR REPLACE FUNCTION trg_case_actor_gate() RETURNS trigger AS $$
DECLARE
    actor text := coalesce(current_setting('tg.actor', true), '');
BEGIN
    IF OLD.status IS DISTINCT FROM NEW.status THEN
        IF actor = '' THEN
            RAISE EXCEPTION 'E-ACTOR-REQUIRED: risk_case 状态变更必须声明 tg.actor（02 §7 留痕纪律）';
        END IF;
        IF ((OLD.status = 'PENDING_APPROVAL' AND NEW.status IN ('APPROVED', 'REJECTED'))
            OR (OLD.status = 'MANUAL_REVIEW' AND NEW.status IN ('PENDING_APPROVAL', 'ARCHIVED'))
            OR (OLD.status = 'INVESTIGATING' AND NEW.status = 'ARCHIVED'))
           AND actor NOT LIKE 'human:%' THEN
            RAISE EXCEPTION 'E-HUMAN-ONLY-DB: % -> % 仅限人工触发（02 §7 human_only 迁移，BA-BR-02/07）',
                OLD.status, NEW.status;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS case_actor_gate ON risk_case;
CREATE TRIGGER case_actor_gate
    BEFORE UPDATE OF status ON risk_case
    FOR EACH ROW EXECUTE FUNCTION trg_case_actor_gate();
