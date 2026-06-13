-- ============================================================
-- Migration: 049_behavioral_intelligence.sql
-- Date:      2026-06-13
-- Author:    RUMMAN Platform
--
-- Purpose:
--   Behavioral Intelligence Layer — turns RUMMAN's historical
--   user behavior data (learning_events, message_signals) into
--   durable, compounding institutional assets.
--
--   "The 5M Telegram messages are not messages.
--    They are a Historical User Behavior Dataset."
--
-- Sections:
--   A. concept_confusion_registry — per-concept confusion that
--      compounds every semester. The moat that can't be bought.
--   B. course_behavioral_profile  — weekly intelligence snapshot
--      per course: query volume, failure rate, panic index.
--   C. get_course_behavioral_intelligence() RPC — single call
--      returns full behavioral picture for any course.
--   D. refresh_course_behavioral_profile() function — computes
--      current snapshot from learning_events + message_signals.
--      Called by future worker; safe to call manually anytime.
--
-- Compounding logic:
--   Year 1: "Students struggle with Corporate Governance (score 87)"
--   Year 2: "This concept has been critical for 2 years, trend=rising"
--   Year 3: "Perennial gap — 3 years of data, exam_frequency=4, never resolved"
--   → By year 3 this data cannot be replicated by any new competitor.
--
-- Safety:
--   100% additive. All functions use STABLE or VOLATILE as appropriate.
--   refresh_course_behavioral_profile() only writes to new tables.
-- ============================================================


-- ── A. concept_confusion_registry ────────────────────────────
-- The highest-compounding asset in RUMMAN.
-- One row per (concept_name × course_code). Accumulates over time.
--
-- confusion_score = (failed_queries / total_queries) * 100
--   Simple failure rate — understandable and debuggable.
--
-- critical_intersection = confusion_score >= 50 AND exam_frequency >= 2
--   This is Tier-3 intelligence stored permanently:
--   "Student can't find this → AND it appears in multiple exam years"
--
-- Populated by concept_confusion_worker (future Python worker).
-- concept_name comes from exam_questions.topic_tags or kg_topics.canonical_name.

CREATE TABLE IF NOT EXISTS concept_confusion_registry (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',

    concept_name        TEXT        NOT NULL,   -- normalized topic label
    course_code         TEXT        NOT NULL,

    -- Accumulated from learning_events (never resets — only grows)
    total_queries       INT         NOT NULL DEFAULT 0,
    failed_queries      INT         NOT NULL DEFAULT 0,  -- grounded=false
    confusion_score     FLOAT       NOT NULL DEFAULT 0.0 CHECK (confusion_score BETWEEN 0 AND 100),

    -- Cross-referenced from exam_questions (via get_recurring_topics RPC)
    exam_frequency      INT         NOT NULL DEFAULT 0,  -- distinct years this topic appeared

    -- Cross-referenced from message_signals
    telegram_mentions   INT         NOT NULL DEFAULT 0,  -- signals mentioning this concept

    -- The Tier-3 intersection: high confusion AND high exam recurrence
    critical_intersection BOOLEAN   NOT NULL DEFAULT false,

    -- Temporal tracking — enables trend computation
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_queried_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    trend               TEXT        NOT NULL DEFAULT 'stable'
                        CHECK (trend IN ('rising', 'stable', 'falling')),

    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (concept_name, course_code, tenant_id)
);

COMMENT ON TABLE concept_confusion_registry IS
    'Per-concept confusion intelligence that compounds every semester. '
    'total_queries and failed_queries accumulate and never reset — this is the moat. '
    'critical_intersection=true means: students fail to find it AND it appears in multiple exams. '
    'Populated by concept_confusion_worker (not yet active). '
    'concept_name aligns with exam_questions.topic_tags entries.';

-- Primary: what are the hardest concepts in this course?
CREATE INDEX IF NOT EXISTS idx_ccr_course_score
    ON concept_confusion_registry (course_code, confusion_score DESC)
    WHERE critical_intersection = true;

-- All concepts for a course ordered by score
CREATE INDEX IF NOT EXISTS idx_ccr_course_all
    ON concept_confusion_registry (tenant_id, course_code, confusion_score DESC);

