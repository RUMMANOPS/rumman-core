-- 028_self_healing_ingestion.sql
--
-- Extends telegram_sync_state with coverage-tracking columns used by the
-- self-healing ingestion pipeline.
--
-- Gap-fill jobs (job_type='telegram_gap_fill') live in the existing
-- processing_jobs table — no schema changes required there.
-- They are created by rumman_engine.py on every live message (ID-jump detection)
-- and at listener startup (outage-gap scan).
-- They are consumed by telegram_backfill_worker.py with priority over full backfills.

ALTER TABLE telegram_sync_state
    ADD COLUMN IF NOT EXISTS last_live_seen_at    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS coverage_verified_at TIMESTAMPTZ;
