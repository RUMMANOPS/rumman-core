-- Migration 030: Student context memory table
--
-- Persistent, per-user context signals that survive across sessions.
-- Powers the context bundle injected into synthesis: enrolled courses,
-- language preference, active course focus, and behavioral signals.
--
-- Three confidence tiers:
--   high   — explicitly stated by the student (never expires)
--   medium — consistently observed across 3+ interactions (expires 30 days)
--   low    — observed once or twice (expires 7 days)
--
-- Gated by rumman_users.opted_into_memory (default true).
-- If opted_into_memory = false, this table is not read or written.

CREATE TABLE IF NOT EXISTS student_context (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID        NOT NULL REFERENCES rumman_users(id) ON DELETE CASCADE,
    tenant_id      UUID        NOT NULL,
    context_type   TEXT        NOT NULL,
    -- context_type values:
    --   enrolled_courses  — {"codes": ["IT362", "MGT311"]}
    --   lang_pref         — {"lang": "ar" | "en"}
    --   active_focus      — {"course_code": "IT362", "exam_type": "midterm"}
    --   study_pattern     — {"peak_hour_utc": 20, "sessions_this_week": 5}
    context_value  JSONB       NOT NULL,
    confidence     TEXT        NOT NULL DEFAULT 'low'
                               CHECK (confidence IN ('high', 'medium', 'low')),
    source         TEXT        NOT NULL DEFAULT 'inferred'
                               CHECK (source IN ('explicit', 'inferred', 'confirmed')),
    observed_count INT         NOT NULL DEFAULT 1,
    last_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at     TIMESTAMPTZ,            -- NULL = never expires (explicit only)
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- One row per (user, context_type) — upsert replaces previous value.
    UNIQUE (user_id, context_type)
);

CREATE INDEX IF NOT EXISTS student_context_user_idx
    ON student_context (user_id, tenant_id);

CREATE INDEX IF NOT EXISTS student_context_expires_idx
    ON student_context (expires_at)
    WHERE expires_at IS NOT NULL;

-- Sweep expired context rows (run periodically via pg_cron or on-demand)
-- DELETE FROM student_context WHERE expires_at < now();
