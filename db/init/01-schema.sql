-- TradeGuard DDL（US-E2-01，依据 docs/08 数据字典；变更纪律：先改 08 → 再改本文件）
-- 通用约定：金额 numeric(14,2)；时间 timestamptz；个人标识哈希；只增表禁 UPDATE/DELETE（权限层强制）

CREATE EXTENSION IF NOT EXISTS vector;   -- pgvector（DA-T-10 HNSW）
CREATE EXTENSION IF NOT EXISTS pgcrypto; -- gen_random_uuid 备用

-- DA-T-01 account
CREATE TABLE account (
    account_hash      char(64) PRIMARY KEY,
    risk_level        smallint     NOT NULL DEFAULT 0 CHECK (risk_level BETWEEN 0 AND 5),
    list_flag         varchar(8)   NOT NULL DEFAULT 'none'
                      CHECK (list_flag IN ('none','watch','gray','black')),
    contact_hash      char(64),
    credit_score_mock smallint,
    created_at        timestamptz  NOT NULL DEFAULT now(),
    updated_at        timestamptz  NOT NULL DEFAULT now()
);

-- DA-T-02 transaction（按月分区，BA-BR-12 归档）
CREATE TABLE transaction (
    tx_id         varchar(40)    NOT NULL,
    account_hash  char(64)       NOT NULL,
    payee_hash    char(64),
    amount        numeric(14,2)  NOT NULL,
    mcc           varchar(4)     NOT NULL,
    channel       varchar(16)    NOT NULL CHECK (channel IN ('CNP','POS','ATM','transfer')),
    device_fp_hash char(64),
    ip            inet,
    geo           varchar(32),
    ts            timestamptz    NOT NULL,
    PRIMARY KEY (tx_id, ts)
) PARTITION BY RANGE (ts);

CREATE TABLE transaction_2026_01 PARTITION OF transaction FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE transaction_2026_02 PARTITION OF transaction FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE transaction_2026_03 PARTITION OF transaction FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE transaction_2026_04 PARTITION OF transaction FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE transaction_2026_05 PARTITION OF transaction FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE transaction_2026_06 PARTITION OF transaction FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE transaction_2026_07 PARTITION OF transaction FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE transaction_2026_08 PARTITION OF transaction FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE transaction_2026_09 PARTITION OF transaction FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE transaction_2026_10 PARTITION OF transaction FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE transaction_2026_11 PARTITION OF transaction FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE transaction_2026_12 PARTITION OF transaction FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
CREATE TABLE transaction_default PARTITION OF transaction DEFAULT;

-- DA-T-03 risk_case（聚合根，乐观锁 DA-INV-01）
CREATE TABLE risk_case (
    case_id       varchar(20) PRIMARY KEY,
    subject_ref   varchar(64)  NOT NULL,
    status        varchar(20)  NOT NULL DEFAULT 'REGISTERED'
                  CHECK (status IN ('REGISTERED','AGGREGATING','INVESTIGATING','PENDING_APPROVAL',
                                    'APPROVED','REJECTED','MANUAL_REVIEW','DISPOSING','DISPOSED',
                                    'VERIFIED','ROLLBACK','ARCHIVED')),
    risk_score    smallint     NOT NULL DEFAULT 0 CHECK (risk_score BETWEEN 0 AND 100),
    current_agent varchar(16),
    context_json  jsonb        NOT NULL DEFAULT '{}',
    version       int          NOT NULL DEFAULT 0,
    matrix_room   varchar(64),
    trace_id      varchar(40),
    created_at    timestamptz  NOT NULL DEFAULT now(),
    updated_at    timestamptz  NOT NULL DEFAULT now(),
    closed_at     timestamptz
);

-- DA-T-04 risk_signal（🔒 只增）
CREATE TABLE risk_signal (
    signal_id    varchar(40) PRIMARY KEY,
    case_id      varchar(20)  NOT NULL REFERENCES risk_case(case_id),
    source       varchar(12)  NOT NULL CHECK (source IN ('tx','credit','sentiment','complaint')),
    type         varchar(40)  NOT NULL,
    confidence   numeric(3,2) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    raw_ref      varchar(64),
    query_reason varchar(200) NOT NULL,
    degraded     boolean      NOT NULL DEFAULT false,
    velocity_json jsonb,   -- BA-BR-14 {velocity_1h, velocity_24h}；tx 源必填
    ts           timestamptz  NOT NULL DEFAULT now()
);

-- DA-T-05 case_evidence（🔒 只增）
CREATE TABLE case_evidence (
    evidence_id varchar(40) PRIMARY KEY,
    case_id     varchar(20)  NOT NULL REFERENCES risk_case(case_id),
    claim       varchar(500) NOT NULL,
    source_ref  varchar(200) NOT NULL,
    confidence  numeric(3,2) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    ts          timestamptz  NOT NULL DEFAULT now()
);

