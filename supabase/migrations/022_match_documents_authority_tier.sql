-- Migration 022: Add authority_tier to match_documents return type
--
-- The original function only returned community-layer columns.
-- search_api._tier_label() reads authority_tier to label OFFICIAL vs COMMUNITY;
-- without it every vector-search result was silently tagged [COMMUNITY] even
-- when the chunk came from an official source document.
--
-- Also adds embedding IS NOT NULL guard to skip unchunkable rows.

-- Must DROP first — PostgreSQL doesn't allow changing OUT-parameter return types in place.
DROP FUNCTION IF EXISTS match_documents(vector, int, text, text);

CREATE OR REPLACE FUNCTION match_documents(
  query_embedding vector(1536),
  match_count     int     DEFAULT 10,
  filter_course   text    DEFAULT NULL,
  filter_type     text    DEFAULT NULL
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
    (1 - (dc.embedding <=> query_embedding))::float AS similarity
  FROM document_chunks dc
  WHERE
    dc.embedding IS NOT NULL
    AND (filter_course IS NULL OR dc.course_code = filter_course)
    AND (filter_type   IS NULL OR dc.source_type = filter_type)
  ORDER BY dc.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;
