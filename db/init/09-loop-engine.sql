-- 09-loop-engine.sql —— LoopEngine 运行时设施（有界确定性环的落库层）
-- 背景（docs/14 延伸 · loop 工程）：项目内三类手写环（EventWorker 处理环、
-- escalation/metabolism 巡检环、plan-reflect 认知环）缺少统一的失败归宿与
-- 效果归因设施——重试耗尽只留日志（无 DLQ/告警/上限），rule_proposal 生效后
-- 无效果观测（慢环不闭）。本脚本补两张环设施表 + 一条归因链接列。
-- 纪律不变：状态机仍是迁移权威（DA-INV-01/02），人工门不被环绕过，环记录只增。

-- ---------- L1 失败归宿：处理死信表（EventWorker 环的 DLQ 默认策略） ----------
-- 语义：案件在聚合环重试耗尽 → 累计 attempts；达上限置 parked=true，轮询排除
-- 该车案件（不再无限重试），由人工经 /api/deadletter/{case_id}/retry 复位放行。
CREATE TABLE IF NOT EXISTS processing_deadletter (
    case_id         varchar(32) PRIMARY KEY,
    stage           varchar(32)  NOT NULL DEFAULT 'aggregation',
    error_class     varchar(80),
    error_msg       varchar(300),
    attempts        integer      NOT NULL DEFAULT 0,
    parked          boolean      NOT NULL DEFAULT false,
    first_failed_at timestamptz  NOT NULL DEFAULT now(),
    last_failed_at  timestamptz  NOT NULL DEFAULT now(),
    resolved_by     varchar(40),
    resolved_at     timestamptz
);

GRANT SELECT, INSERT, UPDATE ON processing_deadletter TO tg_web;
GRANT SELECT                 ON processing_deadletter TO tg_app;

-- ---------- L3 慢环显式化：规则提案来源链接 + 效果归因表 ----------
-- 链接：kb_document.source_case_id 记录提案由哪个案件触发（follow_outcomes 的
-- 再犯命中 → rule_proposal，E2 规则进化发生器）；归因表观测「提案发布后该主体
-- 是否再犯」，使规则进化环可度量（KPI-08 载体）。
ALTER TABLE kb_document ADD COLUMN IF NOT EXISTS source_case_id varchar(32);

CREATE TABLE IF NOT EXISTS proposal_attribution (
    doc_id         varchar(40) PRIMARY KEY REFERENCES kb_document(doc_id),
    source_case_id varchar(32) NOT NULL,
    subject_ref    char(64)    NOT NULL,
    published_at   timestamptz,
    recurred_after boolean,
    checked_at     timestamptz
);

GRANT SELECT, INSERT, UPDATE ON proposal_attribution TO tg_web;
GRANT SELECT                 ON proposal_attribution TO tg_app;
