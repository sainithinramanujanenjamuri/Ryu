-- Migration 001: Create pulses table
-- Phase 1 Durable Pulse Store schema
CREATE TABLE IF NOT EXISTS pulses (
    position BIGSERIAL PRIMARY KEY,
    id VARCHAR(255) NOT NULL UNIQUE,
    space_id VARCHAR(255) NOT NULL,
    type VARCHAR(255) NOT NULL,
    severity VARCHAR(50) NOT NULL,
    source VARCHAR(255) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL,
    taint BOOLEAN NOT NULL DEFAULT FALSE,
    correlation_id VARCHAR(255) NOT NULL,
    parent_pulse_id VARCHAR(255),
    redis_published BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pulses_space_id ON pulses (space_id);
CREATE INDEX IF NOT EXISTS idx_pulses_correlation_id ON pulses (correlation_id);
CREATE INDEX IF NOT EXISTS idx_pulses_parent_pulse_id ON pulses (parent_pulse_id);
CREATE INDEX IF NOT EXISTS idx_pulses_type ON pulses (type);
CREATE INDEX IF NOT EXISTS idx_pulses_redis_published ON pulses (redis_published) WHERE redis_published = FALSE;

