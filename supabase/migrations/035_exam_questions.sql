-- Migration 035: exam_questions — structured question bank
--
-- Context:
--   document_chunks stores raw text blocks. There is no question-level granularity:
--   no boundaries between questions, no chapter attribution, no question type.
--   exam_intelligence stores course-level topic summaries only.
--
--   This table is the foundation for:
--     - Chapter-filtered exam prep ("show me questions from chapters 1–5 only")
--     - Adaptive practice sessions (MCQ auto-graded, essay hints)
--     - Pattern analytics (which topics appear most per course/exam_type)
--     - Study Mode: "اختبر نفسك" with real historical questions
--
--   Populated by: app/question_extraction_worker.py (gpt-4o per exam document)
--   Read by:      search_api.py + mobile app via REST
--
-- Two-pass extraction design:
--   Pass 1 (this worker): question text + type + topic_tags + course attribution
--   Pass 2 (future chapter_attribution_worker): chapter_numbers + chapter_verified
--   This lets us launch with questions before syllabi are fully indexed.

-- ---------------------------------------------------------------------------
-- Track extraction progress on source_documents
-- ---------------------------------------------------------------------------

ALTER TABLE source_documents
    ADD COLUMN IF NOT EXISTS question_extraction_status TEXT
        DEFAULT 'pending'
        CHECK (question_extraction_status IN (
            'pending',   -- not yet processed
            'running',   -- worker has claimed this document
            'completed', -- questions extracted and stored
            'failed',    -- extraction failed (check error_message)
            'skipped'    -- non-exam document or no extractable questions
        ));

-- Fast poll: find unprocessed exam documents
CREATE INDEX IF NOT EXISTS sd_qe_pending_idx
    ON source_documents (tenant_id, question_extraction_status)
    WHERE source_type = 'exam' AND question_extraction_status = 'pending';

-- ---------------------------------------------------------------------------
-- exam_questions: one row per extracted question
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS exam_questions (
    id                    UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID         NOT NULL,

    -- Academic context
    course_code           TEXT         NOT NULL,
    exam_type             TEXT         NOT NULL DEFAULT 'general',
        -- 'midterm' | 'final' | 'quiz' | 'general'
    exam_year             TEXT,
        -- e.g. '2024-2025' or '1446' — extracted from document metadata
    exam_semester         TEXT,
        -- 'first' | 'second' | 'summer' — extracted from document metadata

    -- Chapter attribution (populated by chapter_attribution_worker — Phase 2)
    chapter_numbers       INT[],
        -- [1, 3] — which course chapters this question covers. NULL until Phase 2.
    topic_tags            TEXT[]       NOT NULL DEFAULT '{}',
        -- ['نظرية الألعاب', 'تحليل الحساسية'] — extracted in Pass 1

    -- Question content
    question_text         TEXT         NOT NULL,
    question_type         TEXT         NOT NULL DEFAULT 'unknown',
        -- 'mcq' | 'essay' | 'calculation' | 'true_false' | 'unknown'
    answer_options        JSONB,
        -- MCQ only: [{"key": "أ", "text": "..."}, {"key": "ب", "text": "..."}]
    model_answer          TEXT,
        -- If answer key is embedded in the source document

    -- Source provenance
    source_document_id    UUID         REFERENCES source_documents(id)
                              ON DELETE SET NULL,
    source_message_id     UUID         REFERENCES messages(id)
                              ON DELETE SET NULL,

    -- Quality signals
    extraction_confidence FLOAT        NOT NULL DEFAULT 0.5,
        -- How confident GPT-4o is in the extraction quality (0.0–1.0)
    attribution_verified  BOOLEAN      NOT NULL DEFAULT false,
        -- true when course_code confirmed by second model pass
    chapter_verified      BOOLEAN      NOT NULL DEFAULT false,
        -- true when chapter_numbers confirmed by chapter_attribution_worker

    extracted_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT eq_exam_type_check
        CHECK (exam_type IN ('midterm', 'final', 'quiz', 'general')),
    CONSTRAINT eq_question_type_check
        CHECK (question_type IN ('mcq', 'essay', 'calculation', 'true_false', 'unknown')),
    CONSTRAINT eq_confidence_range
        CHECK (extraction_confidence >= 0 AND extraction_confidence <= 1)
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

-- Primary read path: course + exam type (e.g. all MGT312 final questions)
CREATE INDEX IF NOT EXISTS eq_course_exam_idx
    ON exam_questions (tenant_id, course_code, exam_type);

-- Chapter-filtered queries: "chapters 1-5 only" (GIN for array containment)
CREATE INDEX IF NOT EXISTS eq_chapters_gin_idx
    ON exam_questions USING GIN (chapter_numbers);

-- Topic tag search (GIN for array overlap)
CREATE INDEX IF NOT EXISTS eq_topics_gin_idx
    ON exam_questions USING GIN (topic_tags);

-- Dedup guard: prevent re-extracting same document
CREATE INDEX IF NOT EXISTS eq_source_doc_idx
    ON exam_questions (source_document_id)
    WHERE source_document_id IS NOT NULL;

-- Quality filtering: only verified questions for production queries
CREATE INDEX IF NOT EXISTS eq_verified_idx
    ON exam_questions (tenant_id, course_code, attribution_verified);

-- ---------------------------------------------------------------------------
-- View: high-quality question counts per course (for app display)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW exam_question_stats AS
SELECT
    tenant_id,
    course_code,
    exam_type,
    COUNT(*)                                           AS total_questions,
    COUNT(*) FILTER (WHERE attribution_verified)       AS verified_questions,
    COUNT(*) FILTER (WHERE chapter_verified)           AS chapter_tagged_questions,
    COUNT(*) FILTER (WHERE question_type = 'mcq')      AS mcq_count,
    COUNT(*) FILTER (WHERE question_type = 'essay')    AS essay_count,
    AVG(extraction_confidence)                         AS avg_confidence,
    MAX(extracted_at)                                  AS last_extracted_at
FROM exam_questions
GROUP BY tenant_id, course_code, exam_type;
