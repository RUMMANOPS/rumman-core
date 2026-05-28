-- Migration 004: Media lifecycle tracking
-- Adds raw_file_deleted_at to source_documents so the pipeline can confirm
-- raw files have been scrubbed after extraction. Storage path stays for audit
-- but the file itself is gone.
--
-- Run in Supabase SQL editor after 003_knowledge_layer.sql.

ALTER TABLE source_documents
    ADD COLUMN IF NOT EXISTS raw_file_deleted_at timestamptz;

COMMENT ON COLUMN source_documents.raw_file_deleted_at IS
    'Set when the raw file is deleted from Storage after extraction. '
    'NULL means not yet deleted or no raw file (direct-text seeds). '
    'storage_path is kept for audit trail even after deletion.';
