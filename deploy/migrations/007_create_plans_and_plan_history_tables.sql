-- Migration 007: Create plans and plan_history tables
-- Phase 15.1 Durable PostgreSQL PlanStore & Cold-Boot Reconstruction schema (ADR-0045, REC-003, PLAN-DURABLE-001..010)

-- ─── plans ────────────────────────────────────────────────────────────────────
-- Authoritative, current Plan and TaskGraph state for each Space.
-- One row per space_id. Updated atomically on successful PlanDelta CAS commits.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS plans (
    space_id            VARCHAR(255) PRIMARY KEY,
    plan_version        INTEGER      NOT NULL,
    graph_json          JSONB        NOT NULL,
    last_winning_delta  VARCHAR(255),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plans_space ON plans (space_id);
CREATE INDEX IF NOT EXISTS idx_plans_version ON plans (space_id, plan_version);

-- ─── plan_history ─────────────────────────────────────────────────────────────
-- Immutable append-only historical snapshots of TaskGraphs across plan versions.
-- Enables deterministic historical plan inspection and auditability.
-- Keyed by (space_id, plan_version).
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS plan_history (
    space_id            VARCHAR(255) NOT NULL,
    plan_version        INTEGER      NOT NULL,
    graph_json          JSONB        NOT NULL,
    delta_id            VARCHAR(255),
    committed_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (space_id, plan_version)
);

CREATE INDEX IF NOT EXISTS idx_plan_history_space ON plan_history (space_id);
CREATE INDEX IF NOT EXISTS idx_plan_history_committed ON plan_history (committed_at DESC);
