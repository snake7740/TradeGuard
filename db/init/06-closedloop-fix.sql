-- =============================================================
-- 06-closedloop-fix.sql · 闭环修复轮（v1.4.4）
-- 幂等可重跑；新卷经 initdb 自动执行，运行卷经 docker exec psql -f 手工收敛。
-- 内容：状态白名单补对（B1）+ sys_config 活键补播（D）+ 2027 分区（BA-BR-12）。
-- 注：risk_case 人类门控触发器在 07-case-actor-gate.sql（需应用层 set_config
-- 配套后才可启用，故独立成文，避免中间态拦截）。
-- =============================================================

-- DA-INV-01 白名单补对：DISPOSING 处置失败转人工（DispositionFailed，02 §7）
INSERT INTO case_state_transition (from_status, to_status) VALUES
  ('DISPOSING', 'MANUAL_REVIEW')          -- 处置重试耗尽/门控拒绝 → 转人工（BA-BR-01 失败兜底）
ON CONFLICT DO NOTHING;

-- BA-BR 阈值活键补播（消费方：aggregation/disposition/verification/main 扫描循环，SC-06）
INSERT INTO sys_config (key, value) VALUES
  ('br-05-window-days', '7'),              -- BA-BR-05 高频异常观察窗（天）
  ('br-05-case-count', '3'),               -- BA-BR-05 窗口内立案次数阈值
  ('br-08-verification-timeout-min', '10'),-- BA-BR-08 核验时限（分钟，scan_verification_overdue）
  ('br-14-velocity-24h-count', '50'),      -- BA-BR-14 24 小时频次阈值
  ('br-14-velocity-bonus', '30')           -- BA-BR-14 频次命中加分
ON CONFLICT DO NOTHING;

-- BA-BR-12 归档语义补齐：2027 年月度分区（transaction_default 已兜底，此处显式化）
DO $$
DECLARE
    m int;
    tbl text;
BEGIN
    FOR m IN 1..12 LOOP
        tbl := format('transaction_2027_%s', lpad(m::text, 2, '0'));
        IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = tbl) THEN
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF transaction FOR VALUES FROM (%L) TO (%L)',
                tbl,
                format('2027-%s-01', lpad(m::text, 2, '0')),
                CASE WHEN m = 12 THEN '2028-01-01'
                     ELSE format('2027-%s-01', lpad((m + 1)::text, 2, '0')) END);
        END IF;
    END LOOP;
END $$;
