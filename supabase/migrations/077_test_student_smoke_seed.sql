-- ============================================================
-- 077: Test Student Smoke Seed — DRAFT (NOT APPLIED)
-- ============================================================
-- Populates smoke-test data for the 5 students defined in
-- TEST_STUDENT_SMOKE_MATRIX_PLAN.md.
--
-- DO NOT apply without explicit approval.
-- DO NOT push to production without a teardown plan.
-- Run teardown (bottom of file) after smoke tests pass.
--
-- Tables written (in FK dependency order):
--   §1. rumman_users               — 5 ghost rows
--       [Required: student_registered_sections FK → rumman_users(id) ON DELETE CASCADE.
--        student_program_profile and student_course_history have NO FK on student_id.]
--   §2. student_program_profile    — 6 rows  (A×1, B×2, C×1, D×1, E×1)
--   §3. student_course_history     — 32 rows (A:8, B:12, C:7, D:5, E:0)
--   §4. student_registered_sections — 7 rows (A:3, B:2, C:1, D:1, E:0)
--
-- UUID block (reserved, not in rumman_users before this seed):
--   eeeeeeee-0000-0000-0000-00000000000A  Student A — MGT bachelor
--   eeeeeeee-0000-0000-0000-00000000000B  Student B — CS bachelor (transferred from MGT)
--   eeeeeeee-0000-0000-0000-00000000000C  Student C — PH bachelor (Arabic codes, needs_review)
--   eeeeeeee-0000-0000-0000-00000000000D  Student D — MBA master
--   eeeeeeee-0000-0000-0000-00000000000E  Student E — BUSINESS_ADMINISTRATION diploma (guard)
--
-- Expected API outputs (for assertion in smoke runner):
--   Student A: cc=31, rc=99, current=2  (STAT101 dropped — excluded; ACCT101 withdrawn — 0cr)
--   Student B: cc=34, cc_count=10, current=2  (ENG001=8cr CS; CS230 counted once; TRNS200=0cr null)
--   Student C: cc=32, rc=101, current=1  (نجل001=16cr; needs_review_program warning; 200 NOT 404)
--   Student D: cc=15, rc=21, current=1   (36cr total; NOT 130-based; MGT520 transferred counted)
--   Student E: ALL endpoints → 404       (diploma program not in v_draft_catalog_programs)
-- ============================================================

-- ─────────────────────────────────────────────────────────────
-- PREFLIGHT ASSERTIONS
-- ─────────────────────────────────────────────────────────────
DO $$
BEGIN
    -- Required tables exist
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'tenants'
    ) THEN
        RAISE EXCEPTION 'PREFLIGHT FAILED: tenants table not found';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'rumman_users'
    ) THEN
        RAISE EXCEPTION 'PREFLIGHT FAILED: rumman_users not found';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'student_program_profile'
    ) THEN
        RAISE EXCEPTION 'PREFLIGHT FAILED: student_program_profile not found — run 075+076 first';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'student_course_history'
    ) THEN
        RAISE EXCEPTION 'PREFLIGHT FAILED: student_course_history not found — run 075 first';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'student_registered_sections'
    ) THEN
        RAISE EXCEPTION 'PREFLIGHT FAILED: student_registered_sections not found — run 063 first';
    END IF;

    -- 076 was applied (id column exists on student_program_profile)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'student_program_profile'
          AND column_name  = 'id'
    ) THEN
        RAISE EXCEPTION 'PREFLIGHT FAILED: student_program_profile.id missing — run 076 first';
    END IF;

    -- Default tenant exists (FK target for rumman_users.tenant_id)
    IF NOT EXISTS (
        SELECT 1 FROM public.tenants
        WHERE id = '00000000-0000-0000-0000-000000000001'
    ) THEN
        RAISE EXCEPTION 'PREFLIGHT FAILED: default tenant 00000000-... not found in tenants table';
    END IF;

    -- No stale test rows (idempotency guard — run teardown if this fails)
    IF EXISTS (SELECT 1 FROM public.rumman_users              WHERE id::text          LIKE 'eeeeeeee-%') THEN
        RAISE EXCEPTION 'PREFLIGHT FAILED: stale test rows in rumman_users — run teardown first';
    END IF;
    -- Guard: platform_user_hash values must not collide with any existing api-platform user.
    -- Without this, a conflict would surface as a UNIQUE violation inside the transaction
    -- (confusing error) instead of a clear PREFLIGHT message.
    IF EXISTS (
        SELECT 1 FROM public.rumman_users
        WHERE platform = 'api' AND platform_user_hash LIKE 'RUMMAN_SMOKE_TEST_%'
    ) THEN
        RAISE EXCEPTION 'PREFLIGHT FAILED: RUMMAN_SMOKE_TEST_%% hash already in use for platform=api — check for ghost users with unexpected UUIDs';
    END IF;
    IF EXISTS (SELECT 1 FROM public.student_program_profile   WHERE student_id::text  LIKE 'eeeeeeee-%') THEN
        RAISE EXCEPTION 'PREFLIGHT FAILED: stale test rows in student_program_profile — run teardown first';
    END IF;
    IF EXISTS (SELECT 1 FROM public.student_course_history    WHERE student_id::text  LIKE 'eeeeeeee-%') THEN
        RAISE EXCEPTION 'PREFLIGHT FAILED: stale test rows in student_course_history — run teardown first';
    END IF;
    IF EXISTS (SELECT 1 FROM public.student_registered_sections WHERE student_id::text LIKE 'eeeeeeee-%') THEN
        RAISE EXCEPTION 'PREFLIGHT FAILED: stale test rows in student_registered_sections — run teardown first';
    END IF;

    RAISE NOTICE 'PREFLIGHT OK — all assertions passed, ready to seed';
