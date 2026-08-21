-- 11-case-governance.sql：案件治理批次（BA-BR-26/27/28，docs/14 v1.7 US-E17~19）
-- 缺口#2 优先级队列 / 缺口#4 STR 叙事生成 / 缺口#5 可治理自动关闭（docs/09 v1.3 赛道对标）
-- 零新表：白名单迁移对同步 + sys_config 治理热配置种子，IF NOT EXISTS/ON CONFLICT 可安全重跑

-- 1) DA-INV-01 第二道防线：ARCHIVED→MANUAL_REVIEW（CaseReopened）同步入白名单，
--    与 app/core/state_machine.py TRANSITIONS 逐条对齐；human_only 守卫在应用层
--    next_state 与端点角色门（api_guards），触发器侧仅要求迁移对合法
INSERT INTO case_state_transition (from_status, to_status)
VALUES ('ARCHIVED', 'MANUAL_REVIEW')   -- BA-BR-28 归档复位通道（SC-27）
ON CONFLICT (from_status, to_status) DO NOTHING;

-- 2) 治理热配置种子（ConfigService US-E1-03：Nacos 优先、sys_config 降级）
--    br-26-aging-hours：队列 aging 超期阈值（BA-BR-26，主管看板口径）
--    br-28-auto-close-max-amount：自动关闭金额上限（BA-BR-28，与 BA-BR-01 同源缺省）
INSERT INTO sys_config (key, value) VALUES
  ('br-26-aging-hours', '24'),
  ('br-28-auto-close-max-amount', '5000')
ON CONFLICT (key) DO NOTHING;
