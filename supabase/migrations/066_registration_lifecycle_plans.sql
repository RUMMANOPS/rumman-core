-- Migration 066: Registration Lifecycle (plans) -- lifecycle ONLY (sync policy lives in 067)
--
-- DRAFT. Applied manually after founder approval.
-- Additive + safe constraint widening (student_registered_sections has 0 rows - verified).
-- No hard deletes anywhere; lifecycle handled by statuses.
--
-- Redefines the registration model:
--   suggested (client-only, not stored)
--   -> pinned  (= pending_university_registration; live-rechecked + conflict-free in RUMMAN)
--   -> confirmed (= officially_confirmed_by_student in Banner)  <- ONLY this drives Courses/Today/Calendar
--   side states: registration_failed | superseded | abandoned | needs_review
-- SEU default tenant: 00000000-0000-0000-0000-000000000001

-- student_registration_plans (plan-level lifecycle)
CREATE TABLE IF NOT EXISTS student_registration_plans (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id          UUID        NOT NULL REFERENCES rumman_users(id) ON DELETE CASCADE,
    tenant_id           UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    term_code           TEXT        NOT NULL,

    status              TEXT        NOT NULL DEFAULT 'pinned'
                            CHECK (status IN ('pinned', 'confirmed', 'registration_failed',
                                              'superseded', 'abandoned', 'needs_review')),
    source              TEXT        NOT NULL DEFAULT 'smart_registration'
                            CHECK (source IN ('smart_registration', 'manual')),

    crns                TEXT[]      NOT NULL DEFAULT '{}',   -- snapshot of chosen CRNs at pin time

    -- lifecycle timestamps (audit trail; nothing is hard-deleted)
    prevalidated_at     TIMESTAMPTZ,   -- passed conflict + live re-check
    pinned_at           TIMESTAMPTZ,
    confirmed_at        TIMESTAMPTZ,   -- student confirmed Banner registration
    failed_at           TIMESTAMPTZ,
    superseded_at       TIMESTAMPTZ,
    abandoned_at        TIMESTAMPTZ,

    metadata            JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- At most ONE active plan (pinned or confirmed) per student per term.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_plan_per_student_term
    ON student_registration_plans (student_id, term_code)
    WHERE status IN ('pinned', 'confirmed');

CREATE INDEX IF NOT EXISTS idx_srp_student_term_status
    ON student_registration_plans (student_id, term_code, status);

COMMENT ON TABLE  student_registration_plans IS 'Registration plan lifecycle. confirmed = student registered in Banner (only this drives the app). pinned = pre-confirmed in RUMMAN.';
COMMENT ON COLUMN student_registration_plans.status IS 'pinned=pending_university_registration; confirmed=officially_confirmed_by_student; registration_failed=student could not register; superseded=replaced by newer plan';


-- student_registered_sections: link to plan + widen section status
-- Section-level status is now active|dropped|needs_review (NOT the plan lifecycle).
-- Widen the existing CHECK (keep legacy values so the change is non-destructive; 0 rows anyway).

ALTER TABLE student_registered_sections
    ADD COLUMN IF NOT EXISTS plan_id UUID REFERENCES student_registration_plans(id) ON DELETE SET NULL;

-- Drop ANY existing CHECK constraint on the status column by catalog lookup,
-- NOT by assumed name: if production named it differently, a DROP ... IF EXISTS
-- on a guessed name would no-op and the old (narrower) constraint would silently
-- survive and reject 'active'. This guarantees the new constraint is the only one.
DO $$
DECLARE c text;
BEGIN
    FOR c IN
        SELECT con.conname
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        WHERE rel.relname = 'student_registered_sections'
          AND con.contype = 'c'
          AND pg_get_constraintdef(con.oid) ILIKE '%status%'
    LOOP
        EXECUTE format('ALTER TABLE student_registered_sections DROP CONSTRAINT %I', c);
    END LOOP;
END $$;

ALTER TABLE student_registered_sections
    ADD CONSTRAINT student_registered_sections_status_check
        CHECK (status IN ('active', 'dropped', 'needs_review', 'planned', 'approved'));  -- 'planned'/'approved' kept for backward-compat

CREATE INDEX IF NOT EXISTS idx_srs_plan
    ON student_registered_sections (plan_id)
    WHERE plan_id IS NOT NULL;

COMMENT ON COLUMN student_registered_sections.plan_id IS 'FK to the registration plan this section belongs to (lifecycle lives on the plan)';

-- NOTE: section status keeps legacy 'planned'/'approved' ONLY for transient backward-compat
-- with the currently-deployed /registration/approve endpoint. Once pin/confirm replace it
-- (writing 'active'), a later migration will drop 'planned'/'approved'. Final section
-- statuses = active | dropped | needs_review. Registration window + sync_policy live in 067.
