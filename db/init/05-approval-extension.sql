-- =============================================================
-- 05-approval-extension.sql · Sprint 3-4（E5 处置审批回滚）DA-T-07 扩展
-- 幂等可重跑（IF NOT EXISTS）；新库与运行库同一脚本收敛。
-- =============================================================

-- 审批工单携带处置请求上下文（E-DISP-AUTH 建单时由 AA-AG-04 写入，
-- 批准后 AA-SK-03 据此执行，SC-02 全链闭环）
ALTER TABLE approval_record ADD COLUMN IF NOT EXISTS requested_action varchar(10)
    CHECK (requested_action IS NULL OR requested_action IN ('block','freeze','reduce','release'));
ALTER TABLE approval_record ADD COLUMN IF NOT EXISTS requested_amount numeric(14,2);

-- BA-BR-13 审批时效升级标记（SC-09：超时扫描器写入，门户按此标红，API-W-08 返回）
ALTER TABLE approval_record ADD COLUMN IF NOT EXISTS escalated_at timestamptz;