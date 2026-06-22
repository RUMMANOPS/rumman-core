-- 074_validate_official_catalog.DRAFT.sql
-- DRAFT — DO NOT APPLY. DO NOT COPY TO supabase/migrations WITHOUT EXPLICIT APPROVAL.
-- DO NOT COMMIT / DO NOT PUSH / DO NOT DEPLOY from this file.
--
-- Phase 074: promote the official catalog version draft -> validated.
-- This is the QA-gate step in the lifecycle:  draft -> [validated] -> active -> archived
-- (lifecycle defined in migration 070, catalog_versions.status CHECK).
--
-- WHAT THIS MIGRATION DOES:
--   PART A  Asserts every QA gate (13 checks) inside a DO block. Any failure RAISES and
--           rolls back the whole transaction — nothing changes unless ALL gates are green.
--   PART B  UPDATE catalog_versions SET status='validated', validated_at=now()
--           for tenant 00000000-0000-0000-0000-000000000001 / version 'official-2026-06'
--           ONLY while it is still 'draft' (idempotent guard).
--   PART C  REQUIRED COMPANION — refreshes the 8 existing v_draft_catalog_* QA views
--           (migration 073) so their status filter includes 'validated'. WITHOUT PART C
--           the live catalog API (app/catalog_api.py) returns EMPTY the moment status
--           leaves 'draft', because those views filter cv.status IN ('draft','active').
--           See the BLOCKER note below.
--
-- WHAT THIS MIGRATION DOES *NOT* DO (hard scope boundaries):
--   * NO activation. status goes to 'validated', NOT 'active'. The partial-unique
--     index uq_catalog_versions_one_active (status='active') is never touched.
--   * NO production views. PART C only REPLACES the existing QA views (v_draft_catalog_*);
--     it does NOT create any new v_catalog_* production surface.
--   * NO backend / app code changes.
--   * NO data edits to any cat_* row (no INSERT/DELETE/UPDATE of catalog content).
--   * NO inst_* tables touched. inst_colleges (telegram_chat_ids) stays authoritative.
--   * NO hardcoded version UUID — the version is addressed by (tenant_id, version_code).
--
-- ─────────────────────────────────────────────────────────────────────────────
-- BLOCKER (discovered during 074 design) — WHY PART C IS MANDATORY
-- ─────────────────────────────────────────────────────────────────────────────
-- The 8 QA views from migration 073 all end with:
--       WHERE cv.status IN ('draft', 'active')
-- The live catalog API reads programs/courses/prerequisites/aliases EXCLUSIVELY from
-- those views. 'validated' is NOT in that list, so as soon as PART B runs, every
-- view returns 0 rows and the deployed API answers empty (only /version, which reads
-- catalog_versions directly, keeps working). PART C widens the filter to
--       WHERE cv.status IN ('draft', 'validated', 'active')
-- keeping the API alive across the validated state. Apply PART B and PART C together
-- (single transaction below) — never PART B alone.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- VERIFIED PRE-WRITE STATE (read-only, 2026-06-22):
--   catalog_versions: 1 row — version_code='official-2026-06', status='draft',
--                     id=03325ec9-... (NOT hardcoded here), validated_at=NULL, activated_at=NULL
--   active versions for tenant: 0
--   v_draft_catalog_colleges          = 5
--   v_draft_catalog_programs          = 19   (11 bachelor / 6 master / 2 executive_master)
--   v_draft_catalog_program_courses   = 580
--   v_draft_catalog_courses           = 663
--   v_draft_catalog_prerequisites     = 494  (0 needs_review, 0 unresolved)
--   v_draft_catalog_elective_groups   = 16
--   v_draft_catalog_aliases           = 26   (all latin_code)
--   v_draft_catalog_future_programs   = 16   (diplomas)
--   LAW in active programs view       = 0    (support_level='reference')
--   diplomas in active programs view  = 0
--   PH / MDM / FIN                    = present, all program_status='needs_review'
--   Live API /version                 = 200, status='draft'
-- ─────────────────────────────────────────────────────────────────────────────

BEGIN;