END;
$$;

BEGIN;

-- ═════════════════════════════════════════════════════════════
-- §1. rumman_users — 5 ghost rows
-- ─────────────────────────────────────────────────────────────
-- Required because student_registered_sections.student_id has
-- REFERENCES rumman_users(id) ON DELETE CASCADE.
-- student_program_profile and student_course_history have no such FK.
-- opted_into_memory=false: ghost rows; not real RUMMAN users.
-- ═════════════════════════════════════════════════════════════
INSERT INTO public.rumman_users
    (id, tenant_id, platform, platform_user_hash, opted_into_memory)
VALUES
    ('eeeeeeee-0000-0000-0000-00000000000A',
     '00000000-0000-0000-0000-000000000001',
     'api', 'RUMMAN_SMOKE_TEST_A', false),
    ('eeeeeeee-0000-0000-0000-00000000000B',
     '00000000-0000-0000-0000-000000000001',
     'api', 'RUMMAN_SMOKE_TEST_B', false),
    ('eeeeeeee-0000-0000-0000-00000000000C',
     '00000000-0000-0000-0000-000000000001',
     'api', 'RUMMAN_SMOKE_TEST_C', false),
    ('eeeeeeee-0000-0000-0000-00000000000D',
     '00000000-0000-0000-0000-000000000001',
     'api', 'RUMMAN_SMOKE_TEST_D', false),
    ('eeeeeeee-0000-0000-0000-00000000000E',
     '00000000-0000-0000-0000-000000000001',
     'api', 'RUMMAN_SMOKE_TEST_E', false);


-- ═════════════════════════════════════════════════════════════
-- §2. student_program_profile — 6 rows
-- ═════════════════════════════════════════════════════════════

-- ─── Student A — MGT bachelor (1 active row) ─────────────────
-- Tests: ENG001=16cr (not 8), withdrawn ACCT101 excluded, dropped STAT101 excluded
INSERT INTO public.student_program_profile
    (student_id, tenant_id, program_code, source,
     is_active, started_at, ended_at, change_reason)
VALUES
    ('eeeeeeee-0000-0000-0000-00000000000A',
     '00000000-0000-0000-0000-000000000001',
     'MGT', 'banner_sync',
     true, '2024-09-01', NULL, 'RUMMAN_SMOKE_TEST');

-- ─── Student B — CS (active) + MGT (inactive) — program transfer ──
-- Tests: active profile CS drives ENG001=8cr (not 16), MGT profile present but inactive.
-- Partial-unique index uq_student_program_profile_one_active:
--   two rows for same student_id — valid because only one is is_active=true.
INSERT INTO public.student_program_profile
    (student_id, tenant_id, program_code, source,
     is_active, started_at, ended_at, change_reason)
