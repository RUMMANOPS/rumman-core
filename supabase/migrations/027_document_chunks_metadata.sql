-- =============================================================================
-- 027_document_chunks_metadata.sql
--
-- Adds two columns to document_chunks that the QA mining worker needs:
--
--   chat_name  TEXT  — source Telegram group name for telegram_export chunks.
--                      NULL for document-sourced chunks.
--   metadata   JSONB — arbitrary key-value bag for worker-specific context.
--                      QA miner stores: qa_fingerprint, source_message_ids,
--                      origin ('qa_mining').
--                      Existing workers can ignore this column.
--
-- Both columns are nullable and have no impact on existing rows or RPCs.
-- Safe to re-apply.
-- =============================================================================

ALTER TABLE document_chunks
    ADD COLUMN IF NOT EXISTS chat_name TEXT,
    ADD COLUMN IF NOT EXISTS metadata  JSONB DEFAULT '{}';

-- Index for fast "all QA chunks from this chat" lookups
CREATE INDEX IF NOT EXISTS idx_document_chunks_chat_name
    ON document_chunks (chat_name, source_type)
    WHERE chat_name IS NOT NULL;

-- GIN index for metadata key lookups (qa_fingerprint dedup, origin filter)
CREATE INDEX IF NOT EXISTS idx_document_chunks_metadata
    ON document_chunks USING gin (metadata)
    WHERE metadata != '{}';