-- ═════════════════════════════════════════════════════════════════════════════
-- PART A — QA GATE. Every assertion must pass or the whole transaction rolls back.
-- ═════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    v_tenant        UUID := '00000000-0000-0000-0000-000000000001';
    v_version       TEXT := 'official-2026-06';
    v_draft_count   INT;
    v_active_count  INT;
    n_colleges      INT;
    n_programs      INT;
    n_prog_courses  INT;
    n_courses       INT;
    n_prereqs       INT;
    n_prereqs_bad   INT;
    n_elective      INT;
    n_aliases       INT;
    n_future        INT;
    n_law           INT;
    n_diploma       INT;
    n_needs_review  INT;
    n_bachelor      INT;
    n_master        INT;
    n_exec          INT;
BEGIN
    -- Precondition 1: exactly one DRAFT version for this tenant + version_code.
    SELECT count(*) INTO v_draft_count FROM catalog_versions
      WHERE tenant_id = v_tenant AND version_code = v_version AND status = 'draft';
    IF v_draft_count <> 1 THEN
        RAISE EXCEPTION '074 ABORT: expected exactly 1 draft version (%/%), found %',
            v_tenant, v_version, v_draft_count;
    END IF;

    -- Precondition 2: no version is already active for this tenant.
    SELECT count(*) INTO v_active_count FROM catalog_versions
      WHERE tenant_id = v_tenant AND status = 'active';
    IF v_active_count <> 0 THEN
        RAISE EXCEPTION '074 ABORT: % active version(s) already exist for tenant % — 074 must not run when a release is active',
            v_active_count, v_tenant;
    END IF;

    -- QA counts from the active-rule QA views (the exact surface the API serves).
    SELECT count(*) INTO n_colleges     FROM v_draft_catalog_colleges;
    SELECT count(*) INTO n_programs      FROM v_draft_catalog_programs;
    SELECT count(*) INTO n_prog_courses  FROM v_draft_catalog_program_courses;
    SELECT count(*) INTO n_courses       FROM v_draft_catalog_courses;
    SELECT count(*) INTO n_prereqs       FROM v_draft_catalog_prerequisites;
    SELECT count(*) INTO n_elective      FROM v_draft_catalog_elective_groups;
    SELECT count(*) INTO n_aliases       FROM v_draft_catalog_aliases;
    SELECT count(*) INTO n_future        FROM v_draft_catalog_future_programs;

    IF n_colleges    <> 5   THEN RAISE EXCEPTION '074 ABORT: colleges=% (expected 5)', n_colleges; END IF;
    IF n_programs    <> 19  THEN RAISE EXCEPTION '074 ABORT: active programs=% (expected 19)', n_programs; END IF;
    IF n_prog_courses<> 580 THEN RAISE EXCEPTION '074 ABORT: program_courses=% (expected 580)', n_prog_courses; END IF;
    IF n_courses     <> 663 THEN RAISE EXCEPTION '074 ABORT: courses=% (expected 663)', n_courses; END IF;
    IF n_prereqs     <> 494 THEN RAISE EXCEPTION '074 ABORT: prerequisites=% (expected 494)', n_prereqs; END IF;
    IF n_elective    <> 16  THEN RAISE EXCEPTION '074 ABORT: elective_groups=% (expected 16)', n_elective; END IF;
    IF n_aliases     <> 26  THEN RAISE EXCEPTION '074 ABORT: aliases=% (expected 26)', n_aliases; END IF;
    IF n_future      <> 16  THEN RAISE EXCEPTION '074 ABORT: future/diploma programs=% (expected 16)', n_future; END IF;

    -- LAW must NOT appear in the active surface (support_level='reference').
    SELECT count(*) INTO n_law FROM v_draft_catalog_programs WHERE program_code = 'LAW';
    IF n_law <> 0 THEN
        RAISE EXCEPTION '074 ABORT: LAW present in active programs (% rows) — must be excluded', n_law;
    END IF;

    -- No diploma in the active surface.
    SELECT count(*) INTO n_diploma FROM v_draft_catalog_programs WHERE degree_type = 'diploma';
    IF n_diploma <> 0 THEN
        RAISE EXCEPTION '074 ABORT: % diploma program(s) in active surface — must be 0', n_diploma;
    END IF;

    -- PH / MDM / FIN present AND flagged needs_review (advisory flag, must propagate).
    SELECT count(*) INTO n_needs_review FROM v_draft_catalog_programs
      WHERE program_code IN ('PH','MDM','FIN') AND program_status = 'needs_review';
    IF n_needs_review <> 3 THEN
        RAISE EXCEPTION '074 ABORT: PH/MDM/FIN needs_review rows=% (expected 3)', n_needs_review;
    END IF;

    -- Degree mix sanity: 11 bachelor / 6 master / 2 executive_master = 19.
    SELECT count(*) INTO n_bachelor FROM v_draft_catalog_programs WHERE degree_type = 'bachelor';
    SELECT count(*) INTO n_master   FROM v_draft_catalog_programs WHERE degree_type = 'master';
    SELECT count(*) INTO n_exec     FROM v_draft_catalog_programs WHERE degree_type = 'executive_master';
    IF n_bachelor <> 11 OR n_master <> 6 OR n_exec <> 2 THEN
        RAISE EXCEPTION '074 ABORT: degree mix bachelor=%/master=%/exec=% (expected 11/6/2)',
            n_bachelor, n_master, n_exec;
    END IF;

    -- Every active prerequisite edge must be resolved (no review flag, no null canonical).
    SELECT count(*) INTO n_prereqs_bad FROM v_draft_catalog_prerequisites
      WHERE needs_review = TRUE OR requires_canonical_code IS NULL;
    IF n_prereqs_bad <> 0 THEN
        RAISE EXCEPTION '074 ABORT: % unresolved prerequisite edge(s) in active set — must be 0', n_prereqs_bad;
    END IF;

    RAISE NOTICE '074 QA GATE PASSED: 13/13 assertions green. Promoting %/% draft -> validated.',
        v_tenant, v_version;
