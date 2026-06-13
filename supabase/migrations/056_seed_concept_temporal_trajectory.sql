-- ============================================================
-- Migration: 056_seed_concept_temporal_trajectory.sql
-- Date:      2026-06-13
--
-- Purpose:
--   Seed concept_temporal_trajectory from the existing 17,195
--   exam_questions corpus (100% have topic_tags coverage).
--
-- What this produces:
--   A synthetic "Year Zero" snapshot labelled academic_year='1446',
--   semester_code='first'. Each row represents a concept extracted
--   from actual exam questions — exam_appearances = how many times
--   this concept appeared in our corpus.
--
--   confusion_score = 0.0 (no behavioral data yet — workers not run)
--   compound_score  = 0.0 (GENERATED from confusion × appearances)
--
-- Why '1446'?
--   All exam questions in the corpus are from prior academic cycles.
--   1446 is the most recent completed Hijri year. This seed represents
--   the aggregate historical exam signal — not tied to a specific semester,
--   but positioned chronologically before live data starts (1447+).
--
-- Normalization applied:
--   LOWER(TRIM()) — basic case + whitespace normalization only.
--   Arabic tags preserved. English tags lowercased.
--   Full concept normalization deferred to topic_normalizer_worker.
--
-- HAVING COUNT(*) >= 2:
--   Concepts that appeared in only one question are too sparse to be
--   meaningful as trajectory signals. The threshold excludes noise
--   without losing important recurring concepts.
--
-- Junk course codes excluded:
--   Same codes excluded in Migration 054 backfill pass.
--
-- Safety: INSERT ... ON CONFLICT DO NOTHING — idempotent.
--   Running this migration twice is safe.
-- ============================================================


INSERT INTO concept_temporal_trajectory (
    concept_name,
    course_code,
    college_canon_code,
    academic_year,
    semester_code,
    exam_appearances,
    confusion_score,
    total_queries,
    failed_queries,
    telegram_mentions,
    critical_intersection,
    trend,
    snapshot_taken_at
)
SELECT
    LOWER(TRIM(tag))            AS concept_name,
    eq.course_code              AS course_code,
    eq.college_canon_code       AS college_canon_code,
    '1446'                      AS academic_year,
    'first'                     AS semester_code,
    COUNT(*)                    AS exam_appearances,
    0.0                         AS confusion_score,
    0                           AS total_queries,
    0                           AS failed_queries,
    0                           AS telegram_mentions,
    false                       AS critical_intersection,
    'stable'                    AS trend,
    now()                       AS snapshot_taken_at
FROM
    exam_questions eq,
    UNNEST(eq.topic_tags) AS tag
WHERE
    eq.topic_tags       IS NOT NULL
    AND array_length(eq.topic_tags, 1) > 0
    AND eq.college_canon_code IS NOT NULL
    AND eq.course_code  IS NOT NULL
    AND eq.course_code  NOT IN (
        'UNKNOWN', 'MID2023', 'QUIZ2021', 'TERM2023',
        'CS-001',  'ENG-003', 'N/A',      'GENERAL'
    )
    AND TRIM(tag) != ''
GROUP BY
    LOWER(TRIM(tag)),
    eq.course_code,
    eq.college_canon_code
HAVING
    COUNT(*) >= 2
ON CONFLICT (concept_name, course_code, academic_year, semester_code, tenant_id)
DO NOTHING;


-- ── Verification ─────────────────────────────────────────────

-- Total concepts seeded:
-- SELECT COUNT(*) FROM concept_temporal_trajectory;

-- Top 20 by exam_appearances across all courses:
-- SELECT concept_name, course_code, college_canon_code, exam_appearances
--   FROM concept_temporal_trajectory
--   ORDER BY exam_appearances DESC
--   LIMIT 20;

-- College coverage — how many distinct concepts per college:
-- SELECT college_canon_code, COUNT(*) AS distinct_concepts,
--        SUM(exam_appearances) AS total_exam_hits
--   FROM concept_temporal_trajectory
--   GROUP BY college_canon_code
--   ORDER BY total_exam_hits DESC;

-- ── END OF MIGRATION 056 ──────────────────────────────────────