VALUES
    -- MGT first (older) — inactive
    ('eeeeeeee-0000-0000-0000-00000000000B',
     '00000000-0000-0000-0000-000000000001',
     'MGT', 'banner_sync',
     false, '2023-09-01', '2024-06-01',
     'program transfer to CS (RUMMAN_SMOKE_TEST)'),
    -- CS second (current) — active
    ('eeeeeeee-0000-0000-0000-00000000000B',
     '00000000-0000-0000-0000-000000000001',
     'CS', 'banner_sync',
     true, '2024-09-01', NULL, 'RUMMAN_SMOKE_TEST');

-- ─── Student C — PH bachelor (active, needs_review) ──────────
-- Tests: 404 NOT raised for needs_review; Arabic canonical codes; نجل001=16cr
INSERT INTO public.student_program_profile
    (student_id, tenant_id, program_code, source,
     is_active, started_at, ended_at, change_reason)
VALUES
    ('eeeeeeee-0000-0000-0000-00000000000C',
     '00000000-0000-0000-0000-000000000001',
     'PH', 'banner_sync',
     true, '2024-09-01', NULL, 'RUMMAN_SMOKE_TEST');

-- ─── Student D — MBA master (active) ─────────────────────────
-- Tests: total=36cr (not 130); rc=21; MGT520 transferred counted
INSERT INTO public.student_program_profile
    (student_id, tenant_id, program_code, source,
     is_active, started_at, ended_at, change_reason)
VALUES
    ('eeeeeeee-0000-0000-0000-00000000000D',
     '00000000-0000-0000-0000-000000000001',
     'MBA', 'self_declared',
     true, '2024-09-01', NULL, 'RUMMAN_SMOKE_TEST');

-- ─── Student E — BUSINESS_ADMINISTRATION diploma (guard test) ─
-- Tests: all 5 endpoints must return 404 (not 200/empty).
-- BUSINESS_ADMINISTRATION is in v_draft_catalog_future_programs (future/diploma),
-- not in v_draft_catalog_programs (active) → _require_catalog_program returns 404.
INSERT INTO public.student_program_profile
    (student_id, tenant_id, program_code, source,
     is_active, started_at, ended_at, change_reason)
VALUES
    ('eeeeeeee-0000-0000-0000-00000000000E',
     '00000000-0000-0000-0000-000000000001',
     'BUSINESS_ADMINISTRATION', 'inferred',
     true, NULL, NULL, 'RUMMAN_SMOKE_TEST');


-- ═════════════════════════════════════════════════════════════
-- §3. student_course_history — 32 rows
-- ═════════════════════════════════════════════════════════════

-- ─── Student A — MGT — 8 rows ─────────────────────────────────
-- Expected completed_credits = 16+3+3+2+2+2+3 = 31
-- ACCT101: is_counted=false (withdrawn) → not returned by is_counted=eq.true query
-- All credits from cat_program_courses.credit_hours for program_code=MGT
INSERT INTO public.student_course_history
    (student_id, tenant_id, term_code,
     banner_course_code, canonical_course_code,
     course_state, is_counted, source, confidence, verified_by_student, notes)
VALUES
    ('eeeeeeee-0000-0000-0000-00000000000A', '00000000-0000-0000-0000-000000000001',
     '202410', 'ENG001',  'ENG001',  'passed',    true,  'banner_sync', 'high',   true,  'RUMMAN_SMOKE_TEST'),
    ('eeeeeeee-0000-0000-0000-00000000000A', '00000000-0000-0000-0000-000000000001',
     '202410', 'MATH001', 'MATH001', 'passed',    true,  'banner_sync', 'high',   false, 'RUMMAN_SMOKE_TEST'),
    ('eeeeeeee-0000-0000-0000-00000000000A', '00000000-0000-0000-0000-000000000001',
     '202410', 'CS001',   'CS001',   'passed',    true,  'banner_sync', 'high',   false, 'RUMMAN_SMOKE_TEST'),
    ('eeeeeeee-0000-0000-0000-00000000000A', '00000000-0000-0000-0000-000000000001',
     '202420', 'CI001',   'CI001',   'passed',    true,  'banner_sync', 'high',   false, 'RUMMAN_SMOKE_TEST'),
    ('eeeeeeee-0000-0000-0000-00000000000A', '00000000-0000-0000-0000-000000000001',
     '202420', 'COMM001', 'COMM001', 'passed',    true,  'banner_sync', 'high',   false, 'RUMMAN_SMOKE_TEST'),
    ('eeeeeeee-0000-0000-0000-00000000000A', '00000000-0000-0000-0000-000000000001',
     '202430', 'ISLM101', 'ISLM101', 'passed',    true,  'banner_sync', 'high',   false, 'RUMMAN_SMOKE_TEST'),
    ('eeeeeeee-0000-0000-0000-00000000000A', '00000000-0000-0000-0000-000000000001',
     '202510', 'LAW101',  'LAW101',  'passed',    true,  'banner_sync', 'medium', false, 'RUMMAN_SMOKE_TEST'),
    -- ACCT101: withdrawn → is_counted=false → absent from /completed and credit sum
    ('eeeeeeee-0000-0000-0000-00000000000A', '00000000-0000-0000-0000-000000000001',
     '202510', 'ACCT101', 'ACCT101', 'withdrawn', false, 'banner_sync', 'high',   false, 'RUMMAN_SMOKE_TEST');

