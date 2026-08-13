-- 存储端不变量守护（03 §9.3 DA-INV 的数据库层落地）
-- 设计思路：应用层状态机（web-api core/state_machine.py）是第一道防线，
-- 本文件是第二道防线——任何绕过应用层的写路径（脚本/直连）同样被拒绝。
-- 变更纪律：迁移路径先改 02 §7/OpenAPI 枚举，再同步本文件与应用层状态机。

-- DA-INV-01：状态迁移白名单表（与 app/core/state_machine.py TRANSITIONS 逐条对齐）
CREATE TABLE case_state_transition (
    from_status varchar(20) NOT NULL,
    to_status   varchar(20) NOT NULL,
    PRIMARY KEY (from_status, to_status)
);

INSERT INTO case_state_transition (from_status, to_status) VALUES
  ('REGISTERED',       'AGGREGATING'),
  ('AGGREGATING',      'INVESTIGATING'),
  ('AGGREGATING',      'ARCHIVED'),          -- 低风险误报降噪放行
  ('AGGREGATING',      'DISPOSING'),         -- BA-CAP-05 低风险自动通道（BA-BR-01/SC-01，边界守卫在聚合裁决层）
  ('INVESTIGATING',    'PENDING_APPROVAL'),
  ('INVESTIGATING',    'ARCHIVED'),          -- 人工复核排除欺诈
  ('PENDING_APPROVAL', 'APPROVED'),
  ('PENDING_APPROVAL', 'REJECTED'),
  ('APPROVED',         'DISPOSING'),
  ('DISPOSING',        'DISPOSED'),
  ('REJECTED',         'MANUAL_REVIEW'),     -- 驳回回滚（BA-BR-07）
  ('DISPOSED',         'VERIFIED'),
  ('DISPOSED',         'ROLLBACK'),          -- 核验不一致→反向处置
  ('ROLLBACK',         'MANUAL_REVIEW'),     -- 反向处置完成→升级 P0 转人工
  ('MANUAL_REVIEW',    'PENDING_APPROVAL'),
  ('MANUAL_REVIEW',    'ARCHIVED'),
  ('VERIFIED',         'ARCHIVED');

CREATE OR REPLACE FUNCTION trg_case_transition_guard() RETURNS trigger AS $$
BEGIN
    IF OLD.status IS DISTINCT FROM NEW.status
       AND NOT EXISTS (SELECT 1 FROM case_state_transition
                       WHERE from_status = OLD.status AND to_status = NEW.status) THEN
        RAISE EXCEPTION 'E-BAD-TRANSITION: 非法状态迁移 % -> %（DA-INV-01，02 §7 状态机）',
            OLD.status, NEW.status;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER case_transition_guard
    BEFORE UPDATE OF status ON risk_case
    FOR EACH ROW EXECUTE FUNCTION trg_case_transition_guard();

-- DA-INV-06：kb_document 置 published 必须携带人类操作者会话标记（human:*）
-- 应用层写法：事务内先 SELECT set_config('tg.actor', 'human:xxx', true) 再 UPDATE
CREATE OR REPLACE FUNCTION trg_kb_human_gate() RETURNS trigger AS $$
BEGIN
    IF NEW.status = 'published' AND OLD.status IS DISTINCT FROM 'published'
       AND coalesce(current_setting('tg.actor', true), '') NOT LIKE 'human:%' THEN
        RAISE EXCEPTION 'E-KB-HUMAN-GATE: 知识发布仅限人工确认（DA-INV-06，BA-BR-11）';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER kb_human_gate
    BEFORE UPDATE OF status ON kb_document
    FOR EACH ROW EXECUTE FUNCTION trg_kb_human_gate();

GRANT SELECT ON case_state_transition TO tg_web, tg_app;
