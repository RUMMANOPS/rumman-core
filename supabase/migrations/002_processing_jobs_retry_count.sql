-- Migration 002: Add retry_count to processing_jobs
-- Required before deploying the updated audio_worker.py
-- Run in Supabase SQL editor (Dashboard → SQL Editor → New query)

-- Add retry_count if it doesn't already exist.
-- The audio worker filters: retry_count < MAX_RETRIES (default 5).
-- Jobs that fail MAX_RETRIES times remain as status='failed' and are never
-- picked up again, preventing infinite retry loops on corrupt/unresolvable files.
ALTER TABLE processing_jobs
    ADD COLUMN IF NOT EXISTS retry_count int NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_processing_jobs_retryable
    ON processing_jobs(job_type, status, retry_count)
    WHERE status IN ('pending', 'failed');
