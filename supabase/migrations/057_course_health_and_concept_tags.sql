-- ============================================================
-- Migration: 057_course_health_and_concept_tags.sql
-- Date:      2026-06-13
--
-- Two surgical additions that close critical loops:
--
--   A. concept_tags TEXT[] on learning_events
--      The missing link that enables concept_confusion_worker.
--      Without this column, workers cannot map query failures
--      to specific academic concepts. The confusion registry
--      stays empty forever.
--      Populated by search_api.py from intent.concept_tags.
--
--   B. course_health_score VIEW
--      A composite Cockpit metric answering: "How well does
--      RUMMAN cover this course, right now?"
--      Combines 4 data sources into one deployable signal:
--        - exam_questions: historical exam coverage
--        - course_behavioral_profile: live query quality
--        - kg_topics: concept map completeness
--        - concept_confusion_registry: known failure points
--      No worker needed. Self-updates as data flows in.
--
-- Safety: fully additive. ADD COLUMN IF NOT EXISTS is idempotent.
--         VIEW uses CREATE OR REPLACE — reversible.
-- ============================================================


-- ── A. concept_tags on learning_events ───────────────────────
-- The pipe between the intent classifier and the confusion registry.
--
-- Flow:
--   Student asks: "ما هي نظرية الوكالة؟"
--   → intent classifier extracts: concept_tags = ['agency_theory']
--   → search_api writes learning_events with concept_tags = ['agency_theory']
--   → if grounded=false: concept_confusion_worker sees:
--       agency_theory failed in MGT course → confusion_score +1
--
-- Without this column: workers know a query FAILED but not WHAT failed.
-- With this column: every failure is attributed to a specific concept.

ALTER TABLE learning_events
    ADD COLUMN IF NOT EXISTS concept_tags TEXT[] NOT NULL DEFAULT '{}';

COMMENT ON COLUMN learning_events.concept_tags IS
    'Normalized concept labels extracted by the intent classifier. '
    'Populated from IntentResult.concept_tags in search_api._log_learning_event(). '
    'Examples: [''agency_theory''], [''net_present_value'', ''irr''], []. '
    'Empty for procedural queries (exam_schedule, deadline, resource). '
    'This column is the primary input for concept_confusion_worker: '
    'it maps query failures (grounded=false) to specific academic concepts.';

CREATE INDEX IF NOT EXISTS idx_le_concept_tags
    ON learning_events USING GIN (concept_tags)
    WHERE array_length(concept_tags, 1) > 0;

-- For worker queries: find failed queries per concept
CREATE INDEX IF NOT EXISTS idx_le_concept_failed
    ON learning_events (grounded, occurred_at DESC)
    WHERE grounded = false AND array_length(concept_tags, 1) > 0;


-- ── B. course_health_score VIEW ───────────────────────────────
-- The executive dashboard for RUMMAN coverage quality.
--
-- health_score components:
--   exam_coverage_score    (0–40): how many exam questions do we have?
--                          40pts = 500+ questions (deep coverage)
--   corpus_coverage_score  (0–30): avg grounded success rate (from behavioral_profile)
--                          30pts = 100% grounded answers
--   topic_coverage_score   (0–20): how many canonical topics are indexed?
--                          20pts = 20+ kg_topics for this course
--   confusion_score        (0–10): inverse of critical_intersection count
--                          10pts = 0 critical unresolved concepts
--
-- total health_score = 0–100
-- Interpretation:
--   80–100 = green   (deploy-ready for this course)
--   50–79  = yellow  (partial coverage — known gaps)
--   0–49   = red     (significant gap — do not rely on RUMMAN for this course)
--
-- This VIEW is a live Cockpit panel.
-- No worker. No refresh. Self-updates as data flows.

