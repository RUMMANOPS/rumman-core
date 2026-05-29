-- 008_curriculum_foundations.sql
-- SEU curriculum reference layer: colleges → specializations → courses
-- Enables search scoping, study-plan queries, and content coverage analytics.

-- ---------------------------------------------------------------------------
-- Colleges (كليات)
-- ---------------------------------------------------------------------------
CREATE TABLE seu_colleges (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    code          TEXT        NOT NULL,   -- 'COMP', 'ADMIN', 'LAW', 'HEALTH', 'GENERAL'
    name_ar       TEXT        NOT NULL,
    name_en       TEXT        NOT NULL,
    telegram_chat_ids BIGINT[],           -- numeric IDs of this college's Telegram groups
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(tenant_id, code)
);

-- ---------------------------------------------------------------------------
-- Specializations (تخصصات) — programs within a college
-- ---------------------------------------------------------------------------
CREATE TABLE seu_specializations (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    college_id     UUID        NOT NULL REFERENCES seu_colleges(id) ON DELETE CASCADE,
    code           TEXT        NOT NULL,  -- 'IT', 'CS', 'IS', 'MGT', 'FIN', 'ACC', 'LAW' …
    name_ar        TEXT        NOT NULL,
    name_en        TEXT        NOT NULL,
    total_credits  INT,
    num_levels     INT         NOT NULL DEFAULT 8,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(tenant_id, code)
);

-- ---------------------------------------------------------------------------
-- Courses (مقررات)
-- ---------------------------------------------------------------------------
CREATE TABLE seu_courses (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    specialization_id UUID        REFERENCES seu_specializations(id),
    code              TEXT        NOT NULL,   -- 'IT362', 'MGT425', 'FIN416' …
    name_ar           TEXT,
    name_en           TEXT,
    credit_hours      INT         NOT NULL DEFAULT 3,
    level             INT,                    -- academic level 1–8
    is_required       BOOLEAN     NOT NULL DEFAULT TRUE,
    prerequisites     TEXT[],                 -- array of course codes
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(tenant_id, code)
);

-- ---------------------------------------------------------------------------
-- Index: fast lookups by course code (the primary search key)
-- ---------------------------------------------------------------------------
CREATE INDEX idx_seu_courses_code       ON seu_courses(code);
CREATE INDEX idx_seu_courses_spec       ON seu_courses(specialization_id);
CREATE INDEX idx_seu_courses_level      ON seu_courses(specialization_id, level);
CREATE INDEX idx_seu_colleges_tenant    ON seu_colleges(tenant_id);
CREATE INDEX idx_seu_specs_college      ON seu_specializations(college_id);

-- ---------------------------------------------------------------------------
-- View: course content coverage (how many embedded chunks per course)
-- ---------------------------------------------------------------------------
CREATE VIEW course_content_coverage AS
SELECT
    c.code,
    c.name_ar,
    c.name_en,
    sp.code          AS specialization,
    col.name_ar      AS college,
    c.level,
    COUNT(dc.id)     AS chunk_count,
    COUNT(DISTINCT dc.source_type) AS source_type_count
FROM seu_courses c
LEFT JOIN seu_specializations sp  ON c.specialization_id = sp.id
LEFT JOIN seu_colleges col        ON sp.college_id = col.id
LEFT JOIN document_chunks dc      ON dc.course_code = c.code
                                 AND dc.tenant_id   = c.tenant_id
GROUP BY c.id, c.code, c.name_ar, c.name_en, sp.code, col.name_ar, c.level;