END $$;

-- ═════════════════════════════════════════════════════════════════════════════
-- PART B — PROMOTE draft -> validated (NOT active).
--   Guarded on status='draft' so re-running is a no-op once promoted, and so it can
--   never silently downgrade an active release.
-- ═════════════════════════════════════════════════════════════════════════════
UPDATE catalog_versions
   SET status       = 'validated',
       validated_at = now()
 WHERE tenant_id    = '00000000-0000-0000-0000-000000000001'
   AND version_code = 'official-2026-06'
   AND status       = 'draft';

-- ═════════════════════════════════════════════════════════════════════════════
-- PART C — REQUIRED COMPANION: refresh the 8 QA views to recognise 'validated'.
--   Only the status filter changes: IN ('draft','active') -> IN ('draft','validated','active').
--   Column lists are IDENTICAL to migration 073, so CREATE OR REPLACE is safe.
--   These remain QA views — no production v_catalog_* surface is created here.
-- ═════════════════════════════════════════════════════════════════════════════

-- 1. colleges
CREATE OR REPLACE VIEW v_draft_catalog_colleges AS
SELECT
    col.id,
    col.tenant_id,
    col.catalog_version_id,
    col.college_code,
    col.official_name_ar,
    col.official_name_en,
    col.degree_scope,
    col.status                  AS college_status,
    cv.version_code,
    cv.status                   AS catalog_status
FROM cat_colleges col
JOIN catalog_versions cv ON cv.id = col.catalog_version_id
WHERE cv.status IN ('draft', 'validated', 'active');

-- 2. programs (active rule)
CREATE OR REPLACE VIEW v_draft_catalog_programs AS
SELECT
    p.id,
    p.tenant_id,
    p.catalog_version_id,
    p.program_code,
    p.legacy_code,
    p.degree_type,
    p.official_program_name_ar,
    p.official_program_name_en,
    p.source_program_name_raw,
    p.total_credits_official,
    p.total_credits_alt,
    p.num_levels,
    p.support_level,
    p.status                    AS program_status,
    p.metadata                  AS program_metadata,
    col.college_code,
    col.official_name_ar        AS college_name_ar,
    col.official_name_en        AS college_name_en,
    cv.version_code,
    cv.status                   AS catalog_status