-- ─── Student B — CS (transferred from MGT) — 12 rows ──────────
-- Expected completed_credits = 8+8+3+3+2+2+2+3+3 = 34
-- CS230(failed,is_counted=false) + CS231(failed,is_counted=false) + TRNS200(null canonical) = 0cr
-- completed_courses_count = 10 (all is_counted=true rows, including TRNS200)
--
-- UNIQUE(student_id, term_code, banner_course_code):
--   CS230 appears twice — different terms (202430 fail, 202510 retake) → constraint satisfied.
INSERT INTO public.student_course_history
    (student_id, tenant_id, term_code,
     banner_course_code, canonical_course_code,
     course_state, is_counted, source, confidence, verified_by_student, notes)
VALUES
    -- ENG001 = 8cr in CS (not 16) — shared-course-per-program probe
    ('eeeeeeee-0000-0000-0000-00000000000B', '00000000-0000-0000-0000-000000000001',
     '202410', 'ENG001',  'ENG001',  'passed',   true,  'banner_sync', 'high', false, 'RUMMAN_SMOKE_TEST'),
    ('eeeeeeee-0000-0000-0000-00000000000B', '00000000-0000-0000-0000-000000000001',
     '202420', 'ENG002',  'ENG002',  'passed',   true,  'banner_sync', 'high', false, 'RUMMAN_SMOKE_TEST'),
    ('eeeeeeee-0000-0000-0000-00000000000B', '00000000-0000-0000-0000-000000000001',
     '202410', 'MATH001', 'MATH001', 'passed',   true,  'banner_sync', 'high', false, 'RUMMAN_SMOKE_TEST'),
    ('eeeeeeee-0000-0000-0000-00000000000B', '00000000-0000-0000-0000-000000000001',
     '202410', 'CS001',   'CS001',   'passed',   true,  'banner_sync', 'high', false, 'RUMMAN_SMOKE_TEST'),
    ('eeeeeeee-0000-0000-0000-00000000000B', '00000000-0000-0000-0000-000000000001',
     '202420', 'CI001',   'CI001',   'passed',   true,  'banner_sync', 'high', false, 'RUMMAN_SMOKE_TEST'),
    ('eeeeeeee-0000-0000-0000-00000000000B', '00000000-0000-0000-0000-000000000001',
     '202420', 'COMM001', 'COMM001', 'passed',   true,  'banner_sync', 'high', false, 'RUMMAN_SMOKE_TEST'),
    ('eeeeeeee-0000-0000-0000-00000000000B', '00000000-0000-0000-0000-000000000001',
     '202430', 'ISLM101', 'ISLM101', 'passed',   true,  'banner_sync', 'high', false, 'RUMMAN_SMOKE_TEST'),
    -- CS230 attempt 1: failed → is_counted=false (superseded by retake)
    ('eeeeeeee-0000-0000-0000-00000000000B', '00000000-0000-0000-0000-000000000001',
     '202430', 'CS230',   'CS230',   'failed',   false, 'banner_sync', 'high', false, 'RUMMAN_SMOKE_TEST'),
    -- CS230 attempt 2: retake passes → is_counted=true; counted exactly once
    ('eeeeeeee-0000-0000-0000-00000000000B', '00000000-0000-0000-0000-000000000001',
     '202510', 'CS230',   'CS230',   'repeated', true,  'banner_sync', 'high', false, 'RUMMAN_SMOKE_TEST'),
    -- CS231: failed, only attempt → prerequisite gap for CS241 (future prereq layer must flag)
    ('eeeeeeee-0000-0000-0000-00000000000B', '00000000-0000-0000-0000-000000000001',
     '202510', 'CS231',   'CS231',   'failed',   false, 'banner_sync', 'high', false, 'RUMMAN_SMOKE_TEST'),
    ('eeeeeeee-0000-0000-0000-00000000000B', '00000000-0000-0000-0000-000000000001',
     '202510', 'MATH150', 'MATH150', 'passed',   true,  'banner_sync', 'high', false, 'RUMMAN_SMOKE_TEST'),
    -- TRNS200: transfer from prior institution, canonical=NULL (P9 backfill gap)
    -- is_counted=true but credit_hours=null (no catalog match) → 0cr, warning=canonical_code_missing
    ('eeeeeeee-0000-0000-0000-00000000000B', '00000000-0000-0000-0000-000000000001',
     '000000', 'TRNS200', NULL,      'passed',   true,  'banner_sync', 'low',  false, 'RUMMAN_SMOKE_TEST');

