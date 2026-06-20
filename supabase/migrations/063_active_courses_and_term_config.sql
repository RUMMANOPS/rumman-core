-- Migration 063: Active Courses Foundation + Term Governance
--
-- Build 1A / Phase A — DRAFT. Applied manually after founder approval.
-- Adds:
--   app_term_config             — single-row active-term governance (kills hardcoded TERM_CODE)
--   student_registered_sections — the APPROVED schedule = ACTIVE_COURSES_SOURCE (decision D1)
--   term_sections (governance)  — import_version / source / is_active / last_imported_at
--
-- This migration is purely additive (CREATE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).
-- It does NOT drop or modify existing data.
-- SEU default tenant: 00000000-0000-0000-0000-000000000001

-- ── app_term_config ──────────────────────────────────────────────────────────
-- One active term per tenant. The mobile app reads active_term_code from here
-- instead of the hardcoded '202550' in RegistrationScreen.js.

CREATE TABLE IF NOT EXISTS app_term_config (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',

    active_term_code    TEXT        NOT NULL,            -- e.g. '202550'
    active_term_label   TEXT        NOT NULL,            -- e.g. 'Summer Term 2025-2026'
    source_url          TEXT,                            -- Banner termSelection reference
    source_term_label   TEXT,                            -- label exactly as it appears in Banner

    import_version      INT         NOT NULL DEFAULT 1,  -- bumps on each verified import
    last_imported_at    TIMESTAMPTZ,
    last_verified_at    TIMESTAMPTZ,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (tenant_id)                                   -- one active-term row per tenant
);

-- Seed the current active term (Summer 2025-2026 = 202550, verified against term_sections + snapshot)
INSERT INTO app_term_config
    (tenant_id, active_term_code, active_term_label, source_url, source_term_label, import_version)
VALUES
    ('00000000-0000-0000-0000-000000000001',
     '202550',
     'Summer Term 2025-2026',
     'https://bannservices.seu.edu.sa/StudentRegistrationSsb/ssb/term/termSelection?mode=search',
     'Summer Term 2025-2026',
     1)
ON CONFLICT (tenant_id) DO NOTHING;


-- ── student_registered_sections ──────────────────────────────────────────────
-- The student's APPROVED schedule from Smart Registration.
-- THIS is ACTIVE_COURSES_SOURCE (decision D1) — NOT onboarding_profile.enrolled_courses.
-- Editable always: status planned/approved/dropped + needs_review flag (never hard-delete).
--
-- Course-code strategy (per founder): preserve the raw Banner code AND the canonical code.
--   banner_course_code    — raw as it came from Banner, e.g. 'ACCT101'  (never lost)
--   canonical_course_code — RUMMAN catalog code if resolved via course_aliases, e.g. 'ACC101' (nullable)
--   course_code           — what the app uses now; temporarily mirrors banner_course_code

CREATE TABLE IF NOT EXISTS student_registered_sections (
    id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id            UUID        NOT NULL REFERENCES rumman_users(id) ON DELETE CASCADE,
    tenant_id             UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',

    -- Term + section identity
    term_code             TEXT        NOT NULL,
    crn                   TEXT        NOT NULL,
    section_number        TEXT,

    -- Course identity (dual-code, never lose the raw Banner code)
    banner_course_code    TEXT        NOT NULL,          -- raw Banner, e.g. 'ACCT101'
    canonical_course_code TEXT,                          -- RUMMAN catalog code if resolved (nullable)
    course_code           TEXT,                          -- app-facing; temporarily = banner_course_code
    course_name           TEXT,
    credit_hours          INT,

    -- Section attributes
    campus                TEXT,
    delivery_mode         TEXT,
    class_meetings        JSONB,                          -- [{day,start_time,end_time,building,room,type}]

    -- State
    status                TEXT        NOT NULL DEFAULT 'planned'
                              CHECK (status IN ('planned', 'approved', 'dropped', 'needs_review')),
    source                TEXT        NOT NULL DEFAULT 'smart_registration'
                              CHECK (source IN ('smart_registration', 'manual')),
    import_version        INT,                            -- which term_sections import this came from

    -- Timestamps
    approved_at           TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (student_id, term_code, crn)
);

CREATE INDEX IF NOT EXISTS idx_srs_student_term_status
    ON student_registered_sections (student_id, term_code, status);

CREATE INDEX IF NOT EXISTS idx_srs_crn
    ON student_registered_sections (term_code, crn);


-- ── term_sections governance (additive only) ─────────────────────────────────
-- Verified live: existing columns already include capacity, enrolled, remaining_seats,
-- open_section, class_meetings, subject_course, gender, campus, credit_hours, course_name.
-- Add ONLY the missing governance columns.

ALTER TABLE term_sections
    ADD COLUMN IF NOT EXISTS section_number    TEXT,                 -- Banner section seq (e.g. '0'); preserves section identity
    ADD COLUMN IF NOT EXISTS import_version    INT,
    ADD COLUMN IF NOT EXISTS source_url        TEXT,
    ADD COLUMN IF NOT EXISTS source_term_label TEXT,
    ADD COLUMN IF NOT EXISTS is_active         BOOLEAN DEFAULT true,
    ADD COLUMN IF NOT EXISTS last_imported_at  TIMESTAMPTZ;
