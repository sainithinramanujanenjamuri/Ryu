-- Migration 009: Add embedding vector and metadata storage columns to space_experiences table
-- Phase 15.5.2 Durable Semantic Memory Storage + Schema (Finding F-05, MEM-SEM-001..005, ADR-0049)

-- 1. Add vector and embedding metadata columns (nullable for backward compatibility with historical records)
ALTER TABLE space_experiences
    ADD COLUMN IF NOT EXISTS embedding JSONB DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(100) DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS embedding_dimension INTEGER DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS embedding_version VARCHAR(50) DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS failure_fingerprint VARCHAR(255) DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS provenance_ref VARCHAR(255) DEFAULT NULL;

-- 2. Add composite index for candidate-generation and storage-support recency ordering
CREATE INDEX IF NOT EXISTS idx_space_exp_space_stored 
    ON space_experiences (space_id, stored_at DESC);

-- 3. Add partial index for candidate-generation failure fingerprint matching
CREATE INDEX IF NOT EXISTS idx_space_exp_fingerprint 
    ON space_experiences (space_id, failure_fingerprint) 
    WHERE failure_fingerprint IS NOT NULL AND failure_fingerprint <> '';
