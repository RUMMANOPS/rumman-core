-- ============================================================
-- Migration: 055_canon_propagation_behavioral.sql
-- Date:      2026-06-13
--
-- Purpose:
--   A. Canon Propagation — extend college_canon_code FK to the
--      two remaining behavioral intelligence tables:
--        • course_behavioral_profile
--        • concept_confusion_registry
--      Both backfilled via resolve_course_to_college() RPC.
--
--   B. Fix college_exam_coverage VIEW — CTE rewrite to avoid
--      PostgREST statement timeout. Old view did a full-table
--      cross-join; new version pre-aggregates with CTEs first.
--
--   C. Compounding Assets — four new structures that encode
--      institutional intelligence that compounds over time
--      and cannot be replicated by any new entrant:
--
--   1. concept_temporal_trajectory TABLE
--      The highest-value asset RUMMAN will ever build.
--      One row per (concept × course × academic_year × semester).
--      Year 1 = behavioral snapshot. Year 3 = trajectory.
--      Year 5 = prediction with statistical confidence.
--      No competitor can buy this. The moat IS the history.
--
--   2. institutional_behavioral_clock VIEW
--      Aggregated panic_index across ALL courses by week.
--      Not the official calendar — the actual behavioral fingerprint
--      of the university as experienced by its students.
--      Requires multi-course, multi-year data to mean anything.
--
--   3. college_knowledge_gap VIEW
--      The negative space as a product.
--      acquisition_priority_score = faculty headcount / exam coverage.
--      Tells the content team exactly where to invest next.
--
--   4. concept_cooccurrence_log TABLE
--      When two concepts are queried in the same session, that
--      co-occurrence is a cognitive link not in any textbook.
--      Primed to accumulate on next search_api integration.
--      After 100K sessions: a cognitive graph of the university.
--
-- Safety: 100% additive. No tables dropped. No columns removed.
--   DROP/CREATE on VIEWs only — fully reversible.
--   All backfill via existing resolve_course_to_college() RPC.
-- ============================================================


-- ── A.1 Canon Propagation: course_behavioral_profile ─────────

ALTER TABLE course_behavioral_profile
    ADD COLUMN IF NOT EXISTS college_canon_code TEXT
        REFERENCES seu_colleges_canon(internal_code);

COMMENT ON COLUMN course_behavioral_profile.college_canon_code IS
    'Canon college identifier (ADMIN/COMP/HEALTH/THEO/GENERAL/APPLIED). '
    'Resolved via resolve_course_to_college() — FK chain first, prefix fallback second. '
    'NULL only for courses with unrecognizable code patterns. '
    'Populated on INSERT and backfilled at migration time.';

CREATE INDEX IF NOT EXISTS idx_cbp_college_canon
    ON course_behavioral_profile (college_canon_code, computed_week DESC)
    WHERE college_canon_code IS NOT NULL;

-- Backfill existing rows
UPDATE course_behavioral_profile cbp
SET college_canon_code = resolve_course_to_college(cbp.course_code)
WHERE cbp.college_canon_code IS NULL
  AND cbp.course_code IS NOT NULL;


-- ── A.2 Canon Propagation: concept_confusion_registry ────────

ALTER TABLE concept_confusion_registry
    ADD COLUMN IF NOT EXISTS college_canon_code TEXT
        REFERENCES seu_colleges_canon(internal_code);

COMMENT ON COLUMN concept_confusion_registry.college_canon_code IS
    'Canon college identifier for the course this concept belongs to. '
    'Enables: "Which college has the most unresolved confusion?" — '
    'a question that crosses behavioral data with institutional structure. '
    'Resolved via resolve_course_to_college(course_code).';

CREATE INDEX IF NOT EXISTS idx_ccr_college_canon
    ON concept_confusion_registry (college_canon_code, confusion_score DESC)
    WHERE college_canon_code IS NOT NULL AND critical_intersection = true;