-- Find rising-trend critical concepts across all courses
CREATE INDEX IF NOT EXISTS idx_ccr_critical_trend
    ON concept_confusion_registry (trend, confusion_score DESC)
    WHERE critical_intersection = true AND trend = 'rising';

-- Recency: find stale entries
CREATE INDEX IF NOT EXISTS idx_ccr_stale
    ON concept_confusion_registry (last_queried_at);


-- ── B. course_behavioral_profile ─────────────────────────────
-- Weekly snapshot per course. Recomputed by refresh function.
-- UNIQUE(course_code, computed_week) — one snapshot per week.
--
-- panic_index: ratio of query volume in last 3 days vs 7-day avg.
--   > 2.0 = students are cramming (exam is near)
--   < 0.5 = course is idle (between exams or holiday)
--
-- corpus_coverage_score: 1 - grounded_failure_rate
--   Measures how well RUMMAN answers questions about this course.
--   If coverage < 0.5 → knowledge gap alert in Cockpit.

CREATE TABLE IF NOT EXISTS course_behavioral_profile (
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                   UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',

    course_code                 TEXT        NOT NULL,
    computed_week               DATE        NOT NULL,   -- Monday of the week

    -- Query activity
    query_volume_7d             INT         NOT NULL DEFAULT 0,
    query_volume_30d            INT         NOT NULL DEFAULT 0,
    query_volume_3d             INT         NOT NULL DEFAULT 0,   -- for panic_index
    panic_index                 FLOAT       NOT NULL DEFAULT 1.0, -- query_volume_3d / (query_volume_7d/7*3)

    -- Answer quality
    grounded_failure_rate       FLOAT       NOT NULL DEFAULT 0.0 CHECK (grounded_failure_rate BETWEEN 0 AND 1),
    corpus_coverage_score       FLOAT       NOT NULL DEFAULT 1.0 CHECK (corpus_coverage_score BETWEEN 0 AND 1),

    -- Confusion summary (from concept_confusion_registry)
    critical_concept_count      INT         NOT NULL DEFAULT 0,
    top_confusion_topics        TEXT[]      NOT NULL DEFAULT '{}',

    -- Community signals (from message_signals)
    telegram_signal_count_7d    INT         NOT NULL DEFAULT 0,
    dominant_signal_type        TEXT,   -- 'confusion_cluster' | 'exam_emphasis' | 'professor_note' ...

    computed_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (course_code, computed_week, tenant_id)
);

COMMENT ON TABLE course_behavioral_profile IS
    'Weekly behavioral intelligence snapshot per course. '
    'panic_index > 2.0 signals exam-proximity cramming. '
    'corpus_coverage_score < 0.5 triggers knowledge gap alert in Cockpit. '
    'Computed by refresh_course_behavioral_profile() — callable anytime, '
    'automated by course_behavioral_worker (not yet active).';

-- Primary: latest profile for a course
CREATE INDEX IF NOT EXISTS idx_cbp_course_week
    ON course_behavioral_profile (course_code, computed_week DESC);

-- Cockpit: courses with low corpus coverage needing attention
CREATE INDEX IF NOT EXISTS idx_cbp_low_coverage
    ON course_behavioral_profile (tenant_id, corpus_coverage_score ASC, computed_week DESC)
    WHERE corpus_coverage_score < 0.5;

-- High panic index — exam imminent
CREATE INDEX IF NOT EXISTS idx_cbp_panic
    ON course_behavioral_profile (panic_index DESC, computed_week DESC)
    WHERE panic_index > 2.0;


-- ── C. get_course_behavioral_intelligence() RPC ───────────────
-- Returns full behavioral picture for any course in one call.
-- Used by: search_api.py /v1/intelligence/course/{code},
--          Cockpit course cards, Student OS proactive layer.

