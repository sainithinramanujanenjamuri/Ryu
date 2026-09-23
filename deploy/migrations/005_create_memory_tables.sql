-- Migration 005: Create Space Memory, Global Knowledge, and Promotion Audit tables
-- Phase 10 Space Memory and Adaptation Loop schema (MEM-001..006, ADR-0033..0035)

CREATE TABLE IF NOT EXISTS space_experiences (
    experience_id      VARCHAR(255) NOT NULL,
    space_id           VARCHAR(255) NOT NULL,
    situation          JSONB        NOT NULL,
    action             JSONB        NOT NULL,
    outcome            TEXT         NOT NULL,
    counterfactual     TEXT         NOT NULL CHECK (counterfactual <> ''),
    applicable_context JSONB        NOT NULL DEFAULT '{}'::jsonb,
    stored_at          TIMESTAMPTZ  NOT NULL,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (experience_id, space_id)
);

CREATE INDEX IF NOT EXISTS idx_space_exp_space ON space_experiences (space_id);
CREATE INDEX IF NOT EXISTS idx_space_exp_stored ON space_experiences (stored_at DESC);

CREATE TABLE IF NOT EXISTS global_knowledge (
    knowledge_id       VARCHAR(255) PRIMARY KEY,
    source_space_id    VARCHAR(255) NOT NULL,
    content            JSONB        NOT NULL,
    promoted_by        TEXT         NOT NULL CHECK (promoted_by <> ''),
    promotion_pulse_id TEXT         NOT NULL,
    global_version     INTEGER      NOT NULL DEFAULT 1,
    promoted_at        TIMESTAMPTZ  NOT NULL,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_global_know_space ON global_knowledge (source_space_id);

CREATE TABLE IF NOT EXISTS promotion_audit (
    audit_id         BIGSERIAL    PRIMARY KEY,
    event_type       VARCHAR(50)  NOT NULL, -- 'requested' | 'approved' | 'rejected' | 'forged'
    promotion_id     VARCHAR(255) NOT NULL,
    knowledge_id     VARCHAR(255) NOT NULL,
    source_space_id  VARCHAR(255) NOT NULL,
    approver_id      VARCHAR(255),
    reason           TEXT,
    pulse_id         VARCHAR(255),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_promo_audit_kind ON promotion_audit (event_type);
CREATE INDEX IF NOT EXISTS idx_promo_audit_promo ON promotion_audit (promotion_id);