-- Backfill existing rows
UPDATE concept_confusion_registry ccr
SET college_canon_code = resolve_course_to_college(ccr.course_code)
WHERE ccr.college_canon_code IS NULL
  AND ccr.course_code IS NOT NULL;


-- ── B. Fix college_exam_coverage VIEW (CTE rewrite) ──────────
-- Original view caused PostgREST statement timeout:
--   LEFT JOIN exam_questions + source_documents at full-table scale
--   violated the default statement_timeout.
-- Fix: pre-aggregate each table independently in CTEs, then join.
-- Each CTE is a GROUP BY on an indexed column → much cheaper.

DROP VIEW IF EXISTS college_exam_coverage;

CREATE OR REPLACE VIEW college_exam_coverage AS
WITH exam_agg AS (
    -- Pre-aggregate: one row per college from exam_questions
    SELECT
        college_canon_code,
        COUNT(*)                                            AS question_count,
        COUNT(DISTINCT course_code)                         AS distinct_courses,
        COUNT(*) FILTER (WHERE college_canon_method = 'inst_courses_fk') AS fk_resolved,
        COUNT(*) FILTER (WHERE college_canon_method = 'prefix_map')      AS prefix_resolved
    FROM exam_questions
    WHERE college_canon_code IS NOT NULL
    GROUP BY college_canon_code
),
doc_agg AS (
    -- Pre-aggregate: one row per college from source_documents
    SELECT
        college_canon_code,
        COUNT(*)                AS doc_count,
        COUNT(DISTINCT source_type) AS source_types
    FROM source_documents
    WHERE college_canon_code IS NOT NULL
    GROUP BY college_canon_code
),
total_questions AS (
    SELECT SUM(question_count) AS n FROM exam_agg
)
SELECT
    c.internal_code,
    c.name_ar,
    COALESCE(e.question_count,    0) AS exam_question_count,
    COALESCE(e.distinct_courses,  0) AS courses_with_exams,
    COALESCE(e.fk_resolved,       0) AS fk_resolved_count,
    COALESCE(e.prefix_resolved,   0) AS prefix_resolved_count,
    COALESCE(d.doc_count,         0) AS source_doc_count,
    COALESCE(d.source_types,      0) AS source_type_count,
    CASE
        WHEN tq.n IS NULL OR tq.n = 0 THEN 0.0
        ELSE ROUND(COALESCE(e.question_count, 0)::NUMERIC / tq.n * 100.0, 1)
    END AS pct_of_all_questions
FROM seu_colleges_canon c
CROSS JOIN total_questions tq
LEFT JOIN exam_agg e ON e.college_canon_code = c.internal_code
LEFT JOIN doc_agg  d ON d.college_canon_code = c.internal_code
ORDER BY COALESCE(e.question_count, 0) DESC;

COMMENT ON VIEW college_exam_coverage IS
    'Per-college exam question and document coverage — CTE-optimized to avoid timeout. '
    'Each CTE pre-aggregates independently (one pass each) before joining to colleges. '
    'pct_of_all_questions: share of the 17K question corpus owned by this college. '
    'courses_with_exams: distinct course_code values that contributed questions. '
    'Use this view to identify acquisition gaps (colleges with 0 questions = no coverage).';


-- ── C.1 concept_temporal_trajectory TABLE ────────────────────
-- The primary compounding asset.
--
-- How it works:
--   concept_confusion_worker reads concept_confusion_registry,
--   then writes one snapshot row per (concept × course × semester).
--
-- Why it compounds:
--   Year 1: "Corporate Governance confusion_score=73 this semester"
--   Year 2: "Same concept: confusion_score=81, yoy_confusion_delta=+8, trend=rising"
--   Year 3: "3 consecutive semesters critical, exam_appearances=5, compound_score=40.5"
--   Year 5: compound_score trajectory predicts next semester with >80% confidence
--
-- Why no one can replicate it:
--   It requires 5 years of behavioral data that only existed because
--   we started collecting in Year 1. Buying this would mean buying
--   the entire historical user population — impossible.
--
-- compound_score formula:
--   confusion_score (0–100) × exam_appearances × 0.1
--   confusion=90, appearances=5 → compound_score=45
--   confusion=50, appearances=2 → compound_score=10  ← critical_intersection floor