CREATE OR REPLACE FUNCTION get_course_behavioral_intelligence(
    p_course_code   TEXT,
    p_tenant_id     UUID    DEFAULT '00000000-0000-0000-0000-000000000001'
)
RETURNS JSONB
LANGUAGE SQL STABLE AS $$
    SELECT jsonb_build_object(

        'course_code', p_course_code,

        -- Latest weekly behavioral snapshot
        'behavioral_profile', (
            SELECT row_to_json(cbp.*)
            FROM course_behavioral_profile cbp
            WHERE cbp.course_code  = p_course_code
              AND cbp.tenant_id    = p_tenant_id
            ORDER BY cbp.computed_week DESC
            LIMIT 1
        ),

        -- Tier-3 critical intersections (confused + exam-critical)
        'critical_concepts', (
            SELECT COALESCE(
                json_agg(
                    json_build_object(
                        'concept',          ccr.concept_name,
                        'confusion_score',  ccr.confusion_score,
                        'exam_frequency',   ccr.exam_frequency,
                        'total_queries',    ccr.total_queries,
                        'failed_queries',   ccr.failed_queries,
                        'trend',            ccr.trend,
                        'last_queried',     ccr.last_queried_at
                    ) ORDER BY ccr.confusion_score DESC
                ),
                '[]'::json
            )
            FROM concept_confusion_registry ccr
            WHERE ccr.course_code         = p_course_code
              AND ccr.tenant_id           = p_tenant_id
              AND ccr.critical_intersection = true
            LIMIT 8
        ),

        -- Top confused concepts (including non-critical)
        'top_confused', (
            SELECT COALESCE(
                json_agg(
                    json_build_object(
                        'concept',         ccr.concept_name,
                        'confusion_score', ccr.confusion_score,
                        'trend',           ccr.trend
                    ) ORDER BY ccr.confusion_score DESC
                ),
                '[]'::json
            )
            FROM concept_confusion_registry ccr
            WHERE ccr.course_code   = p_course_code
              AND ccr.tenant_id     = p_tenant_id
              AND ccr.total_queries >= 2
            LIMIT 10
        ),

        -- Recent official announcements affecting this course
        'official_announcements', (
            SELECT COALESCE(
                json_agg(
                    json_build_object(
                        'title',        oa.title,
                        'category',     oa.category,
                        'urgency',      oa.urgency_score,
                        'published_at', oa.published_at,
                        'url',          oa.source_url
                    ) ORDER BY oa.published_at DESC
                ),
                '[]'::json
            )
            FROM official_announcements oa
            WHERE (oa.related_course  = p_course_code
                   OR oa.related_course IS NULL)
              AND oa.tenant_id        = p_tenant_id
              AND oa.status           = 'active'
              AND oa.published_at     > now() - INTERVAL '14 days'
            LIMIT 3
        )

    )
$$;

COMMENT ON FUNCTION get_course_behavioral_intelligence IS
    'Returns full behavioral intelligence for a course: '
    'latest snapshot (panic_index, coverage, failure_rate), '
    'critical Tier-3 concepts (confusion × exam recurrence), '
    'top confused concepts, and recent official announcements. '
    'Called by /v1/intelligence/course/{course_code} endpoint.';


-- ── D. refresh_course_behavioral_profile() ───────────────────
-- Computes the current behavioral profile from learning_events
-- and message_signals, then UPSERTs into course_behavioral_profile.
--
-- Calling convention:
--   SELECT refresh_course_behavioral_profile('MGT401');
--   SELECT refresh_course_behavioral_profile('MGT401', '2026-06-09');
--
-- Safe to call multiple times — idempotent via UNIQUE constraint.
-- Future course_behavioral_worker calls this weekly per active course.

CREATE OR REPLACE FUNCTION refresh_course_behavioral_profile(
    p_course_code   TEXT,
    p_week_start    DATE    DEFAULT date_trunc('week', CURRENT_DATE)::DATE,
    p_tenant_id     UUID    DEFAULT '00000000-0000-0000-0000-000000000001'
)
RETURNS VOID
LANGUAGE plpgsql VOLATILE AS $$
DECLARE
    v_vol_7d    INT;
    v_vol_30d   INT;
    v_vol_3d    INT;
    v_failed_7d INT;
    v_panic     FLOAT;
    v_failure   FLOAT;
    v_coverage  FLOAT;
    v_sig_count INT;
    v_dom_sig   TEXT;
    v_critical  INT;
    v_top_topics TEXT[];
