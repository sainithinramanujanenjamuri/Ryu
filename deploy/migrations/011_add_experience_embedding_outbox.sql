-- Migration 011: Add embedding status tracking and outbox columns to space_experiences table
-- Phase 15.6.5 Durable Experience Embedding Ingestion & Outbox (F05-AUDIT-01, MEM-INGEST-001, ADR-0050)

-- 1. Add outbox status, attempt tracking, and error diagnostic columns
ALTER TABLE space_experiences
    ADD COLUMN IF NOT EXISTS embedding_status VARCHAR(20) NOT NULL DEFAULT 'completed',
    ADD COLUMN IF NOT EXISTS embedding_attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS embedding_error TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS embedding_updated_at TIMESTAMPTZ DEFAULT NULL;

-- 2. Add partial index for efficient outbox polling and recovery by Space
CREATE INDEX IF NOT EXISTS idx_space_exp_embedding_outbox
    ON space_experiences (space_id, embedding_status, embedding_attempts)
    WHERE embedding_status IN ('pending', 'processing');