CREATE TABLE IF NOT EXISTS concept_temporal_trajectory (
    id                      BIGSERIAL   PRIMARY KEY,
    tenant_id               UUID        NOT NULL
                            DEFAULT '00000000-0000-0000-0000-000000000001',

    -- Concept identity
    concept_name            TEXT        NOT NULL,
    course_code             TEXT        NOT NULL,
    college_canon_code      TEXT
        REFERENCES seu_colleges_canon(internal_code) ON DELETE SET NULL,

    -- Academic time slice
    academic_year           TEXT        NOT NULL,
        -- Hijri year string, e.g. '1447'. Matches academic_calendar.academic_year.
    semester_code           TEXT        NOT NULL
        CHECK (semester_code IN ('first', 'second', 'summer')),

    -- Snapshot of concept_confusion_registry at this point in time
    confusion_score         FLOAT       NOT NULL DEFAULT 0.0
        CHECK (confusion_score BETWEEN 0 AND 100),
    exam_appearances        INT         NOT NULL DEFAULT 0,
        -- distinct exam files (or years) this concept appeared in
    total_queries           INT         NOT NULL DEFAULT 0,
    failed_queries          INT         NOT NULL DEFAULT 0,
    telegram_mentions       INT         NOT NULL DEFAULT 0,
    critical_intersection   BOOLEAN     NOT NULL DEFAULT false,
    trend                   TEXT        NOT NULL DEFAULT 'stable'
        CHECK (trend IN ('rising', 'stable', 'falling')),

    -- The compound momentum score for this semester.
    -- confusion × recurrence / 10 → normalized significance.
    -- GENERATED ALWAYS keeps it consistent — never manually set.
    compound_score          FLOAT       GENERATED ALWAYS AS
        (confusion_score * GREATEST(exam_appearances, 1) * 0.1) STORED,

    -- Year-over-year delta (NULL until second year of data)
    yoy_confusion_delta     FLOAT,          -- positive = getting worse
    yoy_exam_delta          INT,            -- positive = appearing more in exams

    snapshot_taken_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (concept_name, course_code, academic_year, semester_code, tenant_id)
);

COMMENT ON TABLE concept_temporal_trajectory IS
    'Historical time series of concept importance, one row per '
    '(concept × course × academic_year × semester). '
    'The moat: after 3 years this table predicts exam topics with trajectory data. '
    'After 5 years it predicts with statistical confidence. '
    'Populated by concept_confusion_worker at semester snapshots. '
    'compound_score = confusion_score × exam_appearances × 0.1 (generated, never stale). '
    'yoy_* columns are NULL in year 1, populated from year 2 onward.';

CREATE INDEX IF NOT EXISTS idx_ctt_concept_course
    ON concept_temporal_trajectory (concept_name, course_code, academic_year DESC);

-- Cockpit: highest compound momentum concepts this year
CREATE INDEX IF NOT EXISTS idx_ctt_college_year_compound
    ON concept_temporal_trajectory (college_canon_code, academic_year, compound_score DESC)
    WHERE college_canon_code IS NOT NULL;

-- Rising critical concepts — the early warning signal
CREATE INDEX IF NOT EXISTS idx_ctt_rising_critical
    ON concept_temporal_trajectory (trend, compound_score DESC)
    WHERE critical_intersection = true AND trend = 'rising';

-- Year-over-year analysis
CREATE INDEX IF NOT EXISTS idx_ctt_yoy
    ON concept_temporal_trajectory (academic_year, semester_code, yoy_confusion_delta DESC)
    WHERE yoy_confusion_delta IS NOT NULL;


