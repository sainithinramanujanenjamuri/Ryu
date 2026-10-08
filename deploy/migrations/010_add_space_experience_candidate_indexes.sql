-- Migration 010: Candidate JSONB Expression Indexes on space_experiences
-- Phase 15.6.4 PostgreSQL Runtime Hardening (F05-AUDIT-04, MEM-PG-001, ADR-0050 §4)

-- 1. Functional expression index for capability match: (space_id, (action->>'capability'))
CREATE INDEX IF NOT EXISTS idx_space_exp_cap
    ON space_experiences (space_id, (action->>'capability'));

-- 2. Functional expression index for error class match: (space_id, (applicable_context->>'error_class'))
CREATE INDEX IF NOT EXISTS idx_space_exp_error_class
    ON space_experiences (space_id, (applicable_context->>'error_class'));

-- 3. Composite functional index for candidate retrieval with recency ordering
CREATE INDEX IF NOT EXISTS idx_space_exp_cap_stored
    ON space_experiences (space_id, (action->>'capability'), stored_at DESC);