-- DA-T-06 disposition_record（幂等键 DA-INV-03）
CREATE TABLE disposition_record (
    exec_id         varchar(40) PRIMARY KEY,
    case_id         varchar(20)   NOT NULL REFERENCES risk_case(case_id),
    action          varchar(10)   NOT NULL CHECK (action IN ('block','freeze','reduce','release')),
    amount          numeric(14,2),
    idempotency_key varchar(60)   NOT NULL UNIQUE,
    approval_ref    varchar(40),
    status          varchar(12)   NOT NULL DEFAULT 'submitted'
                    CHECK (status IN ('submitted','executed','failed','rolled_back')),
    receipt         jsonb,
    ts              timestamptz   NOT NULL DEFAULT now()
);

-- DA-T-07 approval_record（AG-04 创建、人类回填，BA-BR-13 时效）
CREATE TABLE approval_record (
    approval_id varchar(40) PRIMARY KEY,
    case_id     varchar(20)  NOT NULL REFERENCES risk_case(case_id),
    approver    varchar(40),
    decision    varchar(10)  NOT NULL DEFAULT 'pending'
                CHECK (decision IN ('pending','approved','rejected')),
    opinion     varchar(500),
    created_at  timestamptz  NOT NULL DEFAULT now(),
    decided_at  timestamptz
);

-- DA-T-08 audit_log（🔒 append-only，DA-INV-05）
CREATE TABLE audit_log (
    log_id   varchar(40) PRIMARY KEY,
    actor    varchar(40)  NOT NULL,
    action   varchar(60)  NOT NULL,
    target   varchar(64)  NOT NULL,
    basis    varchar(300) NOT NULL,
    trace_id varchar(40),
    ts       timestamptz  NOT NULL DEFAULT now()
);

-- DA-T-09 kb_document（DA-INV-06：发布仅人工）
CREATE TABLE kb_document (
    doc_id      varchar(40) PRIMARY KEY,
    category    varchar(12)  NOT NULL CHECK (category IN ('case','regulation','runbook')),
    title       varchar(200) NOT NULL,
    content     text         NOT NULL,
    status      varchar(10)  NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','published','rejected')),
    applicant   varchar(16)  NOT NULL DEFAULT 'AA-AG-05',
    reviewer    varchar(40),
    ts          timestamptz  NOT NULL DEFAULT now(),
    reviewed_at timestamptz
);

-- DA-T-10 kb_embedding（pgvector HNSW）
CREATE TABLE kb_embedding (
    doc_id    varchar(40) NOT NULL REFERENCES kb_document(doc_id),
    chunk_id  int         NOT NULL,
    embedding vector(1024) NOT NULL,
    text      text        NOT NULL,
    PRIMARY KEY (doc_id, chunk_id)
);

-- DA-T-11 sys_config（Nacos 镜像落盘）
CREATE TABLE sys_config (
    key        varchar(60) PRIMARY KEY,
    value      varchar(200) NOT NULL,
    version    int          NOT NULL DEFAULT 0,
    source     varchar(10)  NOT NULL DEFAULT 'nacos',
    updated_at timestamptz  NOT NULL DEFAULT now()
);

-- DA-T-12 agent_memory（Agent 长期记忆，写/读时机见 03 §4）
CREATE TABLE agent_memory (
    memory_id varchar(40) PRIMARY KEY,
    agent_id  varchar(16) NOT NULL,
    case_id   varchar(20),
    summary   text        NOT NULL,
    ts        timestamptz NOT NULL DEFAULT now()
);

-- ---------- 索引（08 §4） ----------
CREATE INDEX idx_tx_account_ts     ON transaction (account_hash, ts DESC);
CREATE INDEX idx_tx_device         ON transaction (device_fp_hash);
CREATE INDEX idx_case_queue        ON risk_case (status, risk_score DESC);
CREATE INDEX idx_case_subject      ON risk_case (subject_ref);
CREATE INDEX idx_signal_case       ON risk_signal (case_id);
CREATE INDEX idx_evidence_case     ON case_evidence (case_id);
CREATE INDEX idx_approval_queue    ON approval_record (decision, created_at);
CREATE INDEX idx_audit_target_ts   ON audit_log (target, ts);
CREATE INDEX idx_embedding_hnsw    ON kb_embedding USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_memory_agent_ts   ON agent_memory (agent_id, ts DESC);

-- ---------- BA-BR 阈值种子（sys_config，正式值经 Nacos 下发，SC-06） ----------
INSERT INTO sys_config (key, value) VALUES
  ('br-01-auto-block-score', '70'),
  ('br-01-mid-review-score', '40'),
  ('br-01-auto-amount-limit', '5000'),
  ('br-13-approval-timeout-min', '30'),
  ('br-14-velocity-1h-count', '10');
