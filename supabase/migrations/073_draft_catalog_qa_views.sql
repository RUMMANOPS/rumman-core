-- 073_draft_catalog_qa_views.DRAFT.sql
-- DRAFT — DO NOT APPLY UNTIL REVIEWED.
-- DO NOT COPY TO supabase/migrations WITHOUT EXPLICIT APPROVAL.
--
-- Phase 073 D1: QA-only views over the official cat_* catalog.
-- Goal: inspect the new catalog data across all angles without touching inst_*.
-- These views are NOT served to the application and are NOT a backend replacement.
--
-- Active rule applied throughout:
--   cat_programs.support_level = 'active'
--   AND cat_programs.status IN ('ready', 'needs_review')
--
-- Catalog gate:
--   catalog_versions.status IN ('draft', 'active')
--   — draft is included so views return data before the activation step.
--
-- Tables touched: cat_* and catalog_versions (read-only via SELECT in view definitions).
-- Tables NOT touched: inst_colleges, inst_specializations, inst_courses, course_aliases,
--                     course_prerequisites, or any other non-cat_* table.
--
-- Views created (7):
--   1. v_draft_catalog_colleges           — official college list (QA only; no telegram_chat_ids)
--   2. v_draft_catalog_programs           — active programs with college context
--   3. v_draft_catalog_program_courses    — course-in-program rows (program_code on every row)
--   4. v_draft_catalog_courses            — course identity records (all 663, no program filter)
--   5. v_draft_catalog_prerequisites      — prereq edges with resolved code names
--   6. v_draft_catalog_elective_groups    — pool/track/concentration rules
--   7. v_draft_catalog_aliases            — Latin↔Arabic code alias pairs
-- Plus one informational view:
--   8. v_draft_catalog_future_programs    — diplomas only (support_level='future'); for info, NOT for registration

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. v_draft_catalog_colleges
--    Official college list for QA. Does NOT contain telegram_chat_ids (that
--    field lives only on inst_colleges and must stay there).
-- ─────────────────────────────────────────────────────────────────────────────
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
WHERE cv.status IN ('draft', 'active');

COMMENT ON VIEW v_draft_catalog_colleges IS
    'QA view: official colleges from the draft catalog. No telegram_chat_ids — inst_colleges is '
    'still the authoritative source for Telegram routing. catalog_status=draft until activation.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. v_draft_catalog_programs
--    Active programs (support_level=active, status IN (ready, needs_review)).
--    LAW (reference/provisional_conflicted) and all diplomas (future/ready)
--    are excluded by the active rule and do NOT appear here.
--    PH / MDM / FIN are included (active + needs_review).
-- ─────────────────────────────────────────────────────────────────────────────
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
WHERE cv.status IN ('draft', 'active')
  AND p.support_level = 'active'
  AND p.status IN ('ready', 'needs_review');

COMMENT ON VIEW v_draft_catalog_programs IS
    'QA view: programs that will be live in registration v1 '
    '(support_level=active, status IN (ready, needs_review)). '
    'LAW excluded (reference). All 16 diplomas excluded (future). '
    'PH/MDM/FIN included with program_status=needs_review.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. v_draft_catalog_program_courses
--    Every course-in-program row for active programs, with full program context
--    on each row. Does NOT deduplicate shared courses — ISLM101 in 10 programs
--    appears as 10 rows here. program_code on every row is intentional.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_draft_catalog_program_courses AS
SELECT
    pc.id,
    pc.tenant_id,
    pc.catalog_version_id,
    -- Program context (never NULL)
    p.program_code,
    p.degree_type,
    p.official_program_name_ar,
    p.official_program_name_en,
    p.support_level             AS program_support_level,
    p.status                    AS program_status,
    p.total_credits_official,
    -- College context
    col.college_code,
    col.official_name_ar        AS college_name_ar,
    col.official_name_en        AS college_name_en,
    -- Course identity (all three code forms)
    c.official_course_code_raw,
    c.canonical_course_code,
    c.normalized_course_code,
    c.official_title_ar,
    c.official_title_en,
    c.source_language,
    -- Junction values (program-specific)
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
    -- Catalog envelope
    cv.version_code,
    cv.status                   AS catalog_status
FROM cat_program_courses pc
JOIN cat_programs p   ON p.id  = pc.program_id
JOIN cat_courses  c   ON c.id  = pc.course_id
JOIN catalog_versions cv ON cv.id = pc.catalog_version_id
LEFT JOIN cat_colleges col ON col.id = p.college_id
WHERE cv.status IN ('draft', 'active')
  AND p.support_level = 'active'
  AND p.status IN ('ready', 'needs_review');

COMMENT ON VIEW v_draft_catalog_program_courses IS
    'QA view: course-in-program junction rows for active programs. '
    'program_code is on every row by design — shared courses appear once per program. '
    'All three code forms (raw / canonical / normalized) exposed. '
    'credit_hours and level are program-specific (authoritative source).';

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. v_draft_catalog_courses
--    All 663 course identity records in the draft catalog, regardless of which
--    programs they belong to. Use this to inspect course identity data directly.
--    For program context, use v_draft_catalog_program_courses instead.
-- ─────────────────────────────────────────────────────────────────────────────
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
WHERE cv.status IN ('draft', 'active');

COMMENT ON VIEW v_draft_catalog_courses IS
    'QA view: all 663 official course identity records. No program filter — '
    'includes courses from every program including LAW and diplomas. '
    'For program-scoped QA use v_draft_catalog_program_courses.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. v_draft_catalog_prerequisites
