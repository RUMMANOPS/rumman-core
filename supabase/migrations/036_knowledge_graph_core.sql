-- Migration 036: Knowledge Graph Core
--
-- Adds the structural entities missing from the current schema:
--   kg_syllabi          — versioned course outlines (source of truth for chapters)
--   kg_chapters         — chapter-level granularity within a syllabus
--   kg_topics           — canonical topic registry (resolves alias chaos)
--   kg_topic_aliases    — "TCP/IP" = "نموذج TCP/IP" = "طبقات الشبكة"
--   kg_provenance_edges — every knowledge atom linked to its origin + confidence
--
-- Also extends exam_questions with:
--   chapter_id   — direct FK once chapter_attribution_worker runs
--   topic_ids    — normalized topic references
--   embedding    — enables similarity-based chapter attribution

-- ---------------------------------------------------------------------------
-- kg_syllabi — versioned course outline
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS kg_syllabi (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    course_code         TEXT        NOT NULL,
    academic_year       TEXT,                       -- '2025-2026'
    version             INT         NOT NULL DEFAULT 1,
    source_doc_id       UUID        REFERENCES source_documents(id) ON DELETE SET NULL,
    source_type         TEXT        NOT NULL DEFAULT 'official',
        -- 'official' | 'inferred' — official = parsed from university DOCX/PDF
    total_chapters      INT,
    is_current          BOOLEAN     NOT NULL DEFAULT true,
    parsing_confidence  FLOAT       NOT NULL DEFAULT 0.0,
    raw_text            TEXT,       -- full extracted syllabus text (for re-parsing)
    parsed_at           TIMESTAMPTZ,
    superseded_by       UUID        REFERENCES kg_syllabi(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ks_confidence_range CHECK (parsing_confidence BETWEEN 0 AND 1)
);

CREATE INDEX IF NOT EXISTS ks_course_current_idx
    ON kg_syllabi (tenant_id, course_code, is_current)
    WHERE is_current = true;

-- ---------------------------------------------------------------------------
-- kg_chapters — chapter entity (the missing spine of knowledge attribution)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS kg_chapters (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    syllabus_id         UUID        REFERENCES kg_syllabi(id) ON DELETE CASCADE,
    course_code         TEXT        NOT NULL,
    chapter_number      INT         NOT NULL,
    chapter_title       TEXT,
    chapter_title_ar    TEXT,
    topics_raw          TEXT[],                     -- raw before normalization to kg_topics
    topic_ids           UUID[],                     -- normalized references (filled by topic_normalizer)
    learning_outcomes   TEXT[],
    week_start          INT,                        -- which week of semester this starts
    week_end            INT,
    -- Derived signals (updated by workers as data accumulates)
    exam_weight_pct     FLOAT,                      -- % of exam questions from this chapter
    question_count      INT         NOT NULL DEFAULT 0,
    difficulty_score    FLOAT,                      -- 0-1, derived from student performance
    confidence          FLOAT       NOT NULL DEFAULT 0.0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT kc_confidence_range CHECK (confidence BETWEEN 0 AND 1),
    CONSTRAINT kc_chapter_positive CHECK (chapter_number > 0),
    UNIQUE (course_code, chapter_number, syllabus_id)
);

CREATE INDEX IF NOT EXISTS kc_course_idx
    ON kg_chapters (tenant_id, course_code, chapter_number);

CREATE INDEX IF NOT EXISTS kc_topic_ids_gin_idx
    ON kg_chapters USING GIN (topic_ids);

-- ---------------------------------------------------------------------------
-- kg_topics — canonical topic registry
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS kg_topics (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    canonical_name      TEXT        NOT NULL,
    canonical_name_ar   TEXT,
    domain              TEXT,                       -- 'networking' | 'management' | 'finance' ...
    course_codes        TEXT[]      NOT NULL DEFAULT '{}',
    chapter_ids         UUID[]      NOT NULL DEFAULT '{}',
    frequency_score     FLOAT       NOT NULL DEFAULT 0.0,   -- times seen across all exams
    difficulty_score    FLOAT,                              -- derived from question performance
    embedding           VECTOR(1536),                       -- semantic search between topics
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (tenant_id, canonical_name)
);

CREATE INDEX IF NOT EXISTS kt_course_codes_gin_idx
    ON kg_topics USING GIN (course_codes);

CREATE INDEX IF NOT EXISTS kt_chapter_ids_gin_idx
    ON kg_topics USING GIN (chapter_ids);

CREATE INDEX IF NOT EXISTS kt_embedding_idx
    ON kg_topics USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;

-- ---------------------------------------------------------------------------
-- kg_topic_aliases — resolves cross-language + OCR variation
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS kg_topic_aliases (
    id          UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    topic_id    UUID    NOT NULL REFERENCES kg_topics(id) ON DELETE CASCADE,
    alias       TEXT    NOT NULL,
    language    TEXT    NOT NULL DEFAULT 'ar',    -- 'ar' | 'en'
    source      TEXT    NOT NULL DEFAULT 'question',
        -- 'question' | 'syllabus' | 'telegram' | 'human'
    frequency   INT     NOT NULL DEFAULT 1,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (topic_id, alias)
);

CREATE INDEX IF NOT EXISTS kta_alias_idx ON kg_topic_aliases (alias);
CREATE INDEX IF NOT EXISTS kta_topic_idx ON kg_topic_aliases (topic_id);

-- ---------------------------------------------------------------------------
-- kg_provenance_edges — every knowledge atom linked to its origin
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS kg_provenance_edges (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_type    TEXT        NOT NULL,   -- 'exam_question'|'kg_chapter'|'kg_topic'|'document_chunk'
    subject_id      UUID        NOT NULL,
    predicate       TEXT        NOT NULL,
        -- 'extracted_from' | 'assigned_to' | 'tagged_with' | 'verified_by'
        -- 'confirmed_by'   | 'contradicts'  | 'supersedes'
    object_type     TEXT        NOT NULL,   -- 'source_document'|'kg_chapter'|'kg_topic'|'kg_faculty'
    object_id       UUID        NOT NULL,
    confidence      FLOAT       NOT NULL DEFAULT 1.0,
    created_by      TEXT        NOT NULL,   -- worker name | 'human' | 'import_script'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT kpe_confidence_range CHECK (confidence BETWEEN 0 AND 1)
);

CREATE INDEX IF NOT EXISTS kpe_subject_idx
    ON kg_provenance_edges (subject_type, subject_id);

CREATE INDEX IF NOT EXISTS kpe_object_idx
    ON kg_provenance_edges (object_type, object_id);

CREATE INDEX IF NOT EXISTS kpe_predicate_idx
    ON kg_provenance_edges (predicate);

-- ---------------------------------------------------------------------------
-- Extend exam_questions with KG linkage columns
-- ---------------------------------------------------------------------------

-- Direct chapter FK — set by chapter_attribution_worker
ALTER TABLE exam_questions
    ADD COLUMN IF NOT EXISTS chapter_id UUID REFERENCES kg_chapters(id) ON DELETE SET NULL;

-- Normalized topic references — set by topic_normalizer_worker
ALTER TABLE exam_questions
    ADD COLUMN IF NOT EXISTS topic_ids UUID[] NOT NULL DEFAULT '{}';

-- Embedding for similarity-based chapter attribution
ALTER TABLE exam_questions
    ADD COLUMN IF NOT EXISTS embedding VECTOR(1536);

-- Index for chapter-based queries (once chapter_id is populated)
CREATE INDEX IF NOT EXISTS eq_chapter_id_idx
    ON exam_questions (chapter_id)
    WHERE chapter_id IS NOT NULL;

-- GIN index for normalized topic IDs
CREATE INDEX IF NOT EXISTS eq_topic_ids_gin_idx
    ON exam_questions USING GIN (topic_ids);

-- HNSW for embedding similarity (chapter attribution + similar question search)
CREATE INDEX IF NOT EXISTS eq_embedding_idx
    ON exam_questions USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;

-- ---------------------------------------------------------------------------
-- View: topic coverage per course (chapter × topic intersection)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW kg_topic_coverage AS
SELECT
    t.tenant_id,
    t.canonical_name,
    t.canonical_name_ar,
    t.domain,
    t.frequency_score,
    c.course_code,
    c.chapter_number,
    c.chapter_title,
    c.exam_weight_pct,
    COUNT(eq.id)                                            AS question_count,
    COUNT(eq.id) FILTER (WHERE eq.question_type = 'mcq')   AS mcq_count,
    COUNT(eq.id) FILTER (WHERE eq.question_type = 'essay') AS essay_count,
    AVG(eq.extraction_confidence)                           AS avg_confidence
FROM kg_topics t
JOIN kg_chapters c ON t.id = ANY(c.topic_ids)
LEFT JOIN exam_questions eq
    ON eq.chapter_id = c.id
    AND t.id = ANY(eq.topic_ids)
GROUP BY t.tenant_id, t.id, t.canonical_name, t.canonical_name_ar,
         t.domain, t.frequency_score, c.course_code, c.chapter_number,
         c.chapter_title, c.exam_weight_pct;
