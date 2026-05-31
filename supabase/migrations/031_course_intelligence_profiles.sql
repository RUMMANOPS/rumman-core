-- Migration 031: Course intelligence profiles + exam topic signals
--
-- Two tables that convert the raw document_chunks corpus into higher-level
-- academic intelligence, injected into the synthesis context bundle.
--
-- course_intelligence_profiles:
--   Pre-computed per-course corpus summary. Tells GPT how much RUMMAN knows
--   about a course and what kinds of material are available.
--   Refreshed by scripts/refresh_course_profiles.py (no LLM, pure SQL).
--
-- exam_intelligence:
--   Top recurring topics extracted from exam-tagged chunks per (course, exam_type).
--   Extracted by scripts/extract_exam_signals.py (gpt-4o-mini, ~$0.15 one-time).
--
-- Both are injected into _build_context_block() in search_api.py, expanding
-- the synthesis context bundle from student-only signals to corpus-aware signals.

-- ---------------------------------------------------------------------------
-- Course inventory profiles
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS course_intelligence_profiles (
    course_code      TEXT        NOT NULL,
    tenant_id        UUID        NOT NULL,
    total_chunks     INT         NOT NULL DEFAULT 0,
    exam_chunks      INT         NOT NULL DEFAULT 0,
    official_chunks  INT         NOT NULL DEFAULT 0,
    summary_chunks   INT         NOT NULL DEFAULT 0,
    community_chunks INT         NOT NULL DEFAULT 0,
    -- Derived flags (computed by the refresh script, not DB-generated,
    -- so they can incorporate business logic like minimum thresholds)
    has_exam_archives  BOOLEAN   NOT NULL DEFAULT false,
    has_official_docs  BOOLEAN   NOT NULL DEFAULT false,
    has_summaries      BOOLEAN   NOT NULL DEFAULT false,
    -- none | thin | moderate | strong
    coverage_level   TEXT        NOT NULL DEFAULT 'none',
    last_indexed_at  TIMESTAMPTZ,  -- when most recent chunk was added
    refreshed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (course_code, tenant_id)
);

CREATE INDEX IF NOT EXISTS cip_coverage_idx
    ON course_intelligence_profiles (tenant_id, coverage_level);

-- ---------------------------------------------------------------------------
-- Exam topic signals
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS exam_intelligence (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    course_code  TEXT        NOT NULL,
    tenant_id    UUID        NOT NULL,
    exam_type    TEXT        NOT NULL,   -- 'midterm' | 'final' | 'quiz' | 'general'
    top_topics   JSONB       NOT NULL,   -- ["OSI Model", "TCP/IP", ...]
    source_count INT         NOT NULL DEFAULT 0,
    confidence   TEXT        NOT NULL DEFAULT 'low',  -- low | medium | high
    extracted_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (course_code, tenant_id, exam_type)
);

CREATE INDEX IF NOT EXISTS ei_course_idx
    ON exam_intelligence (course_code, tenant_id, exam_type);
