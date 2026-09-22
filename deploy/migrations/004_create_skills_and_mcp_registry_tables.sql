-- Migration 004: Create Skills, Tools, and MCP Registry tables
-- Phase 9 Skills and MCP Extensibility schema

CREATE TABLE IF NOT EXISTS registered_skills (
    skill_id VARCHAR(255) NOT NULL,
    version VARCHAR(50) NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    signature TEXT NOT NULL,
    risk_tier VARCHAR(50) NOT NULL,
    capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    registered_by VARCHAR(255) NOT NULL,
    description TEXT,
    input_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
    lifecycle_state VARCHAR(50) NOT NULL DEFAULT 'REGISTERED',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (skill_id, version)
);

CREATE INDEX IF NOT EXISTS idx_skills_state ON registered_skills (lifecycle_state);
CREATE INDEX IF NOT EXISTS idx_skills_risk ON registered_skills (risk_tier);
CREATE INDEX IF NOT EXISTS idx_skills_content_hash ON registered_skills (content_hash);

CREATE TABLE IF NOT EXISTS registered_tools (
    tool_id VARCHAR(255) NOT NULL,
    version VARCHAR(50) NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    signature TEXT NOT NULL,
    capability VARCHAR(255) NOT NULL,
    risk_tier VARCHAR(50) NOT NULL,
    registered_by VARCHAR(255) NOT NULL,
    source_type VARCHAR(50) NOT NULL DEFAULT 'native', -- 'native' | 'mcp' | 'plugin'
    server_id VARCHAR(255),
    description TEXT,
    input_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
    lifecycle_state VARCHAR(50) NOT NULL DEFAULT 'ENABLED',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tool_id, version)
);

CREATE INDEX IF NOT EXISTS idx_tools_capability ON registered_tools (capability);
CREATE INDEX IF NOT EXISTS idx_tools_server ON registered_tools (server_id);
CREATE INDEX IF NOT EXISTS idx_tools_content_hash ON registered_tools (content_hash);

CREATE TABLE IF NOT EXISTS registered_mcp_servers (
    server_id VARCHAR(255) PRIMARY KEY,
    version VARCHAR(50) NOT NULL,
    transport VARCHAR(50) NOT NULL DEFAULT 'stdio',
    command VARCHAR(255) NOT NULL,
    args JSONB NOT NULL DEFAULT '[]'::jsonb,
    env_allowlist JSONB NOT NULL DEFAULT '[]'::jsonb,
    trust_level VARCHAR(50) NOT NULL DEFAULT 'UNTRUSTED',
    risk_tier VARCHAR(50) NOT NULL DEFAULT 'high',
    registered_by VARCHAR(255) NOT NULL,
    lifecycle_state VARCHAR(50) NOT NULL DEFAULT 'REGISTERED',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mcp_servers_trust ON registered_mcp_servers (trust_level);
CREATE INDEX IF NOT EXISTS idx_mcp_servers_state ON registered_mcp_servers (lifecycle_state);