-- ─── Student C — PH (Arabic codes, needs_review) — 7 rows ────
-- Expected completed_credits = 16+3+3+2+2+3+3 = 32
-- ENG001 (Banner code) → canonical نجل001 (alias resolved by banner_sync)
-- صحة101 exempted (معادلة from prior credential) → is_counted=true
-- PH is needs_review → API must return 200 + needs_review_program warning (not 404)
INSERT INTO public.student_course_history
    (student_id, tenant_id, term_code,
     banner_course_code, canonical_course_code,
     course_state, is_counted, source, confidence, verified_by_student, notes)
VALUES
    -- ENG001 → نجل001 (16cr in PH): banner stores Latin code, canonical is Arabic alias
    ('eeeeeeee-0000-0000-0000-00000000000C', '00000000-0000-0000-0000-000000000001',
     '202410', 'ENG001', 'نجل001',  'passed',   true,  'banner_sync',    'high', false, 'RUMMAN_SMOKE_TEST'),
    ('eeeeeeee-0000-0000-0000-00000000000C', '00000000-0000-0000-0000-000000000001',
     '202410', 'ريض001', 'ريض001',  'passed',   true,  'banner_sync',    'high', false, 'RUMMAN_SMOKE_TEST'),
    ('eeeeeeee-0000-0000-0000-00000000000C', '00000000-0000-0000-0000-000000000001',
     '202410', 'عال001', 'عال001',  'passed',   true,  'banner_sync',    'high', false, 'RUMMAN_SMOKE_TEST'),
    ('eeeeeeee-0000-0000-0000-00000000000C', '00000000-0000-0000-0000-000000000001',
     '202420', 'علم001', 'علم001',  'passed',   true,  'banner_sync',    'high', false, 'RUMMAN_SMOKE_TEST'),
    ('eeeeeeee-0000-0000-0000-00000000000C', '00000000-0000-0000-0000-000000000001',
     '202420', 'نهج001', 'نهج001',  'passed',   true,  'banner_sync',    'high', false, 'RUMMAN_SMOKE_TEST'),
    ('eeeeeeee-0000-0000-0000-00000000000C', '00000000-0000-0000-0000-000000000001',
     '202430', 'حيا101', 'حيا101',  'passed',   true,  'banner_sync',    'high', false, 'RUMMAN_SMOKE_TEST'),
    -- صحة101: exempted via معادلة; term_code=000000 (pre-enrollment); is_counted=true → counts
    ('eeeeeeee-0000-0000-0000-00000000000C', '00000000-0000-0000-0000-000000000001',
     '000000', 'صحة101', 'صحة101',  'exempted', true,  'student_import', 'high', true,  'RUMMAN_SMOKE_TEST');

-- ─── Student D — MBA master — 5 rows ─────────────────────────
-- Expected completed_credits = 5 × 3cr = 15
-- remaining = 36 - 15 = 21  (total=36, NOT 130)
-- MGT520 transferred → is_counted=true → counts toward cc
INSERT INTO public.student_course_history
    (student_id, tenant_id, term_code,
     banner_course_code, canonical_course_code,
     course_state, is_counted, source, confidence, verified_by_student, notes)