FROM cat_programs p
JOIN catalog_versions cv ON cv.id = p.catalog_version_id
LEFT JOIN cat_colleges col ON col.id = p.college_id
WHERE cv.status IN ('draft', 'validated', 'active')
  AND p.support_level = 'active'
  AND p.status IN ('ready', 'needs_review');

-- 3. program_courses (active rule)
CREATE OR REPLACE VIEW v_draft_catalog_program_courses AS
SELECT
    pc.id,
    pc.tenant_id,
    pc.catalog_version_id,
    p.program_code,
    p.degree_type,
    p.official_program_name_ar,
    p.official_program_name_en,
    p.support_level             AS program_support_level,
    p.status                    AS program_status,
    p.total_credits_official,
    col.college_code,
    col.official_name_ar        AS college_name_ar,
    col.official_name_en        AS college_name_en,
    c.official_course_code_raw,
    c.canonical_course_code,
    c.normalized_course_code,
    c.official_title_ar,
    c.official_title_en,
    c.source_language,
    pc.level,
    pc.credit_hours,
    pc.category,
    pc.category_confidence,
    pc.is_required,
    pc.is_elective,
    pc.elective_group,
    pc.track,
    pc.choose_rule,
    pc.choose_count,
    pc.choose_credits,
    pc.requirement_status,
    pc.needs_human_review,
    pc.requirement_note,
    pc.official_raw_text,
    pc.source_page_or_section,
    cv.version_code,
    cv.status                   AS catalog_status
FROM cat_program_courses pc
JOIN cat_programs p   ON p.id  = pc.program_id
JOIN cat_courses  c   ON c.id  = pc.course_id
JOIN catalog_versions cv ON cv.id = pc.catalog_version_id
LEFT JOIN cat_colleges col ON col.id = p.college_id
WHERE cv.status IN ('draft', 'validated', 'active')
  AND p.support_level = 'active'
  AND p.status IN ('ready', 'needs_review');

-- 4. courses (all identity rows, no program filter)
CREATE OR REPLACE VIEW v_draft_catalog_courses AS
SELECT
    c.id,
    c.tenant_id,
    c.catalog_version_id,
    c.official_course_code_raw,
    c.normalized_course_code,
    c.canonical_course_code,
    c.official_title_ar,
    c.official_title_en,
    c.source_language,
    c.official_raw_text,
    c.metadata,
    c.created_at,
    cv.version_code,
    cv.status                   AS catalog_status
FROM cat_courses c
JOIN catalog_versions cv ON cv.id = c.catalog_version_id
WHERE cv.status IN ('draft', 'validated', 'active');

-- 5. prerequisites (active rule)
CREATE OR REPLACE VIEW v_draft_catalog_prerequisites AS
SELECT
    prereq.id,
    prereq.tenant_id,
    prereq.catalog_version_id,
    p.program_code,
    p.degree_type,
    p.support_level             AS program_support_level,
    p.status                    AS program_status,
    c.canonical_course_code     AS course_code,
    c.official_course_code_raw  AS course_code_raw,
    c.official_title_ar         AS course_title_ar,
    c.official_title_en         AS course_title_en,
    prereq.requires_code_raw,
    rc.canonical_course_code    AS requires_canonical_code,
    rc.official_course_code_raw AS requires_code_raw_official,
    rc.official_title_ar        AS requires_title_ar,
    rc.official_title_en        AS requires_title_en,
    prereq.relation,
    prereq.needs_review,
    prereq.conflict_note,
    prereq.confidence,
    prereq.raw_text,
    cv.version_code,
    cv.status                   AS catalog_status
FROM cat_prerequisites prereq
JOIN cat_programs p   ON p.id  = prereq.program_id
JOIN cat_courses  c   ON c.id  = prereq.course_id
LEFT JOIN cat_courses rc ON rc.id = prereq.requires_course_id
JOIN catalog_versions cv ON cv.id = prereq.catalog_version_id
WHERE cv.status IN ('draft', 'validated', 'active')
  AND p.support_level = 'active'
  AND p.status IN ('ready', 'needs_review');

