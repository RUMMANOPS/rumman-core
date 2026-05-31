-- migration 033: backfill tenant_id on document_chunks and related tables
--
-- 98,224 document_chunks (82% of corpus) have tenant_id = NULL because early
-- ingestion paths (embed_worker, ingest_document.py) were not setting it.
-- The system is currently single-tenant (SEU only).
-- ADR-0004: every operational object must carry tenant_id.
--
-- This migration backfills all NULL tenant_id values to SEU_TENANT_ID.
-- Safe to re-run (WHERE clause guards against overwrite of non-null values).

DO $$
DECLARE
    seu_tenant UUID := '00000000-0000-0000-0000-000000000001';
    rows_updated INT;
BEGIN
    -- document_chunks (primary fix — 98K+ rows)
    UPDATE document_chunks
    SET tenant_id = seu_tenant
    WHERE tenant_id IS NULL;
    GET DIAGNOSTICS rows_updated = ROW_COUNT;
    RAISE NOTICE 'document_chunks backfilled: %', rows_updated;

    -- source_documents
    UPDATE source_documents
    SET tenant_id = seu_tenant
    WHERE tenant_id IS NULL;
    GET DIAGNOSTICS rows_updated = ROW_COUNT;
    RAISE NOTICE 'source_documents backfilled: %', rows_updated;

    -- messages (belt-and-suspenders; listener should already set this)
    UPDATE messages
    SET tenant_id = seu_tenant
    WHERE tenant_id IS NULL;
    GET DIAGNOSTICS rows_updated = ROW_COUNT;
    RAISE NOTICE 'messages backfilled: %', rows_updated;
END $$;