-- ── C.2 institutional_behavioral_clock VIEW ──────────────────
-- Aggregates panic_index across ALL courses by week.
-- Reveals the behavioral fingerprint of SEU as a system.
--
-- The official calendar tells you when exams are scheduled.
-- This view tells you when students actually start panicking.
-- The gap between the two is itself a dataset (procrastination curve).
--
-- As course_behavioral_profile grows (more courses, more weeks),
-- this view reveals university-wide patterns invisible at course level:
--   • Which weeks have simultaneous pressure across colleges?
--   • Does ADMIN always panic 2 weeks before COMP?
--   • Does the summer semester produce higher panic than semester 1?

CREATE OR REPLACE VIEW institutional_behavioral_clock AS
WITH weekly_agg AS (
    SELECT
        computed_week,
        COUNT(DISTINCT course_code)                                         AS active_courses,
        COUNT(DISTINCT college_canon_code)
            FILTER (WHERE college_canon_code IS NOT NULL)                   AS colleges_active,
        ROUND(AVG(panic_index)::NUMERIC, 3)                                 AS avg_panic_index,
        MAX(panic_index)                                                     AS peak_panic_index,
        MIN(panic_index)                                                     AS floor_panic_index,
        SUM(query_volume_7d)                                                 AS total_query_volume_7d,
        COUNT(*) FILTER (WHERE panic_index > 2.0)                           AS courses_in_exam_panic,
        COUNT(*) FILTER (WHERE panic_index < 0.5)                           AS courses_idle,
        COUNT(*) FILTER (WHERE corpus_coverage_score < 0.5)                 AS courses_with_coverage_gap,
        ROUND(AVG(corpus_coverage_score)::NUMERIC, 3)                       AS avg_corpus_coverage
    FROM course_behavioral_profile
    GROUP BY computed_week
)
SELECT
    wa.computed_week,
    wa.active_courses,
    wa.colleges_active,
    wa.avg_panic_index,
    wa.peak_panic_index,
    wa.floor_panic_index,
    wa.total_query_volume_7d,
    wa.courses_in_exam_panic,
    wa.courses_idle,
    wa.courses_with_coverage_gap,
    wa.avg_corpus_coverage,
    -- Academic calendar context: what's happening this week officially?
    cal.academic_year,
    cal.semester,
    cal.event_type        AS calendar_event_type,
    cal.event_name_ar     AS calendar_event_ar,
    cal.event_name_en     AS calendar_event_en
FROM weekly_agg wa
LEFT JOIN LATERAL (
    -- Find the most relevant calendar event for this week.
    -- Priority: exams > instruction > registration > other.
    SELECT academic_year, semester, event_type, event_name_ar, event_name_en
    FROM academic_calendar
    WHERE wa.computed_week BETWEEN start_date AND end_date
    ORDER BY
        CASE event_type
            WHEN 'final_exams'        THEN 1
            WHEN 'midterm_exams'      THEN 2
            WHEN 'instruction'        THEN 3
            WHEN 'add_drop'           THEN 4
            WHEN 'course_registration' THEN 5
            ELSE 6
        END
    LIMIT 1
) cal ON true
ORDER BY wa.computed_week DESC;

COMMENT ON VIEW institutional_behavioral_clock IS
    'University-wide behavioral fingerprint by week. '
    'avg_panic_index > 2.0 = university-wide exam pressure. '
    'courses_in_exam_panic = how many courses are simultaneously cramming. '
    'Joined to academic_calendar to show official context vs actual behavior. '
    'The divergence between panic timing and exam timing = procrastination signal. '
    'Grows in value proportionally with the number of courses tracked. '
    'With 3+ years of data: predicts which weeks will be critical before the semester starts.';


