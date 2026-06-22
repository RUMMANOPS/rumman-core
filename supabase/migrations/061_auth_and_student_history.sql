-- Migration 061: Mobile Auth Identity + Append-Only Student History
--
-- Two foundational additions for the Student OS mobile app:
--
-- 1. Mobile device identity — frictionless auth for First-100.
--    A student launches the app, gets a UUID, we hash it and store it.
--    No phone number, no password, no friction. Identity builds over time.
--    The same rumman_users infrastructure, platform='mobile'.
--
-- 2. student_history — the Time Asset.
--    Append-only. No UPDATE, no DELETE via API ever.
--    Every meaningful student action is a permanent, immutable fact.
--    This is the compounding moat: courses taken, tasks completed,
--    requests submitted, questions asked, grades logged, decisions made.
--    It cannot be bought. It cannot be recreated. Guard it.

-- ---------------------------------------------------------------------------
-- 1. MOBILE DEVICE IDENTITY REGISTER
--    Tracks raw device UUIDs (before hashing) so we can revoke/transfer.
--    The actual user identity lives in rumman_users(platform='mobile').
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS mobile_device_sessions (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id      UUID        NOT NULL REFERENCES rumman_users(id) ON DELETE CASCADE,
    tenant_id       UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    device_hash     TEXT        NOT NULL,   -- SHA-256(salt:device_uuid) — same as platform_user_hash
    app_version     TEXT,
    platform_os     TEXT,                   -- 'ios' | 'android'
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (device_hash)
);

CREATE INDEX IF NOT EXISTS mobile_device_student_idx ON mobile_device_sessions (student_id);

-- ---------------------------------------------------------------------------
-- 2. STUDENT_HISTORY — THE TIME ASSET
--    Append-only log of every meaningful student action.
--    event_type taxonomy (extend freely, never remove existing values):
--
--    Onboarding:    onboarding_completed
--    Academic:      grade_logged, course_started, course_completed
--    Tasks:         task_created, task_completed, task_snoozed, task_deleted
--    Calendar:      calendar_event_added, calendar_event_confirmed
--    Requests:      request_started, request_submitted, request_resolved
--    Learning:      ask_query, ask_resolved, ask_confused, ask_task_created
--    Decisions:     registration_plan_built, graduation_sim_run
--    System:        session_started, profile_updated
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS student_history (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id      UUID        NOT NULL REFERENCES rumman_users(id) ON DELETE CASCADE,
    tenant_id       UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    event_type      TEXT        NOT NULL,
    event_data      JSONB       NOT NULL DEFAULT '{}',
    course_code     TEXT,                   -- denormalized for fast course-scoped queries
    -- Causal chain: if this event was triggered by another (e.g. task from ask)
    caused_by_id    UUID        REFERENCES student_history(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    -- NO updated_at — this table is append-only
    -- Rows are permanent records of truth, not mutable state
);

CREATE INDEX IF NOT EXISTS student_history_student_idx
    ON student_history (student_id, created_at DESC);

CREATE INDEX IF NOT EXISTS student_history_type_idx
    ON student_history (student_id, event_type, created_at DESC);

CREATE INDEX IF NOT EXISTS student_history_course_idx
    ON student_history (student_id, course_code, created_at DESC)
    WHERE course_code IS NOT NULL;

CREATE INDEX IF NOT EXISTS student_history_causal_idx
    ON student_history (caused_by_id)
    WHERE caused_by_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 3. STUDENT PROFILE CONTEXT — rich onboarding data
--    The existing student_context (migration 030) uses key-value rows.
--    We add a single 'onboarding_profile' row per student with the full
--    profile from OnboardingScreen: college, specialization, level,
--    enrolled courses, completed courses, credit hours, gender.
--
--    This is "explicit" confidence (student stated it directly) — never expires.
--    context_type = 'onboarding_profile'
--    context_value = { see below }
-- ---------------------------------------------------------------------------

-- No new table needed — student_context already handles this via context_type.
-- See auth_api.py for the upsert pattern.

-- Example onboarding_profile context_value:
-- {
--   "version": 1,
--   "university": "SEU",
--   "university_name": "الجامعة السعودية الإلكترونية",
--   "college_id": "uuid",
--   "college_name_ar": "كلية الحوسبة",
--   "college_code": "CS",
--   "specialization_id": "uuid",
--   "specialization_name_ar": "علوم الحاسب",
--   "specialization_code": "BSCS",
--   "current_level": 4,
--   "gender": "M",
--   "enrolled_courses": ["CS251", "IT362"],
--   "completed_courses": ["CS101", "CS201", "MGT101"],
--   "completed_credit_hours": 54,
--   "remaining_credit_hours": 72,
--   "total_credit_hours": 126,
--   "onboarding_completed_at": "ISO timestamp"
-- }

COMMENT ON TABLE student_history IS
    'Append-only record of every meaningful student action. '
    'No UPDATE or DELETE. The Time Asset — compounds with every interaction.';

COMMENT ON TABLE mobile_device_sessions IS
    'Device identity register for mobile auth. '
    'Links device hashes to student_id (rumman_users). '
    'One device can have one student_id; one student_id can have multiple devices.';
