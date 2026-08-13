-- 权限矩阵账号（03 §6 读写矩阵的数据库层落地，DA-INV-05）
-- 角色：tg_web（web-api 人类写路径） / tg_app（MCP/Agent/数据发生器 应用账号）

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'tg_web') THEN CREATE ROLE tg_web LOGIN PASSWORD 'tg_web_dev'; END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'tg_app') THEN CREATE ROLE tg_app LOGIN PASSWORD 'tg_app_dev'; END IF;
END $$;

GRANT USAGE ON SCHEMA public TO tg_web, tg_app;

-- 只读基线：全体业务表对两个应用角色可读（审计回放/工作台/Agent 读）
GRANT SELECT ON account, transaction, risk_case, risk_signal, case_evidence,
                disposition_record, approval_record, audit_log, kb_document,
                kb_embedding, sys_config, agent_memory TO tg_web, tg_app;

-- tg_app（MCP/Agent 侧）写权限：按 03 §6 矩阵
GRANT INSERT, UPDATE ON risk_case TO tg_app;                    -- AG-01 主控读写
GRANT INSERT ON risk_signal, transaction TO tg_app;             -- AG-02 写信号；数据发生器写流水
GRANT INSERT, UPDATE ON account TO tg_app;                      -- 数据发生器（SIM）为 account 唯一写入方（含团伙打标）
GRANT INSERT ON case_evidence TO tg_app;                        -- AG-03 写证据
GRANT INSERT, UPDATE ON disposition_record TO tg_app;           -- AG-04 写处置
GRANT INSERT ON approval_record TO tg_app;                      -- AG-04 创建工单（decision=pending）
GRANT INSERT ON audit_log, agent_memory TO tg_app;
GRANT INSERT ON kb_document TO tg_app;                          -- AG-05 提交入库申请
GRANT INSERT ON kb_embedding TO tg_app;                         -- 入库流水线

-- tg_web（人类写路径）写权限：审批回填、复核、知识发布、立案演示
GRANT UPDATE ON approval_record, risk_case, account TO tg_web;  -- 审批回填/状态/名单
GRANT INSERT ON audit_log, risk_case, kb_embedding TO tg_web;
GRANT UPDATE ON kb_document TO tg_web;                          -- 人工发布/驳回（DA-INV-06 仅人工）
GRANT UPDATE ON sys_config TO tg_web;

-- 只增表硬约束：任何应用角色禁 UPDATE/DELETE（🔒 DA-T-04/05/08）
REVOKE UPDATE, DELETE ON risk_signal, case_evidence, audit_log FROM tg_web, tg_app;
-- audit_log 对 tg_web 只保留 INSERT（上面 GRANT INSERT 未被 REVOKE 影响）