-- 6. elective_groups (active rule)
CREATE OR REPLACE VIEW v_draft_catalog_elective_groups AS
SELECT
    eg.id,
    eg.tenant_id,
    eg.catalog_version_id,
    p.program_code,
    p.degree_type,
    p.official_program_name_ar,
    p.official_program_name_en,
    p.support_level             AS program_support_level,
    p.status                    AS program_status,
    eg.group_key,
    eg.official_name_ar,
    eg.official_name_en,
    eg.track,
    eg.choose_rule,
    eg.choose_count,
    eg.choose_credits,
    eg.requirement_status,
    eg.needs_review,
    eg.official_raw_text,
    cv.version_code,
    cv.status                   AS catalog_status
FROM cat_elective_groups eg
JOIN cat_programs p   ON p.id  = eg.program_id
JOIN catalog_versions cv ON cv.id = eg.catalog_version_id
WHERE cv.status IN ('draft', 'validated', 'active')
  AND p.support_level = 'active'
  AND p.status IN ('ready', 'needs_review');

-- 7. aliases
CREATE OR REPLACE VIEW v_draft_catalog_aliases AS
SELECT
    a.id,
    a.tenant_id,
    a.catalog_version_id,
    a.alias_label,
    a.canonical_course_code,
    c.official_course_code_raw,
    c.official_title_ar,
    c.official_title_en,
    c.source_language,
    a.alias_type,
    a.confidence,
    a.notes,
    cv.version_code,
    cv.status                   AS catalog_status
FROM cat_course_aliases a
JOIN catalog_versions cv ON cv.id = a.catalog_version_id
LEFT JOIN cat_courses c ON c.id = a.course_id
WHERE cv.status IN ('draft', 'validated', 'active');

-- 8. future_programs (diplomas; informational)
CREATE OR REPLACE VIEW v_draft_catalog_future_programs AS
SELECT
    p.id,
    p.tenant_id,
    p.catalog_version_id,
    p.program_code,
    p.degree_type,
    p.official_program_name_ar,
    p.official_program_name_en,
    p.total_credits_official,
    p.support_level,
    p.status                    AS program_status,
    col.college_code,
    col.official_name_ar        AS college_name_ar,
    col.official_name_en        AS college_name_en,
    cv.version_code,
    cv.status                   AS catalog_status
FROM cat_programs p
JOIN catalog_versions cv ON cv.id = p.catalog_version_id
LEFT JOIN cat_colleges col ON col.id = p.college_id
WHERE cv.status IN ('draft', 'validated', 'active')
  AND p.support_level = 'future';

-- ═════════════════════════════════════════════════════════════════════════════
-- PART D — POST-CHECK. Confirm the promotion + that views still serve data.
--   Rolls back the whole transaction if the end state is wrong.
-- ═════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    v_status   TEXT;
    v_valat    TIMESTAMPTZ;
    n_programs INT;
BEGIN
    SELECT status, validated_at INTO v_status, v_valat FROM catalog_versions
      WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
        AND version_code = 'official-2026-06';

    IF v_status <> 'validated' THEN
        RAISE EXCEPTION '074 POST-CHECK FAIL: status=% (expected validated)', v_status;
    END IF;
    IF v_valat IS NULL THEN
        RAISE EXCEPTION '074 POST-CHECK FAIL: validated_at is NULL after promotion';
    END IF;

    SELECT count(*) INTO n_programs FROM v_draft_catalog_programs;
    IF n_programs <> 19 THEN
        RAISE EXCEPTION '074 POST-CHECK FAIL: active programs view returns % after view refresh (expected 19 — PART C may be missing)', n_programs;
    END IF;

    RAISE NOTICE '074 POST-CHECK PASSED: status=validated, validated_at set, views serving 19 programs.';
END $$;

COMMIT;

-- ─────────────────────────────────────────────────────────────────────────────
-- POST-APPLY (manual, OUT OF SCOPE for 074 — listed for the operator only):
--   * Re-run pre-deploy smoke against the live API; /version should report
--     status='validated', is_draft=false, is_active=false, validated_at set.
--   * Activation (status='active') remains a SEPARATE, human-gated UPDATE — NOT here.
--   * The single-active partial-unique index only engages at activation, never at 'validated'.
-- ─────────────────────────────────────────────────────────────────────────────
