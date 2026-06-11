-- Migration 039: Knowledge Graph RPCs
--
-- match_kg_topics — semantic similarity search over kg_topics.embedding
--                   used by topic_normalizer_worker to find canonical matches

CREATE OR REPLACE FUNCTION match_kg_topics(
    query_embedding  VECTOR(1536),
    match_threshold  FLOAT DEFAULT 0.88,
    match_count      INT   DEFAULT 5
)
RETURNS TABLE (
    id               UUID,
    canonical_name   TEXT,
    canonical_name_ar TEXT,
    domain           TEXT,
    similarity       FLOAT
)
LANGUAGE SQL STABLE AS $$
    SELECT
        kt.id,
        kt.canonical_name,
        kt.canonical_name_ar,
        kt.domain,
        1 - (kt.embedding <=> query_embedding) AS similarity
    FROM kg_topics kt
    WHERE kt.embedding IS NOT NULL
      AND 1 - (kt.embedding <=> query_embedding) >= match_threshold
    ORDER BY kt.embedding <=> query_embedding
    LIMIT match_count;
$$;
