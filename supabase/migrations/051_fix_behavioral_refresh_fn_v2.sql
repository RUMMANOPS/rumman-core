-- ============================================================
-- Migration: 051_fix_behavioral_refresh_fn_v2.sql
-- Date:      2026-06-13
--
-- Fix: message_signals uses extracted_at, not created_at.
--      Discovered after 050 was applied.
-- ============================================================

CREATE OR REPLACE FUNCTION refresh_course_behavioral_profile(
    p_course_code   TEXT,
    p_week_start    DATE    DEFAULT date_trunc('week', CURRENT_DATE)::DATE,
    p_tenant_id     UUID    DEFAULT '00000000-0000-0000-0000-000000000001'
)
RETURNS VOID
LANGUAGE plpgsql VOLATILE AS $$
DECLARE
    v_vol_7d     INT;
    v_vol_30d    INT;
    v_vol_3d     INT;
    v_failed_7d  INT;
    v_panic      FLOAT;
    v_failure    FLOAT;
    v_coverage   FLOAT;
    v_sig_count  INT;
    v_dom_sig    TEXT;
    v_critical   INT;
    v_top_topics TEXT[];
BEGIN
    -- learning_events uses occurred_at
    SELECT
        COUNT(*) FILTER (WHERE le.occurred_at >= now() - INTERVAL '7 days'),
        COUNT(*) FILTER (WHERE le.occurred_at >= now() - INTERVAL '30 days'),
        COUNT(*) FILTER (WHERE le.occurred_at >= now() - INTERVAL '3 days'),
        COUNT(*) FILTER (WHERE le.occurred_at >= now() - INTERVAL '7 days'
                           AND le.grounded = false)
    INTO v_vol_7d, v_vol_30d, v_vol_3d, v_failed_7d
    FROM learning_events le
    WHERE p_course_code = ANY(le.course_codes)
      AND le.occurred_at >= now() - INTERVAL '30 days';

    v_panic := CASE
        WHEN v_vol_7d = 0 THEN 1.0
        ELSE ROUND((v_vol_3d::FLOAT / GREATEST(1, (v_vol_7d::FLOAT / 7.0) * 3.0))::NUMERIC, 2)
    END;

    v_failure := CASE
        WHEN v_vol_7d = 0 THEN 0.0
        ELSE ROUND((v_failed_7d::FLOAT / v_vol_7d)::NUMERIC, 3)
    END;

    v_coverage := ROUND((1.0 - v_failure)::NUMERIC, 3);

    -- message_signals uses extracted_at
    SELECT
        COUNT(*),
        MODE() WITHIN GROUP (ORDER BY ms.signal_type)
    INTO v_sig_count, v_dom_sig
    FROM message_signals ms
    WHERE ms.course_code   = p_course_code
      AND ms.extracted_at >= now() - INTERVAL '7 days'
      AND ms.tenant_id     = p_tenant_id;

    SELECT COUNT(*)
    INTO v_critical
    FROM concept_confusion_registry ccr
    WHERE ccr.course_code           = p_course_code
      AND ccr.tenant_id             = p_tenant_id
      AND ccr.critical_intersection = true;

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

    INSERT INTO course_behavioral_profile (
        tenant_id, course_code, computed_week,
        query_volume_7d, query_volume_30d, query_volume_3d, panic_index,
        grounded_failure_rate, corpus_coverage_score,
        critical_concept_count, top_confusion_topics,
        telegram_signal_count_7d, dominant_signal_type,
        computed_at
    ) VALUES (
        p_tenant_id, p_course_code, p_week_start,
        COALESCE(v_vol_7d,  0), COALESCE(v_vol_30d, 0), COALESCE(v_vol_3d, 0),
        COALESCE(v_panic,   1.0),
        COALESCE(v_failure, 0.0), COALESCE(v_coverage, 1.0),
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
