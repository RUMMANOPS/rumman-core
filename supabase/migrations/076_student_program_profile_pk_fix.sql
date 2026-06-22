-- ============================================================
-- Migration 076: Fix student_program_profile PK
-- DRAFT — do not apply without explicit approval
-- ============================================================
-- Context:
--   075 created student_program_profile with student_id as PK.
--   This prevents storing program-transfer history and makes the
--   intended is_active flag meaningless (only one row ever exists).
--
-- This migration:
--   1. Drops the old PK constraint (student_id)
--   2. Adds id UUID as new PK
--   3. Adds started_at, ended_at, change_reason columns
--   4. Creates partial unique index: one active program per student
--
-- Tables are empty (0 rows) — safe to restructure without data migration.
-- ============================================================

BEGIN;

-- ────────────────────────────────────────────────────────────
-- PREFLIGHT ASSERTIONS
-- ────────────────────────────────────────────────────────────
DO $$
DECLARE
    row_count INT;
    col_exists BOOLEAN;
BEGIN
    -- Assert: table exists
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'student_program_profile'
    ) THEN
        RAISE EXCEPTION 'PREFLIGHT FAILED: student_program_profile does not exist — run 075 first';
    END IF;

    -- Assert: table is empty (safe to restructure)
    SELECT COUNT(*) INTO row_count FROM public.student_program_profile;
    IF row_count > 0 THEN
        RAISE EXCEPTION 'PREFLIGHT FAILED: student_program_profile has % rows — cannot restructure with data', row_count;
    END IF;

    -- Assert: current PK is on student_id (migration not already applied)
    SELECT EXISTS (
        SELECT 1 FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
           AND tc.table_schema    = kcu.table_schema
        WHERE tc.table_schema    = 'public'
          AND tc.table_name      = 'student_program_profile'
          AND tc.constraint_type = 'PRIMARY KEY'
          AND kcu.column_name    = 'student_id'
    ) INTO col_exists;
    IF NOT col_exists THEN
        RAISE EXCEPTION 'PREFLIGHT FAILED: PK is not on student_id — migration may already be applied';
    END IF;

    -- Assert: id column does not already exist
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'student_program_profile'
          AND column_name  = 'id'
    ) INTO col_exists;
    IF col_exists THEN
        RAISE EXCEPTION 'PREFLIGHT FAILED: column id already exists — migration may already be applied';
    END IF;

    RAISE NOTICE 'PREFLIGHT OK: table empty, PK on student_id, id column absent';
END;
$$;

-- ────────────────────────────────────────────────────────────
-- PART A: Drop old PK constraint
-- ────────────────────────────────────────────────────────────
ALTER TABLE public.student_program_profile
    DROP CONSTRAINT pk_student_program_profile;

-- ────────────────────────────────────────────────────────────
-- PART B: Add new id column as PK
-- ────────────────────────────────────────────────────────────
ALTER TABLE public.student_program_profile
    ADD COLUMN id UUID NOT NULL DEFAULT gen_random_uuid();

ALTER TABLE public.student_program_profile
    ADD CONSTRAINT pk_student_program_profile PRIMARY KEY (id);

-- ────────────────────────────────────────────────────────────
-- PART C: Add history columns
-- ────────────────────────────────────────────────────────────
ALTER TABLE public.student_program_profile
    ADD COLUMN started_at    TIMESTAMPTZ NULL,
    ADD COLUMN ended_at      TIMESTAMPTZ NULL,
    ADD COLUMN change_reason TEXT        NULL;

-- ────────────────────────────────────────────────────────────
-- PART D: Drop old single-student index, add partial unique index
-- ────────────────────────────────────────────────────────────
DROP INDEX IF EXISTS public.idx_spp_active;

-- One active program per student at any time
CREATE UNIQUE INDEX uq_student_program_profile_one_active
    ON public.student_program_profile (student_id)
    WHERE is_active = true;

-- student lookup index (student_id no longer PK, needs explicit index)
CREATE INDEX idx_spp_student
    ON public.student_program_profile (student_id);

-- ────────────────────────────────────────────────────────────
-- POST-CHECK
-- ────────────────────────────────────────────────────────────
DO $$
DECLARE
    pk_col     TEXT;
    idx_exists BOOLEAN;
BEGIN
    -- Verify PK is now on id
    SELECT kcu.column_name INTO pk_col
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
        ON tc.constraint_name = kcu.constraint_name
       AND tc.table_schema    = kcu.table_schema
    WHERE tc.table_schema    = 'public'
      AND tc.table_name      = 'student_program_profile'
      AND tc.constraint_type = 'PRIMARY KEY'
    LIMIT 1;

    IF pk_col IS DISTINCT FROM 'id' THEN
        RAISE EXCEPTION 'POST-CHECK FAILED: PK column is %, expected id', pk_col;
    END IF;

    -- Verify partial unique index exists
    SELECT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename  = 'student_program_profile'
          AND indexname  = 'uq_student_program_profile_one_active'
    ) INTO idx_exists;

    IF NOT idx_exists THEN
        RAISE EXCEPTION 'POST-CHECK FAILED: partial unique index uq_student_program_profile_one_active not found';
    END IF;

    -- Verify new columns exist
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'student_program_profile'
          AND column_name  = 'started_at'
    ) THEN
        RAISE EXCEPTION 'POST-CHECK FAILED: column started_at missing';
    END IF;

    RAISE NOTICE 'POST-CHECK OK: PK=id, partial unique index present, history columns added';
END;
$$;

COMMIT;
