-- =============================================================================
-- 012_messages_tenant_id.sql
--
-- Adds tenant_id to the messages table. This is the most critical Phase 3
-- enabler: without it, every pgvector query that joins messages is a
-- cross-tenant scan, and multi-university expansion requires a schema rewrite.
--
-- The messages table predates the migration system and was created without
-- tenant_id. All existing rows are SEU messages, so the backfill is safe.
--
-- After this migration, rumman_engine.py and telegram_backfill_worker.py
-- must start including tenant_id in INSERT payloads. The DEFAULT here is a
-- safety net so existing deployed code doesn't break on the deploy boundary.
-- =============================================================================

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS tenant_id UUID
        DEFAULT '00000000-0000-0000-0000-000000000001'::uuid;

-- Backfill all existing rows (all are SEU data)
UPDATE messages
    SET tenant_id = '00000000-0000-0000-0000-000000000001'::uuid
    WHERE tenant_id IS NULL;

-- Make column NOT NULL now that backfill is complete.
-- This enforces tenant presence going forward.
ALTER TABLE messages
    ALTER COLUMN tenant_id SET NOT NULL;

-- Retain the DEFAULT so current deployed workers don't break before code ships.
-- Remove DEFAULT once all workers include tenant_id explicitly.

CREATE INDEX IF NOT EXISTS idx_messages_tenant
    ON messages(tenant_id, message_date DESC);

CREATE INDEX IF NOT EXISTS idx_messages_tenant_chat
    ON messages(tenant_id, platform_chat_id, platform_message_id);
