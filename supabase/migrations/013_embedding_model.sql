-- =============================================================================
-- 013_embedding_model.sql
--
-- Adds embedding_model to document_chunks. Without this column we cannot
-- selectively re-embed when OpenAI releases a better model — every chunk
-- looks identical and we'd have to re-embed everything blindly.
--
-- With this column, the embed_worker can tag each chunk with the model it
-- used, and a future migration job can filter WHERE embedding_model != 'new'
-- to re-embed only the stale chunks.
--
-- Also adds embedding_dims for completeness — the HNSW index dimension is
-- locked at index-creation time, but this lets us detect model/dim mismatches
-- at the application layer before the index errors out.
-- =============================================================================

ALTER TABLE document_chunks
    ADD COLUMN IF NOT EXISTS embedding_model TEXT
        DEFAULT 'text-embedding-3-large';

ALTER TABLE document_chunks
    ADD COLUMN IF NOT EXISTS embedding_dims  INT
        DEFAULT 1536;

-- Backfill all existing embedded rows
UPDATE document_chunks
    SET embedding_model = 'text-embedding-3-large',
        embedding_dims  = 1536
    WHERE embedding IS NOT NULL
      AND embedding_model IS NULL;

-- NOT NULL constraint applied only to rows that have an embedding.
-- Rows still awaiting embedding leave both columns NULL until embed_worker runs.
-- (A CHECK constraint is more appropriate than NOT NULL here.)
ALTER TABLE document_chunks
    ADD CONSTRAINT chk_embedding_model_set
        CHECK (embedding IS NULL OR embedding_model IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_document_chunks_model
    ON document_chunks(embedding_model)
    WHERE embedding IS NOT NULL;