-- ---------------------------------------------------------------------------
-- Seed: SEU tenant colleges
-- ---------------------------------------------------------------------------
INSERT INTO seu_colleges (tenant_id, code, name_ar, name_en, telegram_chat_ids) VALUES
(
    '00000000-0000-0000-0000-000000000001',
    'COMP',
    'كلية الحوسبة والمعلوماتية',
    'College of Computing and Informatics',
    ARRAY[2301414984, 2398326236, 3174700734, 3134929498]::BIGINT[]
),
(
    '00000000-0000-0000-0000-000000000001',
    'ADMIN',
    'كلية العلوم الإدارية والمالية',
    'College of Administrative and Financial Sciences',
    NULL
),
(
    '00000000-0000-0000-0000-000000000001',
    'LAW',
    'كلية القانون',
    'College of Law',
    ARRAY[2194613267, 2411273497, 2340662099, 2452746005, 2429610731, 2344747265, 3848064747]::BIGINT[]
),
(
    '00000000-0000-0000-0000-000000000001',
    'HEALTH',
    'كلية العلوم الصحية',
    'College of Health Sciences',
    NULL
),
(
    '00000000-0000-0000-0000-000000000001',
    'GENERAL',
    'مواد مشتركة',
    'General / Common Courses',
    NULL
);

-- ---------------------------------------------------------------------------
-- Seed: Specializations (known programs at SEU)
-- ---------------------------------------------------------------------------
INSERT INTO seu_specializations (tenant_id, college_id, code, name_ar, name_en, total_credits) VALUES
-- Computing college
('00000000-0000-0000-0000-000000000001', (SELECT id FROM seu_colleges WHERE tenant_id='00000000-0000-0000-0000-000000000001' AND code='COMP'), 'IT',  'تقنية المعلومات',            'Information Technology',            126),
('00000000-0000-0000-0000-000000000001', (SELECT id FROM seu_colleges WHERE tenant_id='00000000-0000-0000-0000-000000000001' AND code='COMP'), 'CS',  'علم الحاسب',                 'Computer Science',                  126),
('00000000-0000-0000-0000-000000000001', (SELECT id FROM seu_colleges WHERE tenant_id='00000000-0000-0000-0000-000000000001' AND code='COMP'), 'IS',  'نظم المعلومات',              'Information Systems',               126),
('00000000-0000-0000-0000-000000000001', (SELECT id FROM seu_colleges WHERE tenant_id='00000000-0000-0000-0000-000000000001' AND code='COMP'), 'CIS', 'حوسبة وعلوم المعلومات',     'Computing and Information Sciences', 126),
('00000000-0000-0000-0000-000000000001', (SELECT id FROM seu_colleges WHERE tenant_id='00000000-0000-0000-0000-000000000001' AND code='COMP'), 'SE',  'هندسة البرمجيات',           'Software Engineering',               126),
-- Administrative & Financial Sciences
('00000000-0000-0000-0000-000000000001', (SELECT id FROM seu_colleges WHERE tenant_id='00000000-0000-0000-0000-000000000001' AND code='ADMIN'), 'MGT', 'إدارة الأعمال',             'Business Administration',            126),
('00000000-0000-0000-0000-000000000001', (SELECT id FROM seu_colleges WHERE tenant_id='00000000-0000-0000-0000-000000000001' AND code='ADMIN'), 'FIN', 'التمويل والاستثمار',        'Finance and Investment',             126),
('00000000-0000-0000-0000-000000000001', (SELECT id FROM seu_colleges WHERE tenant_id='00000000-0000-0000-0000-000000000001' AND code='ADMIN'), 'ACC', 'المحاسبة',                  'Accounting',                        126),
('00000000-0000-0000-0000-000000000001', (SELECT id FROM seu_colleges WHERE tenant_id='00000000-0000-0000-0000-000000000001' AND code='ADMIN'), 'HRM', 'إدارة الموارد البشرية',     'Human Resource Management',         126),
('00000000-0000-0000-0000-000000000001', (SELECT id FROM seu_colleges WHERE tenant_id='00000000-0000-0000-0000-000000000001' AND code='ADMIN'), 'MKT', 'التسويق',                   'Marketing',                         126),
-- Law
('00000000-0000-0000-0000-000000000001', (SELECT id FROM seu_colleges WHERE tenant_id='00000000-0000-0000-0000-000000000001' AND code='LAW'),   'LAW', 'القانون',                   'Law',                               156),
-- Health Sciences
('00000000-0000-0000-0000-000000000001', (SELECT id FROM seu_colleges WHERE tenant_id='00000000-0000-0000-0000-000000000001' AND code='HEALTH'), 'PHM', 'الصيدلة',                  'Pharmacy',                          192),
('00000000-0000-0000-0000-000000000001', (SELECT id FROM seu_colleges WHERE tenant_id='00000000-0000-0000-0000-000000000001' AND code='HEALTH'), 'NUR', 'التمريض',                  'Nursing',                           136),
-- General
('00000000-0000-0000-0000-000000000001', (SELECT id FROM seu_colleges WHERE tenant_id='00000000-0000-0000-0000-000000000001' AND code='GENERAL'), 'GEN', 'مواد مشتركة',            'General / Common Courses',           NULL);

