-- Migration 003: Knowledge Layer
-- Adds: source_documents, document_chunks (pgvector), courses, course_prerequisites
-- Run in Supabase SQL editor after enabling the vector extension.
--
-- Prerequisites: pgvector extension must be enabled in your Supabase project.
-- Dashboard → Database → Extensions → search "vector" → enable.

-- ─── source_documents ────────────────────────────────────────────────────────
-- One row per ingested file. Tracks origin, extraction status, and metadata.
-- Workers read this table to find work; embed_worker writes chunks referencing it.

CREATE TABLE IF NOT EXISTS source_documents (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    content_hash        text UNIQUE NOT NULL,   -- sha256 hex of raw file bytes (dedup guard)
    storage_path        text,                   -- Supabase Storage path, null for direct-text seeds
    file_name           text,
    mime_type           text,
    file_size_bytes     bigint,

    source_type         text NOT NULL
                        CHECK (source_type IN (
                            'exam',                 -- past exam paper
                            'study_plan',           -- degree plan PDF
                            'regulation',           -- university regulation doc
                            'course_description',   -- seeded directly from JSON (no PDF)
                            'telegram_export',      -- Telegram JSON export
                            'upload'                -- generic admin upload
                        )),

    institution         text NOT NULL DEFAULT 'SEU',
    course_code         text,
    exam_type           text CHECK (exam_type IN ('final', 'midterm', 'quiz', NULL)),
    academic_year       text,
    semester            text CHECK (semester IN ('first', 'second', 'summer', NULL)),
    professor           text,
    language            text CHECK (language IN ('ar', 'en', 'mixed', NULL)),

    page_count          int,
    extraction_method   text CHECK (extraction_method IN (
                            'digital', 'ocr_vision', 'mixed', 'direct_text', NULL
                        )),
    extracted_text      text,
    ocr_confidence      float,

    processing_status   text NOT NULL DEFAULT 'pending'
                        CHECK (processing_status IN (
                            'pending', 'extracting', 'extracted',
                            'chunking', 'chunked', 'failed'
                        )),
    error               text,

    tenant_id           uuid,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_source_documents_course
    ON source_documents(course_code) WHERE course_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_source_documents_status
    ON source_documents(processing_status);
CREATE INDEX IF NOT EXISTS idx_source_documents_type
    ON source_documents(source_type, institution);

-- ─── document_chunks ─────────────────────────────────────────────────────────
-- One row per text chunk. Holds the embedding for vector similarity search.
-- Metadata is denormalised here for fast filtered retrieval — don't normalise it.

CREATE TABLE IF NOT EXISTS document_chunks (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_document_id  uuid REFERENCES source_documents(id) ON DELETE CASCADE,

    content             text NOT NULL,
    embedding           vector(1536),       -- text-embedding-3-large at 1536 dims (pgvector HNSW max is 2000)

    -- Academic metadata (denormalised — fast filter without join)
    institution         text NOT NULL DEFAULT 'SEU',
    course_code         text,
    source_type         text NOT NULL,
    exam_type           text,
    academic_year       text,
    semester            text,
    professor           text,
    language            text,

    chunk_index         int NOT NULL DEFAULT 0,
    total_chunks        int NOT NULL DEFAULT 1,
    ocr_confidence      float,

    content_date        timestamptz,
    ingested_at         timestamptz NOT NULL DEFAULT now(),

    tenant_id           uuid
);

-- HNSW index — build once now, stays current as rows are inserted.
-- m=16, ef_construction=64 is the pgvector recommended default for most workloads.
CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding
    ON document_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_document_chunks_course
    ON document_chunks(course_code) WHERE course_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_document_chunks_type
    ON document_chunks(source_type, institution);

-- ─── courses ─────────────────────────────────────────────────────────────────
-- Relational reference data parsed from official study plans.
-- Do NOT embed this table — it is looked up by exact course_code, not by similarity.

CREATE TABLE IF NOT EXISTS courses (
    course_code         text NOT NULL,
    institution         text NOT NULL DEFAULT 'SEU',
    PRIMARY KEY (course_code, institution),

    course_title        text NOT NULL,
    course_title_ar     text,
    credit_hours        int,
    level               int,            -- academic level 1–8
    program             text,           -- e.g. 'BSBA_MGT', 'BSCS'
    college             text,
    college_ar          text,
    description         text,           -- official course description
    language            text DEFAULT 'en',

    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_courses_program
    ON courses(program, institution);
CREATE INDEX IF NOT EXISTS idx_courses_level
    ON courses(institution, level);

-- ─── course_prerequisites ────────────────────────────────────────────────────
-- Directed edges in the prerequisite graph.
-- course_code REQUIRES prereq_code before it can be taken.

CREATE TABLE IF NOT EXISTS course_prerequisites (
    course_code         text NOT NULL,
    institution         text NOT NULL DEFAULT 'SEU',
    prereq_code         text NOT NULL,
    prereq_institution  text NOT NULL DEFAULT 'SEU',

    PRIMARY KEY (course_code, institution, prereq_code, prereq_institution),
    FOREIGN KEY (course_code, institution)
        REFERENCES courses(course_code, institution),
    FOREIGN KEY (prereq_code, prereq_institution)
        REFERENCES courses(course_code, institution)
);

CREATE INDEX IF NOT EXISTS idx_prereqs_course
    ON course_prerequisites(course_code, institution);
CREATE INDEX IF NOT EXISTS idx_prereqs_prereq
    ON course_prerequisites(prereq_code, prereq_institution);

-- ─── match_course_chunks ─────────────────────────────────────────────────────
-- Vector similarity search scoped to a single course.
-- Called by query_handler.py for exam/description content retrieval.

CREATE OR REPLACE FUNCTION match_course_chunks(
    query_embedding     vector(1536),
    p_course_code       text,
    p_institution       text DEFAULT 'SEU',
    match_count         int DEFAULT 20,
    min_similarity      float DEFAULT 0.25
)
RETURNS TABLE (
    id                  uuid,
    content             text,
    source_type         text,
    exam_type           text,
    academic_year       text,
    semester            text,
    professor           text,
    chunk_index         int,
    similarity          float
)
LANGUAGE SQL STABLE AS $$
    SELECT
        id, content, source_type, exam_type,
        academic_year, semester, professor, chunk_index,
        1 - (embedding <=> query_embedding) AS similarity
    FROM document_chunks
    WHERE
        institution = p_institution
        AND course_code = p_course_code
        AND embedding IS NOT NULL
        AND 1 - (embedding <=> query_embedding) > min_similarity
    ORDER BY embedding <=> query_embedding
    LIMIT match_count;
$$;

-- ─── match_chunks_general ────────────────────────────────────────────────────
-- Vector similarity search not scoped to a course.
-- Used for regulation / policy queries where course_code is unknown or irrelevant.

CREATE OR REPLACE FUNCTION match_chunks_general(
    query_embedding     vector(1536),
    p_institution       text DEFAULT 'SEU',
    p_source_type       text DEFAULT NULL,
    match_count         int DEFAULT 15,
    min_similarity      float DEFAULT 0.25
)
RETURNS TABLE (
    id                  uuid,
    content             text,
    source_type         text,
    course_code         text,
    similarity          float
)
LANGUAGE SQL STABLE AS $$
    SELECT
        id, content, source_type, course_code,
        1 - (embedding <=> query_embedding) AS similarity
    FROM document_chunks
    WHERE
        institution = p_institution
        AND (p_source_type IS NULL OR source_type = p_source_type)
        AND embedding IS NOT NULL
        AND 1 - (embedding <=> query_embedding) > min_similarity
    ORDER BY embedding <=> query_embedding
    LIMIT match_count;
$$;

-- ─── course_intelligence (view) ───────────────────────────────────────────────
-- Enriched course view: adds downstream_count (how many later courses depend on
-- this one) and prerequisite list. Used by query_handler to surface high-impact
-- course warnings without additional queries.

CREATE OR REPLACE VIEW course_intelligence AS
SELECT
    c.course_code,
    c.institution,
    c.course_title,
    c.credit_hours,
    c.level,
    c.program,
    c.college,
    c.description,
    -- Courses that directly depend on this one (blocked if this is failed)
    COUNT(DISTINCT down.course_code)::int                                   AS downstream_count,
    -- What this course requires
    ARRAY_AGG(DISTINCT up.prereq_code)
        FILTER (WHERE up.prereq_code IS NOT NULL)                           AS prerequisite_codes,
    -- What depends on this course
    ARRAY_AGG(DISTINCT down.course_code)
        FILTER (WHERE down.course_code IS NOT NULL)                         AS blocks_codes
FROM courses c
LEFT JOIN course_prerequisites up
    ON  up.course_code   = c.course_code
    AND up.institution   = c.institution
LEFT JOIN course_prerequisites down
    ON  down.prereq_code        = c.course_code
    AND down.prereq_institution = c.institution
GROUP BY
    c.course_code, c.institution, c.course_title, c.credit_hours,
    c.level, c.program, c.college, c.description;
