-- Migration 008: Create composite indexes for bounded pulse retrieval
-- Phase 15.3 Bounded Pulse Retrieval schema hardening (Finding F-03)

CREATE INDEX IF NOT EXISTS idx_pulses_space_position ON pulses (space_id, position);
CREATE INDEX IF NOT EXISTS idx_pulses_space_type_position ON pulses (space_id, type, position);
CREATE INDEX IF NOT EXISTS idx_pulses_space_corr_type ON pulses (space_id, correlation_id, type);
