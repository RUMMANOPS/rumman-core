-- Migration 058: Fix course_health_score — college_canon_code fallback
--
-- Problem: college_canon_code was sourced only from course_behavioral_profile.
-- Courses with no behavioral profile row (e.g. ECON101, LAW101, ISLM*) show NULL
-- even though exam_questions already has their college_canon_code backfilled (migration 054).
--
-- Fix: COALESCE(b.college_canon_code, eq_college.college_canon_code)
-- where eq_college is a LEFT JOIN on exam_questions grouped by course_code.

CREATE OR REPLACE VIEW course_health_score AS
SELECT
    e.course_code,
    COALESCE(b.college_canon_code, eq_college.college_canon_code) AS college_canon_code,
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
LEFT JOIN (
    SELECT eq2.course_code,
           MAX(eq2.college_canon_code) AS college_canon_code
    FROM   exam_questions eq2
    WHERE  eq2.college_canon_code IS NOT NULL
    GROUP  BY eq2.course_code
) eq_college ON eq_college.course_code = e.course_code
ORDER BY health_score DESC NULLS LAST;

COMMENT ON VIEW course_health_score IS
    'Composite coverage quality score per course — 0 to 100. '
    'Components: exam_pts (0–40), corpus_pts (0–30), topic_pts (0–20), confusion_pts (0–10). '
    'green=80+, yellow=50-79, red=0-49. '
    'college_canon_code: course_behavioral_profile first, exam_questions fallback. '
    'Self-updating: no worker needed. '
    'Use for: Cockpit course cards, content acquisition priority, student trust signals. '
    '"Is RUMMAN ready to handle questions about this course?" → this view answers that.';