VALUES
    ('eeeeeeee-0000-0000-0000-00000000000D', '00000000-0000-0000-0000-000000000001',
     '202410', 'ECN500', 'ECN500', 'passed',      true, 'banner_sync',    'high', false, 'RUMMAN_SMOKE_TEST'),
    ('eeeeeeee-0000-0000-0000-00000000000D', '00000000-0000-0000-0000-000000000001',
     '202410', 'FIN500', 'FIN500', 'passed',      true, 'banner_sync',    'high', false, 'RUMMAN_SMOKE_TEST'),
    ('eeeeeeee-0000-0000-0000-00000000000D', '00000000-0000-0000-0000-000000000001',
     '202420', 'RES500', 'RES500', 'passed',      true, 'banner_sync',    'high', false, 'RUMMAN_SMOKE_TEST'),
    ('eeeeeeee-0000-0000-0000-00000000000D', '00000000-0000-0000-0000-000000000001',
     '202420', 'MGT510', 'MGT510', 'passed',      true, 'banner_sync',    'high', false, 'RUMMAN_SMOKE_TEST'),
    -- MGT520: transferred credit (معادلة) — term_code=000000 (pre-enrollment)
    ('eeeeeeee-0000-0000-0000-00000000000D', '00000000-0000-0000-0000-000000000001',
     '000000', 'MGT520', 'MGT520', 'transferred', true, 'student_import', 'high', true,  'RUMMAN_SMOKE_TEST');

-- Student E: no history rows (API returns 404 before reading history)


-- ═════════════════════════════════════════════════════════════
-- §4. student_registered_sections — 7 rows
-- ═════════════════════════════════════════════════════════════
-- FK student_id → rumman_users(id): ghost rows inserted in §1.
-- source='manual' — the only non-smart_registration value allowed by CHECK.
-- CRNs: 99100-99102 (A), 99200-99201 (B), 99300 (C), 99400 (D) — fake, no conflict risk.

-- ─── Student A: 2 active + 1 dropped ─────────────────────────
-- STAT101 (dropped) MUST be excluded by status=in.(active,approved) filter.
-- expected current_courses_count = 2 (not 3)
INSERT INTO public.student_registered_sections
    (student_id, tenant_id, term_code, crn,
     banner_course_code, canonical_course_code, status, source)
VALUES
    ('eeeeeeee-0000-0000-0000-00000000000A', '00000000-0000-0000-0000-000000000001',
     '202520', '99100', 'MGT101',  'MGT101',  'active',  'manual'),
    ('eeeeeeee-0000-0000-0000-00000000000A', '00000000-0000-0000-0000-000000000001',
     '202520', '99101', 'ECON101', 'ECON101', 'active',  'manual'),
    -- dropped: status filter in.(active,approved) must exclude this row
    ('eeeeeeee-0000-0000-0000-00000000000A', '00000000-0000-0000-0000-000000000001',
     '202520', '99102', 'STAT101', 'STAT101', 'dropped', 'manual');

-- ─── Student B: 2 active ──────────────────────────────────────
-- CS241 registered despite CS231 failed-only: prereq gap (future prereq layer must flag)
INSERT INTO public.student_registered_sections
    (student_id, tenant_id, term_code, crn,
     banner_course_code, canonical_course_code, status, source)
VALUES
    ('eeeeeeee-0000-0000-0000-00000000000B', '00000000-0000-0000-0000-000000000001',
     '202520', '99200', 'CS240', 'CS240', 'active', 'manual'),
    ('eeeeeeee-0000-0000-0000-00000000000B', '00000000-0000-0000-0000-000000000001',
     '202520', '99201', 'CS241', 'CS241', 'active', 'manual');

-- ─── Student C: 1 active (Arabic course code) ─────────────────
INSERT INTO public.student_registered_sections
    (student_id, tenant_id, term_code, crn,
     banner_course_code, canonical_course_code, status, source)
VALUES
    ('eeeeeeee-0000-0000-0000-00000000000C', '00000000-0000-0000-0000-000000000001',
     '202520', '99300', 'حيا102', 'حيا102', 'active', 'manual');