-- ── C.3 college_knowledge_gap VIEW ───────────────────────────
-- The negative space as a product.
--
-- RUMMAN knows what it knows. More importantly: it knows what it doesn't.
-- This view quantifies the gap per college so the team can act on it.
--
-- acquisition_priority_score:
--   faculty_count HIGH + exam_questions LOW = highest gap
--   formula: (faculty_count * 100) / NULLIF(exam_question_count, 0)
--   A college with 20 faculty and 0 exams = infinite priority → shown as 9999.
--
-- Why this is itself a compounding asset:
--   As gaps get filled, acquisition_priority_score drops for that college.
--   The VIEW becomes a live dashboard of how RUMMAN is closing its blind spots.
--   When all colleges are below score 5 → RUMMAN has comprehensive coverage.

CREATE OR REPLACE VIEW college_knowledge_gap AS
WITH
exam_counts AS (
    SELECT
        college_canon_code,
        COUNT(*)                        AS exam_question_count,
        COUNT(DISTINCT course_code)     AS covered_courses
    FROM exam_questions
    WHERE college_canon_code IS NOT NULL
    GROUP BY college_canon_code
),
faculty_counts AS (
    -- kg_faculty.college_internal_code added in migration 052
    SELECT
        college_internal_code,
        COUNT(*)    AS faculty_count
    FROM kg_faculty
    WHERE college_internal_code IS NOT NULL
    GROUP BY college_internal_code
),
doc_counts AS (
    SELECT
        college_canon_code,
        COUNT(*)    AS doc_count
    FROM source_documents
    WHERE college_canon_code IS NOT NULL
    GROUP BY college_canon_code
),
program_counts AS (
    SELECT
        college_internal_code,
        COUNT(*)    AS program_count
    FROM seu_programs_canon
    WHERE is_active = true
    GROUP BY college_internal_code
)
SELECT
    c.internal_code,
    c.name_ar,
    COALESCE(p.program_count,         0)    AS program_count,
    COALESCE(f.faculty_count,         0)    AS faculty_count,
    COALESCE(e.exam_question_count,   0)    AS exam_question_count,
    COALESCE(e.covered_courses,       0)    AS courses_with_exams,
    COALESCE(d.doc_count,             0)    AS source_doc_count,
    -- Gap score: higher = bigger gap between institutional weight and coverage
    CASE
        WHEN COALESCE(e.exam_question_count, 0) = 0
            THEN COALESCE(f.faculty_count, 0) * 100   -- infinite gap → scale by faculty
        ELSE
            ROUND(
                (COALESCE(f.faculty_count, 0)::NUMERIC
                 / e.exam_question_count) * 100,
                1
            )
    END AS acquisition_priority_score,
    -- Plain-language gap verdict
    CASE
        WHEN COALESCE(e.exam_question_count, 0) = 0 THEN 'critical — zero exam coverage'
        WHEN COALESCE(e.exam_question_count, 0) < 100 THEN 'high — sparse coverage'
        WHEN COALESCE(e.exam_question_count, 0) < 500 THEN 'medium — partial coverage'
        ELSE 'low — adequate coverage'
    END AS gap_verdict
FROM seu_colleges_canon c
LEFT JOIN exam_counts   e ON e.college_canon_code    = c.internal_code
LEFT JOIN faculty_counts f ON f.college_internal_code = c.internal_code
LEFT JOIN doc_counts    d ON d.college_canon_code    = c.internal_code
LEFT JOIN program_counts p ON p.college_internal_code = c.internal_code
ORDER BY acquisition_priority_score DESC NULLS LAST;

COMMENT ON VIEW college_knowledge_gap IS
    'Acquisition priority dashboard — quantifies the gap between institutional weight '
    '(faculty, programs) and knowledge coverage (exam questions, documents). '
    'acquisition_priority_score: higher = bigger gap = higher content investment priority. '
    'gap_verdict: human-readable tier for Cockpit display. '
    'As content is added per college, this view self-updates — no worker needed. '
    'The gap itself is a product: shows exactly where RUMMAN is blind.';


