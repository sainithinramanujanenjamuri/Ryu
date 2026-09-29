-- Migration 006: Create execution_attempts and convergence_state tables
-- Phase 12.8 Crash Recovery & Durable Execution State schema (ADR-0042, RECOVERY-001..007)

-- ─── execution_attempts ───────────────────────────────────────────────────────
-- Durable record of every task dispatch attempt.
-- Written BEFORE worker dispatch (status='dispatched') and updated on completion.
-- Authoritative source for startup recovery to detect interrupted attempts.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS execution_attempts (
    attempt_id       VARCHAR(255) PRIMARY KEY,
    idempotency_key  VARCHAR(255) NOT NULL UNIQUE,
    space_id         VARCHAR(255) NOT NULL,
    task_id          VARCHAR(255) NOT NULL,
    plan_version     INTEGER      NOT NULL,
    attempt_number   INTEGER      NOT NULL DEFAULT 1,
    capability       VARCHAR(255) NOT NULL DEFAULT '',
    status           VARCHAR(50)  NOT NULL DEFAULT 'dispatched',
    -- 'dispatched' | 'running' | 'completed' | 'failed' | 'recovered'
    worker_id        VARCHAR(255),
    lease_token      VARCHAR(255),
    started_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    completed_at     TIMESTAMPTZ,
    failure_class    VARCHAR(255),
    failure_message  TEXT,
    exit_code        INTEGER,
    artifact_sha256  VARCHAR(128),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_exec_attempts_space ON execution_attempts (space_id);
CREATE INDEX IF NOT EXISTS idx_exec_attempts_task ON execution_attempts (task_id);
CREATE INDEX IF NOT EXISTS idx_exec_attempts_status ON execution_attempts (status);
CREATE INDEX IF NOT EXISTS idx_exec_attempts_idempotency ON execution_attempts (idempotency_key);
CREATE INDEX IF NOT EXISTS idx_exec_attempts_interrupted
    ON execution_attempts (status, started_at)
    WHERE status IN ('dispatched', 'running');

-- ─── convergence_state ────────────────────────────────────────────────────────
-- Durable record of ConvergenceEngine bounded counters and failure fingerprints.
-- Keyed per (space_id, task_id).
-- Ensures retry_count and replan_count survive process restarts.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS convergence_state (
    space_id              VARCHAR(255) NOT NULL,
    task_id               VARCHAR(255) NOT NULL,
    retry_count           INTEGER      NOT NULL DEFAULT 0,
    replan_count          INTEGER      NOT NULL DEFAULT 0,
    failure_fingerprints  JSONB        NOT NULL DEFAULT '[]'::jsonb,
    last_failure_class    VARCHAR(255),
    last_failure_at       TIMESTAMPTZ,
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (space_id, task_id)
);

CREATE INDEX IF NOT EXISTS idx_convergence_space ON convergence_state (space_id);
CREATE INDEX IF NOT EXISTS idx_convergence_updated ON convergence_state (updated_at DESC);
