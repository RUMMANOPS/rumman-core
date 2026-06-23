-- 078_official_ar_program_names.DRAFT.sql
-- ============================================================================
-- DRAFT ONLY — NOT FOR APPLY WITHOUT FOUNDER APPROVAL.
-- Purpose: backfill cat_programs.official_program_name_ar for the 11 active
--          programs where it is currently NULL.
--
-- SOURCE (single, official): الجامعة السعودية الإلكترونية — صفحة البرامج العربية الرسمية
--          https://seu.edu.sa/ar/programs/   (fetched 2026-06-24, lang=ar)
--          Names copied VERBATIM from the official page (confidence = official_verified).
--          Cross-verified by two independent fetches; the page preserved an unrelated
--          typo ("البكالوريس" for HCI) — evidence the text is reproduced verbatim,
--          not paraphrased. (HCI is NOT in this migration; it already has a name.)
--
-- GUARANTEES (per governance rules):
--   * Updates official_program_name_ar ONLY, ONLY where currently NULL (no overwrite).
--   * Does NOT add display_name_ar or short_name_ar.
--   * Does NOT touch official_program_name_en, credits, plan, support_level, status.
--   * Does NOT touch LAW or diplomas.
--   * Scoped to tenant 00000000-0000-0000-0000-000000000001.
--   * Idempotent: re-running fills only rows still NULL.
--
-- NOT verified / NOT in scope (documented elsewhere):
--   * official_title_ar for courses (100% NULL) — separate gap.
--   * Missing level 2 / first-year semantics / STEP exemption — separate gaps.
-- ============================================================================

BEGIN;

-- Optional safety: confirm we are operating on the intended tenant.
-- (Run manually before apply; left as a comment to keep the DRAFT side-effect-free.)
-- SELECT id, version_code, status FROM catalog_versions
--   WHERE tenant_id = '00000000-0000-0000-0000-000000000001' ORDER BY created_at DESC;

UPDATE cat_programs SET official_program_name_ar = 'برنامج بكالوريوس العلوم في إدارة الأعمال - تخصص إدارة'
  WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
    AND program_code = 'MGT'  AND official_program_name_ar IS NULL;

UPDATE cat_programs SET official_program_name_ar = 'برنامج بكالوريوس العلوم في إدارة الأعمال - تخصص محاسبة'
  WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
    AND program_code = 'ACC'  AND official_program_name_ar IS NULL;

UPDATE cat_programs SET official_program_name_ar = 'برنامج بكالوريوس العلوم في إدارة الأعمال - تخصص مالية'
  WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
    AND program_code = 'FIN'  AND official_program_name_ar IS NULL;

UPDATE cat_programs SET official_program_name_ar = 'برنامج بكالوريوس العلوم في إدارة الأعمال - تخصص تجارة إلكترونية'
  WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
    AND program_code = 'ECOM' AND official_program_name_ar IS NULL;

UPDATE cat_programs SET official_program_name_ar = 'برنامج البكالوريوس في تقنية المعلومات'
  WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
    AND program_code = 'IT'   AND official_program_name_ar IS NULL;

UPDATE cat_programs SET official_program_name_ar = 'برنامج البكالوريوس في علوم الحاسب الآلي'
  WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
    AND program_code = 'CS'   AND official_program_name_ar IS NULL;

UPDATE cat_programs SET official_program_name_ar = 'برنامج البكالوريوس في علوم البيانات'
  WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
    AND program_code = 'DS'   AND official_program_name_ar IS NULL;

UPDATE cat_programs SET official_program_name_ar = 'برنامج البكالوريوس في اللغة الإنجليزية والترجمة'
  WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
    AND program_code = 'ENGT' AND official_program_name_ar IS NULL;

UPDATE cat_programs SET official_program_name_ar = 'برنامج الماجستير في الأمن السيبراني'
  WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
    AND program_code = 'MCS'  AND official_program_name_ar IS NULL;

UPDATE cat_programs SET official_program_name_ar = 'برنامج الماجستير في علوم البيانات'
  WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
    AND program_code = 'MDS'  AND official_program_name_ar IS NULL;

UPDATE cat_programs SET official_program_name_ar = 'الماجستير التنفيذي لجودة الرعاية الصحية وسلامة المرضى'
  WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
    AND program_code = 'EMHQS' AND official_program_name_ar IS NULL;

-- Verification (run after apply; expect 0 rows = no active NULLs remain among these 11):
-- SELECT program_code, official_program_name_ar FROM cat_programs
--   WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
--     AND program_code IN ('MGT','ACC','FIN','ECOM','IT','CS','DS','ENGT','MCS','MDS','EMHQS')
--   ORDER BY program_code;

COMMIT;

-- ============================================================================
-- AFTER APPLY: /v1/catalog/programs returns official_name_ar automatically
-- (v_draft_catalog_programs + _program_summary() already expose the column).
-- No view change, no backend code change, no mobile change required.
-- ============================================================================
