-- 006_query_intelligence.sql
--
-- Two tables that form the observability spine for query understanding:
--
--   query_logs           — one row per /search call; the raw material for all
--                          improvement analysis. Never blocks a response.
--
--   improvement_candidates — extracted signals awaiting human review before
--                          promotion into normalization_dict.json or intent_hints.json.
--                          AI populates this; humans decide what ships.

CREATE TABLE IF NOT EXISTS query_logs (
    id                  uuid        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    query_raw           text        NOT NULL,
    query_normalized    text,
    intent_type         text,
    intent_confidence   float,
    course_codes        text[],
    exam_type           text,
    source_type_filter  text,
    result_count        int         NOT NULL DEFAULT 0,
    top_similarity      float,
    response_grounded   bool        NOT NULL DEFAULT false,
    search_params       jsonb,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_query_logs_created_at
    ON query_logs (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_query_logs_intent_type
    ON query_logs (intent_type)
    WHERE intent_type IS NOT NULL;

-- Partial index for quick zero-result mining
CREATE INDEX IF NOT EXISTS idx_query_logs_zero_results
    ON query_logs (created_at DESC)
    WHERE result_count = 0;

-- Partial index for low-confidence queries
CREATE INDEX IF NOT EXISTS idx_query_logs_low_confidence
    ON query_logs (created_at DESC)
    WHERE intent_confidence < 0.65;


CREATE TABLE IF NOT EXISTS improvement_candidates (
    id              uuid        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    surface_form    text        NOT NULL,
    canonical_form  text,
    -- 'normalization' = word/phrase substitution candidate
    -- 'intent_hint'   = new trigger→intent mapping candidate
    -- 'routing_gap'   = query pattern with no good routing
    category        text        NOT NULL DEFAULT 'normalization',
    -- 'corpus'        = extracted from real Telegram messages
    -- 'generated'     = produced by OpenAI seed generation
    -- 'zero_result'   = surfaced from zero-result query logs
    -- 'low_confidence'= surfaced from low-confidence query logs
    source          text        NOT NULL,
    frequency       int         NOT NULL DEFAULT 1,
    example_query   text,
    -- 'pending' → 'approved' → promoted into data/*.json
    -- 'pending' → 'rejected' → done
    status          text        NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected')),
    reviewed_by     text,
    reviewed_at     timestamptz,
    promoted_at     timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_improvement_candidates_status
    ON improvement_candidates (status, created_at DESC);

-- Prevent duplicate pending entries for the same surface+category
CREATE UNIQUE INDEX IF NOT EXISTS idx_improvement_candidates_dedup
    ON improvement_candidates (surface_form, category)
    WHERE status = 'pending';
