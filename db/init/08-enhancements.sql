-- 08-enhancements.sql —— docs/14 增强路线图（US-E8~E12）数据层扩展
-- 纪律：只增不改——既有表仅加列/放宽 CHECK，既有 6 不变量不动；新不变量 DA-INV-07~09 只增。

-- ---------- DA-T-14 account_baseline（A1 自适应基线，EWMA/分位/小时直方图） ----------
CREATE TABLE IF NOT EXISTS account_baseline (
    account_id     char(64)      PRIMARY KEY,
    "window"       varchar(8)    NOT NULL DEFAULT '30d',  -- PG 保留字，须引号（列名对齐 DA-T-14）
    ewma_amount    numeric(14,2) NOT NULL DEFAULT 0,   -- 指数加权平均单笔金额
    p95_amount     numeric(14,2) NOT NULL DEFAULT 0,   -- 30 天 95 分位单笔金额
    hour_histogram jsonb         NOT NULL DEFAULT '[]', -- 24 桶小时分布（计数）
    tx_count       bigint        NOT NULL DEFAULT 0,   -- 基线样本量（<20 视为冷启动回退全局阈值）
    updated_at     timestamptz   NOT NULL DEFAULT now()
);

-- ---------- DA-T-15 disposition_outcome（C2 处置效果长窗回填） ----------
CREATE TABLE IF NOT EXISTS disposition_outcome (
    case_id         varchar(20)  PRIMARY KEY REFERENCES risk_case(case_id),
    disposed_at     timestamptz,                        -- 处置执行时间（T+0 基准）
    t7_label        varchar(16),                        -- T+7 效果标签（clean/recidivism/appealed/pending）
    t30_label       varchar(16),                        -- T+30 效果标签
    recidivism_flag boolean      NOT NULL DEFAULT false, -- 窗口内同主体再犯
    appealed_flag   boolean      NOT NULL DEFAULT false, -- 窗口内主体投诉/申诉（误处置信号）
    followed_at     timestamptz                          -- 最近一次回填时间
);

-- ---------- E1 知识代谢：kb_document 增有效性三列 ----------
ALTER TABLE kb_document ADD COLUMN IF NOT EXISTS cite_count          int     NOT NULL DEFAULT 0;
ALTER TABLE kb_document ADD COLUMN IF NOT EXISTS hit_correct        int     NOT NULL DEFAULT 0;
ALTER TABLE kb_document ADD COLUMN IF NOT EXISTS effectiveness_score numeric(5,2);

-- ---------- E2 规则进化：category 放宽接纳 rule_proposal（13 字符 > 原 varchar(12)） ----------
ALTER TABLE kb_document ALTER COLUMN category TYPE varchar(16);
ALTER TABLE kb_document DROP CONSTRAINT IF EXISTS kb_document_category_check;
ALTER TABLE kb_document ADD CONSTRAINT kb_document_category_check
    CHECK (category IN ('case','regulation','runbook','rule_proposal'));

-- ---------- C1 控辩互审：debate_json 只增列（DA-INV-09 延伸 DA-INV-05 语义） ----------
ALTER TABLE approval_record ADD COLUMN IF NOT EXISTS debate_json jsonb;
ALTER TABLE audit_log       ADD COLUMN IF NOT EXISTS debate_json jsonb;

-- ---------- DA-INV-08：rule_proposal 未经 human 审核不得生效（DB 触发器守护） ----------
-- 语义：rule_proposal 类目只能以 pending 申请单进入；置 published 必须经 pending 且
-- 事务携带 human:* 会话标记（与 DA-INV-06 同构，BA-BR-21）。直接 INSERT published 或
-- 跳过 pending 一律拒绝。
CREATE OR REPLACE FUNCTION trg_kb_proposal_gate() RETURNS trigger AS $$
BEGIN
    IF NEW.category = 'rule_proposal' AND NEW.status = 'published' THEN
        IF TG_OP = 'INSERT'
           OR OLD.status IS DISTINCT FROM 'pending'
           OR coalesce(current_setting('tg.actor', true), '') NOT LIKE 'human:%' THEN
            RAISE EXCEPTION 'E-PROPOSAL-HUMAN-GATE: rule_proposal 须经 pending 人审后方可生效（DA-INV-08，BA-BR-21）';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS kb_proposal_gate ON kb_document;
CREATE TRIGGER kb_proposal_gate
    BEFORE INSERT OR UPDATE OF status ON kb_document
    FOR EACH ROW EXECUTE FUNCTION trg_kb_proposal_gate();

-- ---------- 权限矩阵（02-roles.sql 同源语义：写入方授权，读方最小） ----------
-- 基线 upsert（AA-SK-01）与 outcome 回填任务（AA-SK-04 定时）均由 web-api 执行，
-- 故写权授 tg_web；tg_app（mcp-core）只读（与 risk_case/kb_document 编排层写同构）
GRANT SELECT, INSERT, UPDATE       ON account_baseline   TO tg_web;
GRANT SELECT                        ON account_baseline   TO tg_app;
GRANT SELECT, INSERT, UPDATE       ON disposition_outcome TO tg_web;
GRANT SELECT                        ON disposition_outcome TO tg_app;

CREATE INDEX IF NOT EXISTS idx_baseline_account ON account_baseline (account_id);
CREATE INDEX IF NOT EXISTS idx_outcome_follow   ON disposition_outcome (disposed_at)
    WHERE t7_label IS NULL OR t30_label IS NULL;
