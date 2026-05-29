-- 009_curriculum_graduate_and_remapping.sql
-- Two objectives:
--   1. Add all 8 missing graduate specializations (migration 008 only seeded bachelor's).
--   2. Fix the 62 courses whose specialization_id is NULL because their code prefix was
--      not handled in the 008 seed — applies deterministic re-mapping via UPDATE.

-- ---------------------------------------------------------------------------
-- Part 1: Graduate specializations
-- ---------------------------------------------------------------------------

INSERT INTO seu_specializations
    (tenant_id, college_id, code, name_ar, name_en, total_credits, num_levels)
VALUES
-- Computing graduate programs
(
    '00000000-0000-0000-0000-000000000001'::UUID,
    (SELECT id FROM seu_colleges WHERE tenant_id='00000000-0000-0000-0000-000000000001'::UUID AND code='COMP'),
    'MCS', 'الأمن السيبراني', 'Cybersecurity', 36, 4
),
(
    '00000000-0000-0000-0000-000000000001'::UUID,
    (SELECT id FROM seu_colleges WHERE tenant_id='00000000-0000-0000-0000-000000000001'::UUID AND code='COMP'),
    'MDS', 'علوم البيانات (ماجستير)', 'Data Science (MSc)', 36, 4
),
-- Administrative & Financial Sciences graduate programs
(
    '00000000-0000-0000-0000-000000000001'::UUID,
    (SELECT id FROM seu_colleges WHERE tenant_id='00000000-0000-0000-0000-000000000001'::UUID AND code='ADMIN'),
    'MBA', 'إدارة الأعمال (ماجستير)', 'Master of Business Administration', 42, 4
),
(
    '00000000-0000-0000-0000-000000000001'::UUID,
    (SELECT id FROM seu_colleges WHERE tenant_id='00000000-0000-0000-0000-000000000001'::UUID AND code='ADMIN'),
    'MBADM', 'ماجستير إدارة الأعمال - تسويق رقمي', 'MBA — Digital Marketing', 42, 4
),
(
    '00000000-0000-0000-0000-000000000001'::UUID,
    (SELECT id FROM seu_colleges WHERE tenant_id='00000000-0000-0000-0000-000000000001'::UUID AND code='ADMIN'),
    'EMBA', 'ماجستير إدارة الأعمال التنفيذي', 'Executive MBA', 42, 4
),
-- Health Sciences graduate programs
(
    '00000000-0000-0000-0000-000000000001'::UUID,
    (SELECT id FROM seu_colleges WHERE tenant_id='00000000-0000-0000-0000-000000000001'::UUID AND code='HEALTH'),
    'MHA', 'إدارة الرعاية الصحية (ماجستير)', 'Healthcare Administration (MSc)', 36, 4
),
(
    '00000000-0000-0000-0000-000000000001'::UUID,
    (SELECT id FROM seu_colleges WHERE tenant_id='00000000-0000-0000-0000-000000000001'::UUID AND code='HEALTH'),
    'EMHQS', 'الماجستير التنفيذي لجودة الرعاية الصحية وسلامة المرضى',
             'Executive MSc in Healthcare Quality and Patient Safety', 36, 4
),
-- Theoretical Sciences graduate programs
(
    '00000000-0000-0000-0000-000000000001'::UUID,
    (SELECT id FROM seu_colleges WHERE tenant_id='00000000-0000-0000-0000-000000000001'::UUID AND code='THEO'),
    'MTT', 'تقنيات الترجمة (ماجستير)', 'Translation Technologies (MSc)', 36, 4
),
-- Restore GEN specialization (deleted during 008 correction phase)
(
    '00000000-0000-0000-0000-000000000001'::UUID,
    (SELECT id FROM seu_colleges WHERE tenant_id='00000000-0000-0000-0000-000000000001'::UUID AND code='GENERAL'),
    'GEN', 'مواد مشتركة', 'General / Common Courses', NULL, 8
)
ON CONFLICT (tenant_id, code) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Part 2: Re-map courses with NULL specialization_id
-- ---------------------------------------------------------------------------

-- ACCT* → ACC  (alternative Accounting prefix used in some documents)
UPDATE seu_courses
SET specialization_id = (
    SELECT id FROM seu_specializations
    WHERE tenant_id = '00000000-0000-0000-0000-000000000001'::UUID AND code = 'ACC'
)
WHERE tenant_id = '00000000-0000-0000-0000-000000000001'::UUID
  AND specialization_id IS NULL
  AND code ~ '^ACCT\d';

-- DS* → DS  (Data Science bachelor's courses)
UPDATE seu_courses
SET specialization_id = (
    SELECT id FROM seu_specializations
    WHERE tenant_id = '00000000-0000-0000-0000-000000000001'::UUID AND code = 'DS'
)
WHERE tenant_id = '00000000-0000-0000-0000-000000000001'::UUID
  AND specialization_id IS NULL
  AND code ~ '^DS\d';

-- ECOM* → ECOM
UPDATE seu_courses
SET specialization_id = (
    SELECT id FROM seu_specializations
    WHERE tenant_id = '00000000-0000-0000-0000-000000000001'::UUID AND code = 'ECOM'
)
WHERE tenant_id = '00000000-0000-0000-0000-000000000001'::UUID
  AND specialization_id IS NULL
  AND code ~ '^ECOM\d';

-- ECON* → MGT  (Economics courses sit inside Admin college)
UPDATE seu_courses
SET specialization_id = (
    SELECT id FROM seu_specializations
    WHERE tenant_id = '00000000-0000-0000-0000-000000000001'::UUID AND code = 'MGT'
)
WHERE tenant_id = '00000000-0000-0000-0000-000000000001'::UUID
  AND specialization_id IS NULL
  AND code ~ '^ECON\d';

-- HCI* → HCI
UPDATE seu_courses
SET specialization_id = (
    SELECT id FROM seu_specializations
    WHERE tenant_id = '00000000-0000-0000-0000-000000000001'::UUID AND code = 'HCI'
)
WHERE tenant_id = '00000000-0000-0000-0000-000000000001'::UUID
  AND specialization_id IS NULL
  AND code ~ '^HCI\d';

-- IS* → IT  (Information Systems courses — IS programme retired, closest is IT)
UPDATE seu_courses
SET specialization_id = (
    SELECT id FROM seu_specializations
    WHERE tenant_id = '00000000-0000-0000-0000-000000000001'::UUID AND code = 'IT'
)
WHERE tenant_id = '00000000-0000-0000-0000-000000000001'::UUID
  AND specialization_id IS NULL
  AND code ~ '^IS\d';

-- ISLAM* / ISLM* / ARA* / ENG* / MATH* / STAT* / SCI* / SCL* → GENERAL
UPDATE seu_courses
SET specialization_id = (
    SELECT id FROM seu_specializations
    WHERE tenant_id = '00000000-0000-0000-0000-000000000001'::UUID AND code = 'GEN'
)
WHERE tenant_id = '00000000-0000-0000-0000-000000000001'::UUID
  AND specialization_id IS NULL
  AND (
       code ~ '^ISLAM\d'
    OR code ~ '^ISLM\d'
    OR code ~ '^ARA\d'
    OR code ~ '^ENG\d'
    OR code ~ '^MATH\d'
    OR code ~ '^STAT\d'
    OR code ~ '^SCI\d'
    OR code ~ '^SCL\d'
  );

-- LOW* → LAW  (likely a transcription variant of LAW in Telegram messages)
UPDATE seu_courses
SET specialization_id = (
    SELECT id FROM seu_specializations
    WHERE tenant_id = '00000000-0000-0000-0000-000000000001'::UUID AND code = 'LAW'
)
WHERE tenant_id = '00000000-0000-0000-0000-000000000001'::UUID
  AND specialization_id IS NULL
  AND code ~ '^LOW\d';

-- PHC* / HCM* → HCI  (Health Informatics / Health Care Management variants)
UPDATE seu_courses
SET specialization_id = (
    SELECT id FROM seu_specializations
    WHERE tenant_id = '00000000-0000-0000-0000-000000000001'::UUID AND code = 'HCI'
)
WHERE tenant_id = '00000000-0000-0000-0000-000000000001'::UUID
  AND specialization_id IS NULL
  AND (code ~ '^PHC\d' OR code ~ '^HCM\d');

-- MIS* → MGT  (Management Information Systems)
UPDATE seu_courses
SET specialization_id = (
    SELECT id FROM seu_specializations
    WHERE tenant_id = '00000000-0000-0000-0000-000000000001'::UUID AND code = 'MGT'
)
WHERE tenant_id = '00000000-0000-0000-0000-000000000001'::UUID
  AND specialization_id IS NULL
  AND code ~ '^MIS\d';

-- MG* → MGT  (short variant prefix seen in document_chunks)
UPDATE seu_courses
SET specialization_id = (
    SELECT id FROM seu_specializations
    WHERE tenant_id = '00000000-0000-0000-0000-000000000001'::UUID AND code = 'MGT'
)
WHERE tenant_id = '00000000-0000-0000-0000-000000000001'::UUID
  AND specialization_id IS NULL
  AND code ~ '^MG\d';

-- ACT* → ACC  (Accounting variant prefix)
UPDATE seu_courses
SET specialization_id = (
    SELECT id FROM seu_specializations
    WHERE tenant_id = '00000000-0000-0000-0000-000000000001'::UUID AND code = 'ACC'
)
WHERE tenant_id = '00000000-0000-0000-0000-000000000001'::UUID
  AND specialization_id IS NULL
  AND code ~ '^ACT\d';
