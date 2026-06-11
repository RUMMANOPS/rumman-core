-- Migration 038: Question Clusters + Coverage Stats
--
-- kg_question_clusters  — groups near-duplicate questions across exam years/sources
--                         A question that appears in 4 different midterms = high-priority
-- kg_chapter_stats      — materialized: chapter coverage heatmap, updated by worker
-- Adds cluster_id FK to exam_questions

-- ---------------------------------------------------------------------------
-- kg_question_clusters — recurring question detection
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS kg_question_clusters (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    course_code             TEXT        NOT NULL,
    exam_type               TEXT,                       -- 'midterm' | 'final' — null = mixed
    canonical_question_id   UUID        REFERENCES exam_questions(id) ON DELETE SET NULL,
        -- The highest-confidence version of the question
    member_question_ids     UUID[]      NOT NULL DEFAULT '{}',
        -- All near-duplicate question IDs in this cluster
    min_similarity          FLOAT,                      -- lowest cosine sim within cluster
    occurrence_count        INT         NOT NULL DEFAULT 1,
        -- How many distinct source documents contain this question
    is_recurring            BOOLEAN     NOT NULL DEFAULT false,
        -- true if seen in 2+ distinct academic years
    years                   TEXT[]      NOT NULL DEFAULT '{}',
        -- ['2023-2024', '2024-2025'] — years this question has appeared
    chapter_numbers         INT[],                      -- shared chapter attribution
    topic_ids               UUID[]      NOT NULL DEFAULT '{}',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT kqc_similarity_range CHECK (
        min_similarity IS NULL OR min_similarity BETWEEN 0 AND 1
    )
);

CREATE INDEX IF NOT EXISTS kqc_course_idx
    ON kg_question_clusters (tenant_id, course_code, is_recurring);

CREATE INDEX IF NOT EXISTS kqc_members_gin_idx
    ON kg_question_clusters USING GIN (member_question_ids);

CREATE INDEX IF NOT EXISTS kqc_topic_ids_gin_idx
    ON kg_question_clusters USING GIN (topic_ids);

-- ---------------------------------------------------------------------------
-- Link exam_questions to their cluster
-- ---------------------------------------------------------------------------

ALTER TABLE exam_questions
    ADD COLUMN IF NOT EXISTS cluster_id UUID REFERENCES kg_question_clusters(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS eq_cluster_idx
    ON exam_questions (cluster_id)
    WHERE cluster_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- kg_chapter_stats — materialized chapter coverage (refreshed by worker)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS kg_chapter_stats (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    course_code         TEXT        NOT NULL,
    chapter_id          UUID        REFERENCES kg_chapters(id) ON DELETE CASCADE,
    chapter_number      INT         NOT NULL,

    -- Coverage signals
    total_questions     INT         NOT NULL DEFAULT 0,
    recurring_questions INT         NOT NULL DEFAULT 0,   -- questions in clusters
    mcq_count           INT         NOT NULL DEFAULT 0,
    essay_count         INT         NOT NULL DEFAULT 0,
    avg_confidence      FLOAT,

    -- Exam-type breakdown (JSON for flexibility: {"midterm": 12, "final": 8})
    by_exam_type        JSONB       NOT NULL DEFAULT '{}',

    -- Derived importance signal
    exam_weight_pct     FLOAT,                            -- % of total course questions
    is_high_frequency   BOOLEAN     NOT NULL DEFAULT false,-- top 25% by question count

    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (course_code, chapter_number)
);

CREATE INDEX IF NOT EXISTS kcs_course_idx
    ON kg_chapter_stats (tenant_id, course_code);

CREATE INDEX IF NOT EXISTS kcs_weight_idx
    ON kg_chapter_stats (tenant_id, course_code, exam_weight_pct DESC NULLS LAST);

-- ---------------------------------------------------------------------------
-- Refresh function — called by chapter_attribution_worker after each batch
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION refresh_chapter_stats(p_course_code TEXT DEFAULT NULL)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    -- Delete and recompute for given course (or all if NULL)
    DELETE FROM kg_chapter_stats
    WHERE p_course_code IS NULL OR course_code = p_course_code;

    INSERT INTO kg_chapter_stats (
        tenant_id, course_code, chapter_id, chapter_number,
        total_questions, recurring_questions, mcq_count, essay_count,
        avg_confidence, by_exam_type, exam_weight_pct, is_high_frequency
    )
    SELECT
        eq.tenant_id,
        eq.course_code,
        eq.chapter_id,
        c.chapter_number,
        COUNT(eq.id)                                            AS total_questions,
        COUNT(eq.id) FILTER (WHERE eq.cluster_id IS NOT NULL)   AS recurring_questions,
        COUNT(eq.id) FILTER (WHERE eq.question_type = 'mcq')    AS mcq_count,
        COUNT(eq.id) FILTER (WHERE eq.question_type = 'essay')  AS essay_count,
        AVG(eq.extraction_confidence)                           AS avg_confidence,
        jsonb_object_agg(
            COALESCE(eq.exam_type, 'unknown'),
            cnt
        )                                                       AS by_exam_type,
        ROUND(
            COUNT(eq.id)::numeric /
            NULLIF(SUM(COUNT(eq.id)) OVER (PARTITION BY eq.tenant_id, eq.course_code), 0) * 100,
            2
        )                                                       AS exam_weight_pct,
        false                                                   AS is_high_frequency
    FROM exam_questions eq
    JOIN kg_chapters c ON c.id = eq.chapter_id
    CROSS JOIN LATERAL (
        SELECT eq.exam_type, COUNT(*) AS cnt
        FROM exam_questions eq2
        WHERE eq2.chapter_id = eq.chapter_id
        GROUP BY eq2.exam_type
    ) et
    WHERE eq.chapter_id IS NOT NULL
      AND (p_course_code IS NULL OR eq.course_code = p_course_code)
    GROUP BY eq.tenant_id, eq.course_code, eq.chapter_id, c.chapter_number;

    -- Mark top 25% by question count as high-frequency
    UPDATE kg_chapter_stats cs
    SET is_high_frequency = true
    WHERE cs.total_questions >= (
        SELECT PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY total_questions)
        FROM kg_chapter_stats cs2
        WHERE cs2.course_code = cs.course_code
    )
    AND (p_course_code IS NULL OR cs.course_code = p_course_code);
END;
$$;
