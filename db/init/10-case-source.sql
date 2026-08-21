-- ============================================================================
-- 10-case-source.sql —— 案件来源类型落库（LoopEngine 环设施配套，扩展非重构）
--
-- 背景：测试与活栈共享同一库，web-api 容器内 EventWorker 每 2s 轮询
-- REGISTERED 案件并自动聚合（DA-T-04），与测试的显式状态驱动形成竞态
-- （test_mcp_gate 等偶发 E-BAD-TRANSITION flake）。此前 source_type 仅进
-- 审计 basis 文案未落库，自动环无法区分合成案件。
--
-- 决策：risk_case 增列 source_type（默认 'UNKNOWN' 兼容存量行），自动环
-- （EventWorker 轮询/委托扫描）确定性排除 'TEST' 源——与 KPI 口径「非 TEST
-- 来源双保险」同节奏：合成案件归测试显式驱动，生产环只消费真实来源。
-- 12 态状态机、迁移表、不变量均不受影响（纯数据列，无 CHECK/触发器变更）。
-- ============================================================================

ALTER TABLE risk_case
    ADD COLUMN IF NOT EXISTS source_type varchar(16) NOT NULL DEFAULT 'UNKNOWN';

COMMENT ON COLUMN risk_case.source_type IS
    '案件来源类型（ALERT/DEMO/GENERATED/TEST…，API-W-01 入参落库）；自动环排除 TEST';
