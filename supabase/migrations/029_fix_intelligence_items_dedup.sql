-- Migration 029: Fix intelligence_items dedup index for ON CONFLICT support
--
-- The existing intelligence_items_dedup_idx is a PARTIAL index
-- (WHERE tenant_id IS NOT NULL), which PostgreSQL cannot use with
-- ON CONFLICT column-list syntax. The intelligence worker's upsert
-- via PostgREST on_conflict parameter fails with error 42P10.
--
-- Fix: replace partial index with a full unique index.
-- All rows have non-null tenant_id (enforced by the FK and worker logic),
-- so this is safe.

DROP INDEX IF EXISTS intelligence_items_dedup_idx;

CREATE UNIQUE INDEX intelligence_items_dedup_idx
    ON intelligence_items (tenant_id, source_platform, source_message_id, item_type);
