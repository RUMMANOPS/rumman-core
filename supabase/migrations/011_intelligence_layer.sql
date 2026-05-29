-- 011_intelligence_layer.sql
-- Infrastructure for Phase 2 intelligence extraction.
-- Creates two tables:
--   worker_cursors     — persistent cursor state for read-and-process workers
--   intelligence_items — extracted items from message analysis, with dedup constraint
--
-- Neither table is used until INTELLIGENCE_WORKER_ENABLED=true is set.
-- See app/intelligence_worker.py and docs/constraints/hard-boundaries.md.

-- ---------------------------------------------------------------------------
-- worker_cursors: stores last-processed position for stateful workers
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS worker_cursors (
    worker_id   TEXT        PRIMARY KEY,
    tenant_id   UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    last_cursor TEXT,          -- opaque value: message UUID, timestamp, sequence ID
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- intelligence_items: extracted operational items from message analysis
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS intelligence_items (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    source_platform   TEXT        NOT NULL DEFAULT 'telegram',
    source_chat_id    TEXT        NOT NULL,
    source_message_id TEXT        NOT NULL,
    item_type         TEXT        NOT NULL
                      CHECK (item_type IN (
                          'assignment', 'quiz', 'exam', 'deadline',
                          'meeting', 'decision', 'reminder', 'announcement'
                      )),
    title             TEXT        NOT NULL,
    description       TEXT,
    due_date          DATE,
    course_code       TEXT,
    confidence        FLOAT       NOT NULL DEFAULT 0.5
                      CHECK (confidence BETWEEN 0.0 AND 1.0),
    metadata          JSONB       NOT NULL DEFAULT '{}',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Dedup: one item_type per source message per tenant
    UNIQUE(tenant_id, source_platform, source_message_id, item_type)
);

CREATE INDEX IF NOT EXISTS idx_intelligence_items_tenant
    ON intelligence_items(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_intelligence_items_chat
    ON intelligence_items(tenant_id, source_chat_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_intelligence_items_type
    ON intelligence_items(tenant_id, item_type);
CREATE INDEX IF NOT EXISTS idx_intelligence_items_course
    ON intelligence_items(tenant_id, course_code)
    WHERE course_code IS NOT NULL;