-- ---------------------------------------------------------------------------
-- Seed: Courses inferred from document_chunks content already in DB
-- (Specialization assignments are best-effort from prefix mapping)
-- ---------------------------------------------------------------------------
INSERT INTO seu_courses (tenant_id, specialization_id, code, level)
SELECT DISTINCT
    '00000000-0000-0000-0000-000000000001'::UUID,
    (
        SELECT sp.id
        FROM seu_specializations sp
        WHERE sp.tenant_id = '00000000-0000-0000-0000-000000000001'::UUID
          AND sp.code = CASE
            -- IT prefix map
            WHEN dc.course_code ~ '^IT\d'   THEN 'IT'
            WHEN dc.course_code ~ '^CS\d'   THEN 'CS'
            WHEN dc.course_code ~ '^IS\d'   THEN 'IS'
            WHEN dc.course_code ~ '^CIS\d'  THEN 'CIS'
            WHEN dc.course_code ~ '^SE\d'   THEN 'SE'
            -- Admin/Finance
            WHEN dc.course_code ~ '^MGT\d'  THEN 'MGT'
            WHEN dc.course_code ~ '^FIN\d'  THEN 'FIN'
            WHEN dc.course_code ~ '^ACC\d'  THEN 'ACC'
            WHEN dc.course_code ~ '^HRM\d'  THEN 'HRM'
            WHEN dc.course_code ~ '^MKT\d'  THEN 'MKT'
            WHEN dc.course_code ~ '^ECO\d'  THEN 'MGT'  -- economics → admin
            WHEN dc.course_code ~ '^BUS\d'  THEN 'MGT'
            -- Law
            WHEN dc.course_code ~ '^LAW\d'  THEN 'LAW'
            -- Health
            WHEN dc.course_code ~ '^PHM\d'  THEN 'PHM'
            WHEN dc.course_code ~ '^NUR\d'  THEN 'NUR'
            -- General / Islamic studies
            WHEN dc.course_code ~ '^ISLAM\d' THEN 'GEN'
            WHEN dc.course_code ~ '^ARA\d'  THEN 'GEN'
            WHEN dc.course_code ~ '^ENG\d'  THEN 'GEN'
            WHEN dc.course_code ~ '^GEN\d'  THEN 'GEN'
            ELSE NULL
          END
        LIMIT 1
    ),
    dc.course_code,
    -- Infer level from last 3 digits: 1xx=1, 2xx=2, …, 8xx=8
    CASE
        WHEN dc.course_code ~ '\d{3}$'
        THEN LEAST(8, GREATEST(1, CAST(substring(dc.course_code from '\d{3}$') AS INT) / 100))
        ELSE NULL
    END
FROM document_chunks dc
WHERE dc.course_code IS NOT NULL
  AND dc.course_code != ''
  AND dc.tenant_id = '00000000-0000-0000-0000-000000000001'::UUID
ON CONFLICT (tenant_id, code) DO NOTHING;