--    Prerequisite / corequisite edges for active programs.
--    Both the dependent course and the required course show all three code forms.
--    requires_canonical_code is NULL when needs_review=TRUE (unresolved edge).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_draft_catalog_prerequisites AS
SELECT
    prereq.id,
    prereq.tenant_id,
    prereq.catalog_version_id,
    -- Program context
    p.program_code,
    p.degree_type,
    p.support_level             AS program_support_level,
    p.status                    AS program_status,
    -- Dependent course (the course that HAS a prerequisite)
    c.canonical_course_code     AS course_code,
    c.official_course_code_raw  AS course_code_raw,
    c.official_title_ar         AS course_title_ar,
    c.official_title_en         AS course_title_en,
    -- Required course (the prerequisite itself)
    prereq.requires_code_raw,
    rc.canonical_course_code    AS requires_canonical_code,
    rc.official_course_code_raw AS requires_code_raw_official,
    rc.official_title_ar        AS requires_title_ar,
    rc.official_title_en        AS requires_title_en,
    -- Edge metadata
    prereq.relation,
    prereq.needs_review,
    prereq.conflict_note,
    prereq.confidence,
    prereq.raw_text,
    -- Catalog envelope
    cv.version_code,
    cv.status                   AS catalog_status
FROM cat_prerequisites prereq
JOIN cat_programs p   ON p.id  = prereq.program_id
JOIN cat_courses  c   ON c.id  = prereq.course_id
LEFT JOIN cat_courses rc ON rc.id = prereq.requires_course_id
JOIN catalog_versions cv ON cv.id = prereq.catalog_version_id
WHERE cv.status IN ('draft', 'active')
  AND p.support_level = 'active'
  AND p.status IN ('ready', 'needs_review');

COMMENT ON VIEW v_draft_catalog_prerequisites IS
    'QA view: prerequisite/corequisite edges for active programs. '
    '617 total edges, all needs_review=FALSE (fully resolved). '
    'requires_canonical_code is NULL when requires_course_id is unresolved.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. v_draft_catalog_elective_groups
--    Pool / track / concentration rules for active programs.
--    Member courses are NOT listed here — they are the cat_program_courses rows
--    where elective_group = group_key. Join to v_draft_catalog_program_courses
--    on (program_code, elective_group = group_key) to see members.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_draft_catalog_elective_groups AS
SELECT
    eg.id,
    eg.tenant_id,
    eg.catalog_version_id,
    -- Program context
    p.program_code,
    p.degree_type,
    p.official_program_name_ar,
    p.official_program_name_en,
    p.support_level             AS program_support_level,
    p.status                    AS program_status,
    -- Group definition
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
    -- Catalog envelope
    cv.version_code,
    cv.status                   AS catalog_status
FROM cat_elective_groups eg
JOIN cat_programs p   ON p.id  = eg.program_id
JOIN catalog_versions cv ON cv.id = eg.catalog_version_id
WHERE cv.status IN ('draft', 'active')
  AND p.support_level = 'active'
  AND p.status IN ('ready', 'needs_review');

COMMENT ON VIEW v_draft_catalog_elective_groups IS
    'QA view: elective pool/track/concentration rules for active programs (18 groups). '
    'Member courses are NOT listed here — they are cat_program_courses rows where '
    'elective_group = group_key. Join on (program_code, elective_group = group_key) to see members.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. v_draft_catalog_aliases
--    Latin ↔ Arabic code alias pairs (26 unique aliases from HCI + MTT).
--    These are catalog aliases (cat_course_aliases) — a separate table from the
--    legacy course_aliases (migration 043, which maps typos/wrong-codes).
--    Both tables serve different purposes and must coexist.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_draft_catalog_aliases AS
SELECT
    a.id,
    a.tenant_id,
    a.catalog_version_id,
    a.alias_label,
    a.canonical_course_code,
    -- Resolved course identity
    c.official_course_code_raw,
    c.official_title_ar,
    c.official_title_en,
    c.source_language,
    -- Alias metadata
    a.alias_type,
    a.confidence,
    a.notes,
    -- Catalog envelope
    cv.version_code,
    cv.status                   AS catalog_status
FROM cat_course_aliases a
JOIN catalog_versions cv ON cv.id = a.catalog_version_id
LEFT JOIN cat_courses c ON c.id = a.course_id
WHERE cv.status IN ('draft', 'active');

COMMENT ON VIEW v_draft_catalog_aliases IS
    'QA view: 26 Latin↔Arabic alias pairs (HCI/MTT Arabic-coded programs). '
    'alias_type=latin_code for all current rows. '
    'DISTINCT from legacy course_aliases (migration 043) which maps typos/wrong-codes.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 8. v_draft_catalog_future_programs  (INFORMATIONAL — NOT for registration)
--    All 16 diploma programs (support_level='future'). Excluded from all
--    registration surfaces in v1. Included here for catalog completeness QA only.
-- ─────────────────────────────────────────────────────────────────────────────
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
WHERE cv.status IN ('draft', 'active')
  AND p.support_level = 'future';

COMMENT ON VIEW v_draft_catalog_future_programs IS
    'INFORMATIONAL ONLY — all 16 diploma programs (support_level=future). '
    'These programs are NOT served in registration v1 and do not appear in '
    'v_draft_catalog_programs. This view exists for catalog completeness QA only.';

COMMIT;
