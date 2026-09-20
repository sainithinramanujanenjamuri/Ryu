-- Migration 003: Create approver credentials, nonces, approvals tables and audit immutability trigger
-- Phase 8 Human Gates and CLI Channel schema

CREATE TABLE IF NOT EXISTS approver_credentials (
    approver_id VARCHAR(255) PRIMARY KEY,
    token_id VARCHAR(255) NOT NULL UNIQUE,
    secret_ref VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    is_revoked BOOLEAN NOT NULL DEFAULT FALSE,
    revoked_at TIMESTAMPTZ,
    revocation_reason TEXT,
    version INT NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS approver_auth_nonces (
    nonce VARCHAR(64) PRIMARY KEY,
    approver_id VARCHAR(255) NOT NULL REFERENCES approver_credentials(approver_id),
    timestamp DOUBLE PRECISION NOT NULL,
    consumed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nonces_timestamp ON approver_auth_nonces (timestamp);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id VARCHAR(255) PRIMARY KEY,
    space_id VARCHAR(255) NOT NULL,
    goal_id VARCHAR(255) NOT NULL,
    plan_id VARCHAR(255) NOT NULL,
    plan_version INTEGER NOT NULL,
    correlation_id VARCHAR(255) NOT NULL,
    parent_pulse_id VARCHAR(255),
    requester_id VARCHAR(255) NOT NULL,
    capability VARCHAR(255) NOT NULL,
    capability_request_hash VARCHAR(64) NOT NULL,
    risk_tier VARCHAR(50) NOT NULL,
    taint BOOLEAN NOT NULL DEFAULT FALSE,
    summary TEXT NOT NULL,
    timeout_class VARCHAR(50) NOT NULL,
    timeout_seconds DOUBLE PRECISION NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    queue_state VARCHAR(50) NOT NULL DEFAULT 'queued',
    approver_id VARCHAR(255),
    resolved_at DOUBLE PRECISION,
    consumed_at DOUBLE PRECISION,
    resolution_reason TEXT,
    decision_signature TEXT,
    decision_key_version INT NOT NULL DEFAULT 1,
    created_db_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_approvals_space_status ON approvals (space_id, status);
CREATE INDEX IF NOT EXISTS idx_approvals_queue_state ON approvals (space_id, queue_state);
CREATE INDEX IF NOT EXISTS idx_approvals_expires_at ON approvals (expires_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_approvals_req_hash ON approvals (capability_request_hash, plan_version);

-- Audit Immutability Trigger on pulses table
CREATE OR REPLACE FUNCTION prevent_pulse_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Audit Immutability Violation: pulses table is append-only. UPDATE and DELETE are prohibited.';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_pulses_immutable ON pulses;
CREATE TRIGGER trg_pulses_immutable
BEFORE UPDATE OR DELETE ON pulses
FOR EACH ROW EXECUTE FUNCTION prevent_pulse_modification();