BEGIN
    -- Query volumes from learning_events
    SELECT
        COUNT(*) FILTER (WHERE le.created_at >= now() - INTERVAL '7 days'),
        COUNT(*) FILTER (WHERE le.created_at >= now() - INTERVAL '30 days'),
        COUNT(*) FILTER (WHERE le.created_at >= now() - INTERVAL '3 days'),
        COUNT(*) FILTER (WHERE le.created_at >= now() - INTERVAL '7 days'
                           AND le.grounded = false)
    INTO v_vol_7d, v_vol_30d, v_vol_3d, v_failed_7d
    FROM learning_events le
    WHERE p_course_code = ANY(le.course_codes)
      AND le.created_at >= now() - INTERVAL '30 days';

    -- panic_index = 3d query rate vs expected 3d rate from 7d average
    -- Expected 3d rate = (v_vol_7d / 7) * 3
    v_panic := CASE
        WHEN v_vol_7d = 0 THEN 1.0
        ELSE ROUND((v_vol_3d::FLOAT / GREATEST(1, (v_vol_7d::FLOAT / 7.0) * 3.0))::NUMERIC, 2)
    END;

    -- grounded failure rate (last 7 days)
    v_failure := CASE
        WHEN v_vol_7d = 0 THEN 0.0
        ELSE ROUND((v_failed_7d::FLOAT / v_vol_7d)::NUMERIC, 3)
    END;

    v_coverage := ROUND((1.0 - v_failure)::NUMERIC, 3);

    -- Telegram signal count + dominant type (last 7 days)
    SELECT
        COUNT(*),
        MODE() WITHIN GROUP (ORDER BY ms.signal_type)
    INTO v_sig_count, v_dom_sig
    FROM message_signals ms
    WHERE ms.course_code = p_course_code
      AND ms.created_at  >= now() - INTERVAL '7 days'
      AND ms.tenant_id   = p_tenant_id;

    -- Critical concept count from concept_confusion_registry
    SELECT COUNT(*)
    INTO v_critical
    FROM concept_confusion_registry ccr
    WHERE ccr.course_code           = p_course_code
      AND ccr.tenant_id             = p_tenant_id
      AND ccr.critical_intersection = true;

    -- Top 5 confused concepts
    SELECT ARRAY_AGG(ccr.concept_name ORDER BY ccr.confusion_score DESC)
    INTO v_top_topics
    FROM (
        SELECT concept_name, confusion_score
        FROM concept_confusion_registry
        WHERE course_code = p_course_code
          AND tenant_id   = p_tenant_id
        ORDER BY confusion_score DESC
        LIMIT 5
    ) ccr;

    -- UPSERT the snapshot
    INSERT INTO course_behavioral_profile (
        tenant_id, course_code, computed_week,
        query_volume_7d, query_volume_30d, query_volume_3d, panic_index,
        grounded_failure_rate, corpus_coverage_score,
        critical_concept_count, top_confusion_topics,
        telegram_signal_count_7d, dominant_signal_type,
        computed_at
    ) VALUES (
        p_tenant_id, p_course_code, p_week_start,
        v_vol_7d, v_vol_30d, v_vol_3d, v_panic,
        v_failure, v_coverage,
        COALESCE(v_critical, 0),
        COALESCE(v_top_topics, ARRAY[]::TEXT[]),
        COALESCE(v_sig_count, 0), v_dom_sig,
        now()
    )
    ON CONFLICT (course_code, computed_week, tenant_id) DO UPDATE SET
        query_volume_7d          = EXCLUDED.query_volume_7d,
        query_volume_30d         = EXCLUDED.query_volume_30d,
        query_volume_3d          = EXCLUDED.query_volume_3d,
        panic_index              = EXCLUDED.panic_index,
        grounded_failure_rate    = EXCLUDED.grounded_failure_rate,
        corpus_coverage_score    = EXCLUDED.corpus_coverage_score,
        critical_concept_count   = EXCLUDED.critical_concept_count,
        top_confusion_topics     = EXCLUDED.top_confusion_topics,
        telegram_signal_count_7d = EXCLUDED.telegram_signal_count_7d,
        dominant_signal_type     = EXCLUDED.dominant_signal_type,
        computed_at              = now();
END;
$$;

COMMENT ON FUNCTION refresh_course_behavioral_profile IS
    'Computes and upserts a behavioral intelligence snapshot for a course. '
    'Reads from learning_events (query volume, failure rate) and '
    'message_signals (Telegram activity). Idempotent — safe to call repeatedly. '
    'Called manually: SELECT refresh_course_behavioral_profile(''MGT401''); '
    'Automated by course_behavioral_worker (not yet active).';


-- ── END OF MIGRATION 049 ──────────────────────────────────────