CREATE OR REPLACE VIEW course_health_score AS
SELECT
    e.course_code,
    b.college_canon_code,
    e.exam_question_count,
    COALESCE(t.topic_count, 0)              AS indexed_topic_count,
    b.corpus_coverage_score,
    b.panic_index                           AS current_panic_index,
    COALESCE(b.query_volume_7d, 0)          AS query_volume_7d,
    b.computed_week                         AS last_snapshot_date,
    COALESCE(c.critical_count, 0)           AS critical_concepts,
    c.avg_confusion                         AS avg_confusion_score,

    LEAST(40, FLOOR(SQRT(LEAST(e.exam_question_count, 500))
                    / SQRT(500) * 40)::INT)                         AS exam_pts,
    (ROUND(COALESCE(b.corpus_coverage_score, 0) * 30))::INT         AS corpus_pts,
    LEAST(20, COALESCE(t.topic_count, 0))::INT                      AS topic_pts,
    GREATEST(0, 10 - COALESCE(c.critical_count, 0))::INT            AS confusion_pts,

    (
        LEAST(40, FLOOR(SQRT(LEAST(e.exam_question_count, 500))
                        / SQRT(500) * 40)::INT)
        + (ROUND(COALESCE(b.corpus_coverage_score, 0) * 30))::INT
        + LEAST(20, COALESCE(t.topic_count, 0))::INT
        + GREATEST(0, 10 - COALESCE(c.critical_count, 0))::INT
    )                                                               AS health_score,

    CASE
        WHEN (
            LEAST(40, FLOOR(SQRT(LEAST(e.exam_question_count, 500))
                            / SQRT(500) * 40)::INT)
            + (ROUND(COALESCE(b.corpus_coverage_score, 0) * 30))::INT
            + LEAST(20, COALESCE(t.topic_count, 0))::INT
            + GREATEST(0, 10 - COALESCE(c.critical_count, 0))::INT
        ) >= 80 THEN 'green'
        WHEN (
            LEAST(40, FLOOR(SQRT(LEAST(e.exam_question_count, 500))
                            / SQRT(500) * 40)::INT)
            + (ROUND(COALESCE(b.corpus_coverage_score, 0) * 30))::INT
            + LEAST(20, COALESCE(t.topic_count, 0))::INT
            + GREATEST(0, 10 - COALESCE(c.critical_count, 0))::INT
        ) >= 50 THEN 'yellow'
        ELSE 'red'
    END                                                             AS health_tier

FROM (
    SELECT eq.course_code,
           COUNT(*)::INT AS exam_question_count
    FROM   exam_questions eq
    WHERE  eq.course_code IS NOT NULL
      AND  eq.course_code NOT IN ('UNKNOWN','MID2023','QUIZ2021','TERM2023','CS-001','ENG-003')
    GROUP  BY eq.course_code
) e
LEFT JOIN (
    SELECT u.course_code,
           COUNT(DISTINCT kt.id)::INT AS topic_count
    FROM   kg_topics kt,
           UNNEST(kt.course_codes) AS u(course_code)
    WHERE  u.course_code IS NOT NULL
    GROUP  BY u.course_code
) t ON t.course_code = e.course_code
LEFT JOIN (
    SELECT ccr.course_code,
           COUNT(*) FILTER (WHERE ccr.critical_intersection = true) AS critical_count,
           ROUND(AVG(ccr.confusion_score)::NUMERIC, 1)              AS avg_confusion
    FROM   concept_confusion_registry ccr
    GROUP  BY ccr.course_code
) c ON c.course_code = e.course_code
LEFT JOIN LATERAL (
    SELECT cbp.corpus_coverage_score,
           cbp.panic_index,
           cbp.query_volume_7d,
           cbp.college_canon_code,
           cbp.computed_week
    FROM   course_behavioral_profile cbp
    WHERE  cbp.course_code = e.course_code
    ORDER  BY cbp.computed_week DESC
    LIMIT  1
) b ON true
ORDER BY health_score DESC NULLS LAST;

COMMENT ON VIEW course_health_score IS
    'Composite coverage quality score per course — 0 to 100. '
    'Components: exam_pts (0–40), corpus_pts (0–30), topic_pts (0–20), confusion_pts (0–10). '
    'green=80+, yellow=50-79, red=0-49. '
    'Self-updating: no worker needed. '
    'Use for: Cockpit course cards, content acquisition priority, student trust signals. '
    '"Is RUMMAN ready to handle questions about this course?" → this view answers that.';


-- ── Verification ─────────────────────────────────────────────

-- Top 20 healthiest courses:
-- SELECT course_code, college_canon_code, health_score, health_tier,
--        exam_question_count, corpus_coverage_score, indexed_topic_count
--   FROM course_health_score
--   ORDER BY health_score DESC LIMIT 20;

-- Red-tier courses (acquisition targets):
-- SELECT course_code, college_canon_code, health_score,
--        exam_question_count, indexed_topic_count
--   FROM course_health_score
--   WHERE health_tier = 'red'
--   ORDER BY exam_question_count ASC LIMIT 20;

-- ── END OF MIGRATION 057 ──────────────────────────────────────
