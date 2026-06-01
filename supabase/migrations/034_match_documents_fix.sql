-- =============================================================================
-- Migration 034: Fix match_documents — add metadata return, active-only filter,
--                and tenant_id parameter
--
-- Bugs fixed:
--
-- 1. metadata column missing from return set (added in migration 027).
--    search_api._tier_label() reads metadata.origin to label [CALENDAR] and
--    [INTELLIGENCE] chunks. Without it, every vector-search result was tagged
--    [COMMUNITY] regardless of its actual origin — authority tiers in the
--    synthesis prompt were silently wrong.
--
-- 2. No superseded_by IS NULL guard.
--    active_document_chunks view (migration 025) gates on superseded_by IS NULL,
--    but match_documents queried document_chunks directly — could return stale
--    superseded versions of chunks when newer ones existed.
--
-- 3. No tenant_id filter.
--    ADR-0004: every retrieval path must be tenant-scoped. Single-tenant today
--    but the function must accept the parameter now so callers don't need to
--    change their signature when tenants 2+ arrive. Defaults to NULL = no filter
--    (backward-compatible for existing callers not yet passing it).
-- =============================================================================

DROP FUNCTION IF EXISTS match_documents(vector, int, text, text);
DROP FUNCTION IF EXISTS match_documents(vector, int, text, text, uuid);

CREATE OR REPLACE FUNCTION match_documents(
  query_embedding vector(1536),
  match_count     int     DEFAULT 10,
  filter_course   text    DEFAULT NULL,
  filter_type     text    DEFAULT NULL,
  filter_tenant   uuid    DEFAULT NULL
)
RETURNS TABLE (
  id              uuid,
  content         text,
  course_code     text,
  source_type     text,
  exam_type       text,
  institution     text,
  chunk_index     int,
  authority_tier  text,
  metadata        jsonb,
  similarity      float
)
LANGUAGE plpgsql STABLE
AS $$
BEGIN
  RETURN QUERY
  SELECT
    dc.id,
    dc.content,
    dc.course_code,
    dc.source_type,
    dc.exam_type,
    dc.institution,
    dc.chunk_index,
    dc.authority_tier,
    dc.metadata,
    (1 - (dc.embedding <=> query_embedding))::float AS similarity
  FROM document_chunks dc
  WHERE
    dc.embedding      IS NOT NULL
    AND dc.superseded_by IS NULL
    AND (filter_course IS NULL OR dc.course_code = filter_course)
    AND (filter_type   IS NULL OR dc.source_type = filter_type)
    AND (filter_tenant IS NULL OR dc.tenant_id   = filter_tenant)
  ORDER BY dc.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;
