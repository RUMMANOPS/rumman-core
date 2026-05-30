-- Migration 023: worker_heartbeats — no-op note
--
-- worker_heartbeats was already created by migration 016_temporal_and_ops.sql
-- with this schema (columns: worker_id, service_name, tenant_id, last_seen_at,
-- jobs_processed, jobs_failed, last_job_id, status, metadata).
--
-- app/heartbeat.py writes to this existing schema.
-- The CREATE TABLE IF NOT EXISTS below was a no-op on apply.

CREATE TABLE IF NOT EXISTS worker_heartbeats (
    worker_id     TEXT PRIMARY KEY,
    service_name  TEXT NOT NULL,
    tenant_id     UUID,
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status        TEXT NOT NULL DEFAULT 'running',
    metadata      JSONB NOT NULL DEFAULT '{}'
);
