-- Migration 002: Create registered_resources and resource_leases tables
-- Phase 3 Resource Manager and Leases schema

CREATE TABLE IF NOT EXISTS registered_resources (
    handle VARCHAR(255) PRIMARY KEY,
    space_id VARCHAR(255) NOT NULL,
    resource_type VARCHAR(255) NOT NULL,
    provider_id VARCHAR(255) NOT NULL,
    instance_id VARCHAR(255) NOT NULL,
    total_capacity INT NOT NULL DEFAULT 1,
    allocated_capacity INT NOT NULL DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_resources_space ON registered_resources (space_id);

CREATE TABLE IF NOT EXISTS resource_leases (
    lease_token VARCHAR(255) PRIMARY KEY,
    space_id VARCHAR(255) NOT NULL,
    resource_type VARCHAR(255) NOT NULL,
    provider_id VARCHAR(255) NOT NULL,
    instance_id VARCHAR(255) NOT NULL,
    requester_id VARCHAR(255) NOT NULL,
    idempotency_key VARCHAR(255),
    units INT NOT NULL DEFAULT 1,
    state VARCHAR(50) NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL,
    expiry TIMESTAMPTZ NOT NULL,
    renewed_count INT NOT NULL DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_leases_space ON resource_leases (space_id);
CREATE INDEX IF NOT EXISTS idx_leases_res ON resource_leases (resource_type, provider_id, instance_id);
CREATE INDEX IF NOT EXISTS idx_leases_state ON resource_leases (state);
CREATE INDEX IF NOT EXISTS idx_leases_idempotency ON resource_leases (space_id, requester_id, idempotency_key);