-- ─── Student D: 1 active ──────────────────────────────────────
INSERT INTO public.student_registered_sections
    (student_id, tenant_id, term_code, crn,
     banner_course_code, canonical_course_code, status, source)
VALUES
    ('eeeeeeee-0000-0000-0000-00000000000D', '00000000-0000-0000-0000-000000000001',
     '202520', '99400', 'MGT560', 'MGT560', 'active', 'manual');

-- Student E: no sections (guard — API returns 404 before any section read)


-- ═════════════════════════════════════════════════════════════
-- POST-CHECK
-- ═════════════════════════════════════════════════════════════
DO $$
DECLARE
    cnt_users    INT;
    cnt_profile  INT;
    cnt_history  INT;
    cnt_sections INT;
    cnt_b_active INT;
BEGIN
    SELECT COUNT(*) INTO cnt_users    FROM public.rumman_users               WHERE id::text          LIKE 'eeeeeeee-%';
    SELECT COUNT(*) INTO cnt_profile  FROM public.student_program_profile    WHERE student_id::text  LIKE 'eeeeeeee-%';
    SELECT COUNT(*) INTO cnt_history  FROM public.student_course_history     WHERE student_id::text  LIKE 'eeeeeeee-%';
    SELECT COUNT(*) INTO cnt_sections FROM public.student_registered_sections WHERE student_id::text LIKE 'eeeeeeee-%';

    IF cnt_users != 5 THEN
        RAISE EXCEPTION 'POST-CHECK FAILED: rumman_users — expected 5, got %', cnt_users;
    END IF;
    IF cnt_profile != 6 THEN
        RAISE EXCEPTION 'POST-CHECK FAILED: student_program_profile — expected 6 (A×1 B×2 C×1 D×1 E×1), got %', cnt_profile;
    END IF;
    IF cnt_history != 32 THEN
        RAISE EXCEPTION 'POST-CHECK FAILED: student_course_history — expected 32 (A:8 B:12 C:7 D:5 E:0), got %', cnt_history;
    END IF;
    IF cnt_sections != 7 THEN
        RAISE EXCEPTION 'POST-CHECK FAILED: student_registered_sections — expected 7 (A:3 B:2 C:1 D:1 E:0), got %', cnt_sections;
    END IF;

    -- Partial unique index: exactly 1 active profile per student
    -- Student B must have 1 active (CS) and 1 inactive (MGT)
    SELECT COUNT(*) INTO cnt_b_active
    FROM public.student_program_profile
    WHERE student_id = 'eeeeeeee-0000-0000-0000-00000000000B'
      AND is_active  = true;
    IF cnt_b_active != 1 THEN
        RAISE EXCEPTION 'POST-CHECK FAILED: Student B must have exactly 1 active profile, found %', cnt_b_active;
    END IF;

    RAISE NOTICE 'POST-CHECK OK — users=%, profile=%, history=%, sections=%',
        cnt_users, cnt_profile, cnt_history, cnt_sections;
    RAISE NOTICE 'Seed complete. Run smoke tests, then execute teardown.';
END;
$$;

COMMIT;


-- ════════════════════════════════════════════════════════════════════════════
-- TEARDOWN — run AFTER smoke tests pass, NOT before
-- ════════════════════════════════════════════════════════════════════════════
-- Deletes all rows inserted by this seed.
--
-- Order matters:
--   1. student_registered_sections  (FK → rumman_users; explicit before cascade)
--   2. student_course_history       (no FK — explicit)
--   3. student_program_profile      (no FK — explicit)
--   4. rumman_users                 (ON DELETE CASCADE would handle sections,
--                                    but explicit deletes above make teardown auditable)
--
-- Single-predicate: only touches UUIDs in the eeeeeeee-... block.
-- Real users / real students are never in this UUID range.
--
-- BEGIN;
-- DELETE FROM public.student_registered_sections  WHERE student_id::text LIKE 'eeeeeeee-%';
-- DELETE FROM public.student_course_history       WHERE student_id::text LIKE 'eeeeeeee-%';
-- DELETE FROM public.student_program_profile      WHERE student_id::text LIKE 'eeeeeeee-%';
-- DELETE FROM public.rumman_users                 WHERE id::text          LIKE 'eeeeeeee-%';
-- COMMIT;
