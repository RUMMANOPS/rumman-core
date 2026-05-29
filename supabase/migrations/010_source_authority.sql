-- 010_source_authority.sql
-- Add source_authority to document_chunks to distinguish official institutional
-- content from community-sourced content (Telegram uploads, student notes).
--
-- Why this matters: search_api.py currently treats all chunks equally.
-- With authority metadata, synthesis can be instructed to prefer official
-- sources and to indicate confidence based on source type.

ALTER TABLE document_chunks
  ADD COLUMN IF NOT EXISTS source_authority TEXT
    CHECK (source_authority IN ('official', 'community', 'verified_community'))
    DEFAULT 'community';

-- Back-fill: any chunk with a source_document_id is from official ingestion
UPDATE document_chunks
SET source_authority = 'official'
WHERE source_document_id IS NOT NULL
  AND source_authority = 'community';

-- Index for filtering by authority in search queries
CREATE INDEX IF NOT EXISTS idx_document_chunks_source_authority
  ON document_chunks (tenant_id, source_authority);

COMMENT ON COLUMN document_chunks.source_authority IS
  'official = ingested from 0-Universities/ repository via ingest_document.py; '
  'community = from Telegram ingestion; '
  'verified_community = community content manually approved';
