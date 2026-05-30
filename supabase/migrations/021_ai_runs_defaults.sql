-- Migration 021: Add safe defaults to ai_runs legacy columns
--
-- run_type was NOT NULL with no default — new workers (attribution, intelligence)
-- were failing silently because they used the 'job_type' field instead.
-- Set a fallback default so inserting without run_type doesn't fail.

ALTER TABLE ai_runs
    ALTER COLUMN run_type SET DEFAULT 'unknown';