-- ── C.4 concept_cooccurrence_log TABLE ───────────────────────
-- Two concepts queried in the same session = a cognitive connection
-- that no textbook documents. This is emergent knowledge from behavior.
--
-- After 10K sessions: "Students who struggle with X always ask about Y next"
-- After 100K sessions: a cognitive graph of the university — unique in the world.
-- After 1M sessions: prerequisites can be inferred from behavior alone,
--   without ever reading a single study plan.
--
-- Schema enforces concept_a < concept_b (lexicographic order) to ensure
-- pairs are canonical regardless of query order. The INSERT path in
-- search_api must use LEAST()/GREATEST() before writing.
--
-- This table is empty at creation. It starts accumulating when
-- search_api.py is updated to log co-queries (additive change).

CREATE TABLE IF NOT EXISTS concept_cooccurrence_log (
    id                      BIGSERIAL   PRIMARY KEY,
    tenant_id               UUID        NOT NULL
                            DEFAULT '00000000-0000-0000-0000-000000000001',

    -- Anonymous session (hashed user + day — never raw user ID)
    session_id              TEXT        NOT NULL,

    -- The pair (lexicographically ordered — INSERT must enforce this)
    concept_a               TEXT        NOT NULL,   -- LEAST(c1, c2)
    concept_b               TEXT        NOT NULL,   -- GREATEST(c1, c2)

    -- Course context (same or different — cross-course pairs are valuable)
    course_code_a           TEXT,
    course_code_b           TEXT,
    college_canon_code_a    TEXT REFERENCES seu_colleges_canon(internal_code),
    college_canon_code_b    TEXT REFERENCES seu_colleges_canon(internal_code),

    -- Temporal gap (seconds between the two queries in the session)
    query_gap_seconds       INT,    -- short gap = linked thinking; long gap = coincidence

    occurred_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Enforce canonical pair order
    CONSTRAINT cc_pair_order CHECK (concept_a < concept_b)
);

COMMENT ON TABLE concept_cooccurrence_log IS
    'Co-occurrence log: two concepts queried in the same session. '
    'concept_a < concept_b enforced — pairs are canonical regardless of query order. '
    'INSERT path must use: LEAST(c1,c2) as concept_a, GREATEST(c1,c2) as concept_b. '
    'short query_gap_seconds = tightly linked in student cognition. '
    'After 100K sessions: emergent cognitive graph of SEU — no textbook has this. '
    'Empty at creation. Populated when search_api is updated to log co-queries.';

-- Primary: find all concepts paired with a given concept
CREATE INDEX IF NOT EXISTS idx_ccol_concept_a
    ON concept_cooccurrence_log (tenant_id, concept_a, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_ccol_concept_b
    ON concept_cooccurrence_log (tenant_id, concept_b, occurred_at DESC);

-- Cross-college co-occurrences are the rarest and most valuable
CREATE INDEX IF NOT EXISTS idx_ccol_cross_college
    ON concept_cooccurrence_log (college_canon_code_a, college_canon_code_b)
    WHERE college_canon_code_a IS DISTINCT FROM college_canon_code_b;


-- ── Verification queries ──────────────────────────────────────

-- Canon propagation coverage:
-- SELECT course_code, college_canon_code
--   FROM course_behavioral_profile ORDER BY course_code;
--
-- SELECT COUNT(*), college_canon_code
--   FROM concept_confusion_registry GROUP BY college_canon_code;

-- Exam coverage (should not timeout now):
-- SELECT * FROM college_exam_coverage;

-- Knowledge gap dashboard:
-- SELECT internal_code, name_ar, exam_question_count,
--        faculty_count, acquisition_priority_score, gap_verdict
--   FROM college_knowledge_gap;

-- Behavioral clock (currently sparse — grows with more weekly snapshots):
-- SELECT * FROM institutional_behavioral_clock;

-- ── END OF MIGRATION 055 ──────────────────────────────────────
