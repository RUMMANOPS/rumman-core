-- ============================================================
-- Migration 075: Student Progress Schema
-- DRAFT — do not apply without explicit approval
-- ============================================================
-- Creates two tables:
--   1. student_program_profile   — declared program per student
--   2. student_course_history    — one row per attempt (student × term × banner_course_code)
--
-- CONSTRAINTS:
--   - No grades, no GPA, no percentages
--   - course_state carries pass/fail semantics only
--   - credit_hours_banner is stored for reference only; never summed
--   - All credit calculations join cat_program_courses.credit_hours
--   - Multiple attempts supported; is_counted controls which attempt is canonical
-- ============================================================

BEGIN;

-- ────────────────────────────────────────────────────────────
-- PREFLIGHT ASSERTIONS
-- ────────────────────────────────────────────────────────────
DO $$
BEGIN
    -- Assert: rumman_users table exists (FK target)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'rumman_users'
    ) THEN
        RAISE EXCEPTION 'PREFLIGHT FAILED: rumman_users table not found';
    END IF;

    -- Assert: catalog_versions table exists (FK target)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'catalog_versions'
    ) THEN
        RAISE EXCEPTION 'PREFLIGHT FAILED: catalog_versions table not found';
    END IF;

    -- Assert: student_program_profile does not already exist
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'student_program_profile'
    ) THEN
        RAISE EXCEPTION 'PREFLIGHT FAILED: student_program_profile already exists — migration already applied?';
    END IF;

    -- Assert: student_course_history does not already exist
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'student_course_history'
    ) THEN
        RAISE EXCEPTION 'PREFLIGHT FAILED: student_course_history already exists — migration already applied?';
    END IF;

    RAISE NOTICE 'PREFLIGHT OK: all assertions passed';
END;
$$;

-- ────────────────────────────────────────────────────────────
-- TABLE 1: student_program_profile
-- One row per student. Stores the declared/official program.
-- Without this table, every progress query requires the caller
-- to supply program_code — wrong program → wrong credit_hours.
-- ────────────────────────────────────────────────────────────
CREATE TABLE public.student_program_profile (
    student_id          UUID        NOT NULL,
    tenant_id           UUID        NOT NULL,
    program_code        TEXT        NOT NULL,
    catalog_version_id  UUID        NULL
        REFERENCES public.catalog_versions(id) ON DELETE SET NULL,
    declared_at         TIMESTAMPTZ NULL,
    source              TEXT        NOT NULL
        CHECK (source IN ('banner_sync', 'self_declared', 'inferred')),
    is_active           BOOLEAN     NOT NULL DEFAULT true,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_student_program_profile PRIMARY KEY (student_id)
);

-- Indexes
CREATE INDEX idx_spp_tenant     ON public.student_program_profile (tenant_id);
CREATE INDEX idx_spp_program    ON public.student_program_profile (program_code);
CREATE INDEX idx_spp_active     ON public.student_program_profile (student_id) WHERE is_active = true;

-- ────────────────────────────────────────────────────────────
-- TABLE 2: student_course_history
-- One row per attempt (student × term × banner_course_code).
-- Attempt rows are never deleted; is_counted controls which
-- attempt is canonical for credit and prerequisite purposes.
--
-- credit_hours_banner: stored for reference only.
--   NEVER sum this column. Always join cat_program_courses.credit_hours
--   filtered by program_code for authoritative credit values.
--   Reason: ENG001 is 8cr in CS and 16cr in IT — a Banner-supplied
--   single value cannot represent both.
-- ────────────────────────────────────────────────────────────
CREATE TABLE public.student_course_history (
    id                      UUID        NOT NULL DEFAULT gen_random_uuid(),
    student_id              UUID        NOT NULL,
    tenant_id               UUID        NOT NULL,
    term_code               TEXT        NOT NULL,   -- e.g. '202420'; '000000' = pre-enrollment
    banner_course_code      TEXT        NOT NULL,   -- raw Banner code, never modified
    canonical_course_code   TEXT        NULL,       -- resolved via alias tables (P9 backfill)
    program_code            TEXT        NULL,       -- which program this attempt counts toward
    credit_hours_banner     INT         NULL,       -- informational only — NEVER used in calculations

    course_state            TEXT        NOT NULL
        CHECK (course_state IN (
            'planned',
            'in_progress',
            'passed',
            'failed',
            'withdrawn',
            'transferred',
            'exempted',
            'repeated',
            'ignored'
        )),

    -- Counting flags
    -- is_counted=true  → counts toward graduation + opens prerequisites
    -- is_counted=false → row retained but superseded (e.g. failed attempt when retake passes)
    is_counted              BOOLEAN     NOT NULL DEFAULT false,
    is_excluded             BOOLEAN     NOT NULL DEFAULT false,  -- admin override

    -- Provenance
    source                  TEXT        NOT NULL DEFAULT 'banner_sync'
        CHECK (source IN ('banner_sync', 'student_import', 'manual', 'admin_review')),
    confidence              TEXT        NOT NULL DEFAULT 'high'
        CHECK (confidence IN ('high', 'medium', 'low')),
    verified_by_student     BOOLEAN     NOT NULL DEFAULT false,

    -- Timestamps
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_updated_at       TIMESTAMPTZ NULL,   -- when source system last reported this row
    notes                   TEXT        NULL,   -- free-text audit note

    CONSTRAINT pk_student_course_history PRIMARY KEY (id),
    CONSTRAINT uq_sch_attempt UNIQUE (student_id, term_code, banner_course_code)
);

-- Indexes
CREATE INDEX idx_sch_student        ON public.student_course_history (student_id);
CREATE INDEX idx_sch_tenant         ON public.student_course_history (tenant_id);
CREATE INDEX idx_sch_canonical      ON public.student_course_history (canonical_course_code) WHERE canonical_course_code IS NOT NULL;
CREATE INDEX idx_sch_counted        ON public.student_course_history (student_id, program_code) WHERE is_counted = true;
CREATE INDEX idx_sch_term           ON public.student_course_history (student_id, term_code);
CREATE INDEX idx_sch_state          ON public.student_course_history (course_state);

-- ────────────────────────────────────────────────────────────
-- POST-CHECK
-- ────────────────────────────────────────────────────────────
DO $$
DECLARE
    tbl_count INT;
BEGIN
    SELECT COUNT(*) INTO tbl_count
    FROM information_schema.tables
    WHERE table_schema = 'public'
      AND table_name IN ('student_program_profile', 'student_course_history');

    IF tbl_count <> 2 THEN
        RAISE EXCEPTION 'POST-CHECK FAILED: expected 2 tables, found %', tbl_count;
    END IF;

    RAISE NOTICE 'POST-CHECK OK: student_program_profile and student_course_history created';
END;
$$;

COMMIT;
