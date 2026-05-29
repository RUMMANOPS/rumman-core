-- =============================================================================
-- 014_rename_seu_to_inst.sql
--
-- Rename seu_colleges / seu_specializations / seu_courses → inst_* so the
-- institutional schema is university-agnostic from this point forward.
-- tenant_id handles university scoping; the table names must not embed it.
--
-- Zero-downtime strategy:
--   1. Rename the real tables.
--   2. Immediately create read-only views under the old names so any still-
--      running Railway process sees no breakage during the deploy window.
--   3. After all services have restarted with the new code the views can be
--      dropped (migration 014b or manually — they are harmless if left).
-- =============================================================================

-- ── Rename tables ─────────────────────────────────────────────────────────────

ALTER TABLE seu_colleges        RENAME TO inst_colleges;
ALTER TABLE seu_specializations RENAME TO inst_specializations;
ALTER TABLE seu_courses         RENAME TO inst_courses;

-- ── Rename indexes ────────────────────────────────────────────────────────────

ALTER INDEX IF EXISTS idx_seu_courses_code    RENAME TO idx_inst_courses_code;
ALTER INDEX IF EXISTS idx_seu_courses_spec    RENAME TO idx_inst_courses_spec;
ALTER INDEX IF EXISTS idx_seu_courses_level   RENAME TO idx_inst_courses_level;
ALTER INDEX IF EXISTS idx_seu_colleges_tenant RENAME TO idx_inst_colleges_tenant;
ALTER INDEX IF EXISTS idx_seu_specs_college   RENAME TO idx_inst_specs_college;

-- ── Drop old course_intelligence view (references old names) ─────────────────
-- Recreated below with new names.

DROP VIEW IF EXISTS course_intelligence;
DROP VIEW IF EXISTS seu_course_intelligence;

CREATE OR REPLACE VIEW inst_course_intelligence AS
SELECT
    c.id,
    c.tenant_id,
    c.code,
    c.name_ar,
    c.name_en,
    c.credit_hours,
    c.level,
    c.is_required,
    c.prerequisites,
    sp.code        AS specialization_code,
    sp.name_ar     AS specialization_name_ar,
    sp.name_en     AS specialization_name_en,
    col.code       AS college_code,
    col.name_ar    AS college_name_ar,
    col.name_en    AS college_name_en
FROM inst_courses c
LEFT JOIN inst_specializations sp  ON c.specialization_id = sp.id
LEFT JOIN inst_colleges        col ON sp.college_id        = col.id;

-- ── Backward-compat views (drop once all services have redeployed) ────────────

CREATE OR REPLACE VIEW seu_colleges        AS SELECT * FROM inst_colleges;
CREATE OR REPLACE VIEW seu_specializations AS SELECT * FROM inst_specializations;
CREATE OR REPLACE VIEW seu_courses         AS SELECT * FROM inst_courses;
