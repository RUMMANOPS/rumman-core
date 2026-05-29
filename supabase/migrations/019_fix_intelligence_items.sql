-- Migration 019: Fix intelligence_items table
--
-- Problems found:
--   1. Missing tenant_id column (worker inserts it; PostgREST silently ignores unknown columns)
--   2. Missing unique constraint (worker uses on_conflict — without it, upsert becomes blind insert)
--   3. Worker sends due_date but column is due_at — add due_date alias column for compatibility

ALTER TABLE intelligence_items
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id),
    ADD COLUMN IF NOT EXISTS due_date DATE;

-- Backfill due_date from due_at where possible
UPDATE intelligence_items
SET due_date = due_at::date
WHERE due_at IS NOT NULL AND due_date IS NULL;

-- Add the unique constraint the worker depends on for upsert dedup
CREATE UNIQUE INDEX IF NOT EXISTS intelligence_items_dedup_idx
    ON intelligence_items (tenant_id, source_platform, source_message_id, item_type)
    WHERE tenant_id IS NOT NULL;

-- Index for tenant-scoped queries
CREATE INDEX IF NOT EXISTS intelligence_items_tenant_type_idx
    ON intelligence_items (tenant_id, item_type, created_at DESC);

-- Index for course-scoped retrieval
CREATE INDEX IF NOT EXISTS intelligence_items_course_idx
    ON intelligence_items (tenant_id, course_code)
    WHERE course_code IS NOT NULL;
