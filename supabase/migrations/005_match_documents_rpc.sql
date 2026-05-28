-- match_documents: semantic search over document_chunks via pgvector cosine similarity.
-- Called by search_api.py with a pre-computed 1536-dim embedding.
CREATE OR REPLACE FUNCTION match_documents(
  query_embedding vector(1536),
  match_count     int     DEFAULT 10,
  filter_course   text    DEFAULT NULL,
  filter_type     text    DEFAULT NULL
)
RETURNS TABLE (
  id          uuid,
  content     text,
  course_code text,
  source_type text,
  exam_type   text,
  institution text,
  chunk_index int,
  similarity  float
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
    (1 - (dc.embedding <=> query_embedding))::float AS similarity
  FROM document_chunks dc
  WHERE
    (filter_course IS NULL OR dc.course_code = filter_course)
    AND (filter_type   IS NULL OR dc.source_type = filter_type)
  ORDER BY dc.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;
