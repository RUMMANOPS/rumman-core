-- Migration 023: worker_heartbeats table
--
-- Each Railway worker writes a heartbeat row (upsert) every N seconds.
-- Ops can query this table to determine worker liveness and detect stalls.
-- A worker absent for > 2× its beat_interval_seconds should be investigated.

CREATE TABLE IF NOT EXISTS worker_heartbeats (
    worker_id         TEXT        PRIMARY KEY,
    process           TEXT        NOT NULL,          -- 'embed', 'attribution', 'intelligence', etc.
    status            TEXT        NOT NULL DEFAULT 'running',  -- 'running' | 'idle' | 'error'
    last_beat_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    beat_interval_s   INT         NOT NULL DEFAULT 30,
    metadata          JSONB,                         -- last batch stats, error info, etc.
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE worker_heartbeats IS
    'Live liveness table — each active worker upserts a row every beat_interval_s seconds.';
