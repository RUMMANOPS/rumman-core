-- ============================================================
-- Migration: 052_seu_institutional_canon.sql
-- Date:      2026-06-13
--
-- SEU Institutional Canon
-- ========================
-- Single source of truth for SEU's organizational structure.
-- All scrapers, workers, and imports resolve through this Canon
-- before writing anything to the database.
--
-- Problem solved:
--   inst_colleges uses COMP/ADMIN/HEALTH/THEO (internal codes).
--   seu.edu.sa uses caic/afsc/hsc/satsc (URL codes).
--   No bridge existed — scrapers had no stable anchor.
--
-- Tables:
--   A. seu_colleges_canon   — authoritative college registry, maps
--      internal codes to website URL codes and all known aliases
--   B. seu_programs_canon   — authoritative program registry tied
--      to colleges via internal_code FK
--   C. seu_org_aliases      — free-form alias resolution table:
--      any string → canonical internal_code
--
-- Additive columns on existing tables:
--   D. official_announcements.college_canon_code FK
--   E. course_sections.college_canon_code FK
--   F. kg_faculty.college_internal_code FK + arabic_rank TEXT
--
-- Seed:
--   Populated from dry-run discovery (2026-06-13) + inst_colleges
--   data already in Supabase. College UUIDs are stable references.
--
-- Canon pipeline rule (enforced by naming convention):
--   Discover → Fetch → Normalize → Canonicalize (this table)
--   → Diff → Validate → Write only safe changes → Provenance
--
-- Safety: 100% additive. No existing columns removed.
-- ============================================================


-- ── A. seu_colleges_canon ─────────────────────────────────────
-- One row per SEU college. The bridge between all naming systems.
--
-- internal_code = the code used everywhere in this DB (COMP, ADMIN…)
-- website_code  = the slug used in seu.edu.sa URLs (caic, afsc…)
-- inst_college_id = FK to inst_colleges for backward compatibility
--
-- When a new college is created on the website, add a row here
-- BEFORE writing any data linked to that college.

CREATE TABLE IF NOT EXISTS seu_colleges_canon (
    internal_code       TEXT        PRIMARY KEY,    -- COMP, ADMIN, HEALTH, THEO, GENERAL, APPLIED
    inst_college_id     UUID        REFERENCES inst_colleges(id) ON DELETE RESTRICT,

    -- Official Arabic name (from university website / regulations)
    name_ar             TEXT        NOT NULL,
    name_en             TEXT        NOT NULL,

    -- Website URL code (null if no staff/program page on website)
    website_code        TEXT        UNIQUE,         -- caic, afsc, hsc, satsc

    -- Staff directory URL pattern (null if not published)
    staff_url_pattern   TEXT,       -- e.g. https://seu.edu.sa/caic/ar/staff/

    -- Scraper metadata
    faculty_count_last  INT,        -- last known faculty count from dry-run
    faculty_count_at    TIMESTAMPTZ,

    -- Lifecycle
    is_active           BOOLEAN     NOT NULL DEFAULT true,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE seu_colleges_canon IS
    'SEU Institutional Canon: authoritative college registry. '
    'internal_code = DB primary key; website_code = seu.edu.sa URL slug. '
    'All scrapers must resolve college identity through this table. '
    'Never write a college reference using a raw string — always FK to internal_code.';

COMMENT ON COLUMN seu_colleges_canon.website_code IS
    'URL slug used in seu.edu.sa staff/program pages. '
    'caic=Computing, afsc=Admin/Finance, hsc=Health, satsc=Sciences/Theoretical. '
    'NULL means no staff page published on the website (e.g. GENERAL, APPLIED).';

CREATE INDEX IF NOT EXISTS idx_scc_website_code
    ON seu_colleges_canon (website_code)
    WHERE website_code IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_scc_active
    ON seu_colleges_canon (is_active, internal_code);


-- ── Seed: seu_colleges_canon ──────────────────────────────────
-- Verified from: inst_colleges Supabase query (2026-06-13)
--                + dry-run scraper discovery (caic/afsc/hsc/satsc)
-- inst_college_id values from Supabase inst_colleges table.

INSERT INTO seu_colleges_canon
    (internal_code, inst_college_id, name_ar, name_en, website_code, staff_url_pattern, faculty_count_last, faculty_count_at)
VALUES
    (
        'COMP',
        'b6424e9c-5b02-42c1-a00d-3ac21ea48625',
        'كلية الحوسبة والمعلوماتية',
        'College of Computing and Informatics',
        'caic',
        'https://seu.edu.sa/caic/ar/staff/',
        168, '2026-06-13T00:00:00Z'
    ),
    (
        'ADMIN',
        'd16560f6-639d-495e-9c73-53b3f4c3596b',
        'كلية العلوم الإدارية والمالية',
        'College of Administrative and Financial Sciences',
        'afsc',
        'https://seu.edu.sa/afsc/ar/staff/',
        161, '2026-06-13T00:00:00Z'
    ),
    (
        'HEALTH',
        'd4e24c12-1ac9-4172-81d9-e7620453dbb9',
        'كلية العلوم الصحية',
        'College of Health Sciences',
        'hsc',
        'https://seu.edu.sa/hsc/ar/staff/',
        76, '2026-06-13T00:00:00Z'
    ),
    (
        'THEO',
        'c0e87324-ff48-4dbe-b73a-8c8a24f27972',
        'كلية العلوم والدراسات النظرية',
        'College of Sciences and Theoretical Studies',
        'satsc',
        'https://seu.edu.sa/satsc/ar/staff/',
        256, '2026-06-13T00:00:00Z'
    ),
    (
        'GENERAL',
        '61fb4861-502e-481f-a05c-37eca31bfdcf',
        'مواد مشتركة',
        'General / Common Courses',
        NULL, NULL, NULL, NULL
    ),
    (
        'APPLIED',
        NULL,
        'الكلية التطبيقية',
        'Applied College',
        NULL,   -- no staff page discovered in dry-run
        NULL,
        NULL, NULL
    )
ON CONFLICT (internal_code) DO NOTHING;


-- ── B. seu_programs_canon ─────────────────────────────────────
-- One row per SEU academic program. Ties inst_specializations
-- to the Canon via both internal and website representations.
--
-- program_code = inst_specializations.code (IT, CS, MGT, FIN…)
-- college_internal_code → FK to seu_colleges_canon

CREATE TABLE IF NOT EXISTS seu_programs_canon (
    program_code            TEXT        PRIMARY KEY,    -- IT, CS, MGT, FIN, ACC, etc.
    college_internal_code   TEXT        NOT NULL REFERENCES seu_colleges_canon(internal_code),

    -- Degree level
    degree_level            TEXT        NOT NULL DEFAULT 'bachelor'
                            CHECK (degree_level IN ('bachelor','master','diploma','executive')),

    -- Official names
    name_ar                 TEXT        NOT NULL,
    name_en                 TEXT        NOT NULL,

    -- Website-facing identifiers (from dry-run program pages)
    website_slug            TEXT,       -- URL slug as it appears on seu.edu.sa/programs/
    accreditation_body      TEXT,       -- e.g. NCAAA, ABET, CAHME

    -- Repository links
    study_plan_file         TEXT,       -- relative path in 1- Saudi Electronic University/1. StudyPlans/
    plo_url                 TEXT,       -- Program Learning Outcomes page URL

    -- Lifecycle
    is_active               BOOLEAN     NOT NULL DEFAULT true,
    activated_year          INT,        -- first year the program was offered
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE seu_programs_canon IS
    'SEU program registry — one row per academic program. '
    'program_code aligns with inst_specializations.code and course prefix conventions. '
    'All program_intelligence rows should FK here via specialization_code.';

CREATE INDEX IF NOT EXISTS idx_spc_college
    ON seu_programs_canon (college_internal_code, degree_level);

CREATE INDEX IF NOT EXISTS idx_spc_active
    ON seu_programs_canon (is_active, college_internal_code);


-- ── Seed: seu_programs_canon ──────────────────────────────────
-- Source: inst_specializations Supabase (2026-06-13)
-- Study plan files: repository audit (2026-06-13)

INSERT INTO seu_programs_canon
    (program_code, college_internal_code, degree_level, name_ar, name_en, study_plan_file)
VALUES
    -- COMP college — bachelor
    ('IT',    'COMP', 'bachelor', 'تقنية المعلومات',                   'Information Technology',
        '1. StudyPlans/البكالوريوس/كلية الحوسبة والمعلوماتية/قسم تقنية المعلومات/'),
    ('CS',    'COMP', 'bachelor', 'علم الحاسب',                        'Computer Science',
        '1. StudyPlans/البكالوريوس/كلية الحوسبة والمعلوماتية/قسم علوم الحاسب/'),
    ('DS',    'COMP', 'bachelor', 'علوم البيانات',                     'Data Science',
        '1. StudyPlans/البكالوريوس/كلية الحوسبة والمعلوماتية/قسم علوم البيانات/'),
    ('MCS',   'COMP', 'bachelor', 'الأمن السيبراني',                   'Cybersecurity',
        '1. StudyPlans/البكالوريوس/كلية الحوسبة والمعلوماتية/قسم الأمن السيبراني/'),
    -- COMP college — master
    ('MDS',   'COMP', 'master',   'علوم البيانات (ماجستير)',           'Data Science (MSc)',   NULL),

    -- ADMIN college — bachelor
    ('MGT',   'ADMIN', 'bachelor', 'إدارة الأعمال',                    'Business Administration',
        '1. StudyPlans/البكالوريوس/كلية العلوم الإدارية والمالية/قسم إدارة الأعمال/'),
    ('FIN',   'ADMIN', 'bachelor', 'التمويل والاستثمار',               'Finance and Investment',
        '1. StudyPlans/البكالوريوس/كلية العلوم الإدارية والمالية/قسم التمويل والاستثمار/'),
    ('ACC',   'ADMIN', 'bachelor', 'المحاسبة',                         'Accounting',
        '1. StudyPlans/البكالوريوس/كلية العلوم الإدارية والمالية/قسم المحاسبة/'),
    ('ECOM',  'ADMIN', 'bachelor', 'التجارة الإلكترونية',              'E-Commerce',
        '1. StudyPlans/البكالوريوس/كلية العلوم الإدارية والمالية/قسم التجارة الإلكترونية/'),
    -- ADMIN college — master
    ('MBA',   'ADMIN', 'master',   'إدارة الأعمال (ماجستير)',          'Master of Business Administration',          NULL),
    ('MBADM', 'ADMIN', 'master',   'ماجستير إدارة الأعمال - تسويق رقمي', 'MBA — Digital Marketing',                  NULL),
    ('EMBA',  'ADMIN', 'executive','ماجستير إدارة الأعمال التنفيذي',   'Executive MBA',                              NULL),

    -- HEALTH college — bachelor
    ('HCI',   'HEALTH', 'bachelor', 'المعلوماتية الصحية',              'Health Informatics',
        '1. StudyPlans/البكالوريوس/كلية العلوم الصحية/قسم المعلوماتية الصحية/برنامج البكالوريوس في المعلوماتية الصحية/'),
    ('PH',    'HEALTH', 'bachelor', 'الصحة العامة',                    'Public Health',
        '1. StudyPlans/البكالوريوس/كلية العلوم الصحية/قسم الصحة العامة/برنامج البكالوريوس في الصحة العامة/'),
    -- HEALTH college — master
    ('MHA',   'HEALTH', 'master',   'إدارة الرعاية الصحية (ماجستير)', 'Healthcare Administration (MSc)',
        '1. StudyPlans/الدراسات العليا/كلية العلوم الصحية/قسم الصحة العامة/برنامج الماجستير في إدارة الرعاية الصحية/'),
    ('EMHQS', 'HEALTH', 'executive','الماجستير التنفيذي لجودة الرعاية الصحية وسلامة المرضى',
        'Executive MSc in Healthcare Quality and Patient Safety',     NULL),

    -- THEO college — bachelor
    ('LAW',   'THEO', 'bachelor', 'القانون',                           'Law',
        '1. StudyPlans/البكالوريوس/كلية العلوم والدراسات النظرية/قسم القانون/'),
    ('ENGT',  'THEO', 'bachelor', 'اللغة الإنجليزية والترجمة',        'English Language and Translation',
        '1. StudyPlans/البكالوريوس/كلية العلوم والدراسات النظرية/قسم اللغة الإنجليزية والترجمة/'),
    ('DM',    'THEO', 'bachelor', 'الإعلام الإلكتروني',               'Digital Media',
        '1. StudyPlans/البكالوريوس/كلية العلوم والدراسات النظرية/قسم الإعلام الإلكتروني/'),
    -- THEO college — master
    ('MTT',   'THEO', 'master',   'تقنيات الترجمة (ماجستير)',         'Translation Technologies (MSc)',              NULL),

    -- GENERAL
    ('GEN',   'GENERAL', 'bachelor', 'مواد مشتركة',                   'General / Common Courses',                   NULL)

ON CONFLICT (program_code) DO NOTHING;


-- ── C. seu_org_aliases ────────────────────────────────────────
-- Any string that has ever been used to refer to an SEU college
-- can be normalized to an internal_code via this table.
--
-- Use cases:
--   "caic"               → COMP  (website URL code)
--   "الحوسبة"            → COMP  (short Arabic name)
--   "CAIC"               → COMP  (uppercase variant)
--   "College of Computing" → COMP (English short name)
--   "computing"          → COMP  (lowercase slug)
--
-- When a scraper or import hits an unknown college string, it must:
--   1. Lookup here → get internal_code
--   2. If not found → surface to Cockpit as "unknown alias" alert
--   3. Never write NULL college_canon_code silently

CREATE TABLE IF NOT EXISTS seu_org_aliases (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The raw string as it appears in the source (case-preserved)
    alias_text      TEXT        NOT NULL,

    -- What it resolves to
    college_internal_code   TEXT    NOT NULL REFERENCES seu_colleges_canon(internal_code),

    -- Source context for audit trail
    source          TEXT        NOT NULL DEFAULT 'manual',
                    -- 'manual' | 'website_scraper' | 'telegram_inference' | 'banner_export'
    confidence      FLOAT       NOT NULL DEFAULT 1.0 CHECK (confidence BETWEEN 0 AND 1),

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Aliases are case-insensitive per normalized lookup key
    UNIQUE (alias_text)
);

COMMENT ON TABLE seu_org_aliases IS
    'Alias resolution table: any string → seu_colleges_canon.internal_code. '
    'Before writing a college reference from any external source, look up the '
    'raw string here. If not found, surface as unknown-alias alert in Cockpit. '
    'Never silently write a NULL college_canon_code.';

CREATE INDEX IF NOT EXISTS idx_soa_alias_lower
    ON seu_org_aliases (lower(alias_text));

CREATE INDEX IF NOT EXISTS idx_soa_college
    ON seu_org_aliases (college_internal_code);


-- ── Seed: seu_org_aliases ─────────────────────────────────────
-- Website URL codes (from dry-run)
-- Arabic short names (common in Telegram references)
-- English variants (common in Banner exports, official docs)
-- Legacy inst_colleges codes (already in use throughout DB)

INSERT INTO seu_org_aliases (alias_text, college_internal_code, source) VALUES
    -- COMP — website codes
    ('caic',                    'COMP', 'website_scraper'),
    ('CAIC',                    'COMP', 'website_scraper'),
    -- COMP — Arabic variants
    ('الحوسبة',                  'COMP', 'manual'),
    ('الحوسبة والمعلوماتية',    'COMP', 'manual'),
    ('كلية الحوسبة',             'COMP', 'manual'),
    ('كلية الحوسبة والمعلوماتية', 'COMP', 'manual'),
    -- COMP — English variants
    ('computing',               'COMP', 'manual'),
    ('Computing',               'COMP', 'manual'),
    ('COMP',                    'COMP', 'manual'),

    -- ADMIN — website codes
    ('afsc',                    'ADMIN', 'website_scraper'),
    ('AFSC',                    'ADMIN', 'website_scraper'),
    -- ADMIN — Arabic variants
    ('الإدارية',                 'ADMIN', 'manual'),
    ('الإدارة',                  'ADMIN', 'manual'),
    ('الإدارية والمالية',        'ADMIN', 'manual'),
    ('كلية العلوم الإدارية',     'ADMIN', 'manual'),
    ('كلية العلوم الإدارية والمالية', 'ADMIN', 'manual'),
    -- ADMIN — English variants
    ('admin',                   'ADMIN', 'manual'),
    ('ADMIN',                   'ADMIN', 'manual'),
    ('administrative',          'ADMIN', 'manual'),

    -- HEALTH — website codes
    ('hsc',                     'HEALTH', 'website_scraper'),
    ('HSC',                     'HEALTH', 'website_scraper'),
    -- HEALTH — Arabic variants
    ('الصحية',                   'HEALTH', 'manual'),
    ('العلوم الصحية',            'HEALTH', 'manual'),
    ('كلية العلوم الصحية',       'HEALTH', 'manual'),
    -- HEALTH — English variants
    ('health',                  'HEALTH', 'manual'),
    ('HEALTH',                  'HEALTH', 'manual'),

    -- THEO — website codes
    ('satsc',                   'THEO', 'website_scraper'),
    ('SATSC',                   'THEO', 'website_scraper'),
    -- THEO — Arabic variants
    ('النظرية',                  'THEO', 'manual'),
    ('الدراسات النظرية',         'THEO', 'manual'),
    ('العلوم والدراسات النظرية', 'THEO', 'manual'),
    ('كلية العلوم والدراسات النظرية', 'THEO', 'manual'),
    -- THEO — English variants
    ('theoretical',             'THEO', 'manual'),
    ('THEO',                    'THEO', 'manual'),
    ('sciences',                'THEO', 'manual'),

    -- GENERAL
    ('general',                 'GENERAL', 'manual'),
    ('GENERAL',                 'GENERAL', 'manual'),
    ('مشتركة',                   'GENERAL', 'manual'),
    ('مواد مشتركة',              'GENERAL', 'manual'),
    ('GEN',                     'GENERAL', 'manual'),

    -- APPLIED
    ('التطبيقية',                'APPLIED', 'manual'),
    ('الكلية التطبيقية',         'APPLIED', 'manual'),
    ('applied',                 'APPLIED', 'manual'),
    ('APPLIED',                 'APPLIED', 'manual')

ON CONFLICT (alias_text) DO NOTHING;


-- ── D. Additive column: official_announcements ────────────────
-- Add college_canon_code FK alongside existing related_college TEXT.
-- related_college is NOT dropped — it remains as the raw scraped value.
-- canon_code = normalized, FK-enforced college reference.

ALTER TABLE official_announcements
    ADD COLUMN IF NOT EXISTS college_canon_code TEXT
        REFERENCES seu_colleges_canon(internal_code) ON DELETE RESTRICT;

COMMENT ON COLUMN official_announcements.college_canon_code IS
    'Canonical college reference (FK to seu_colleges_canon). '
    'Populated by the canonicalization step in the institutional pipeline. '
    'related_college retains the original raw scraped text for audit.';

CREATE INDEX IF NOT EXISTS idx_oa_college_canon
    ON official_announcements (college_canon_code)
    WHERE college_canon_code IS NOT NULL;


-- ── E. Additive column: course_sections ──────────────────────
-- course_sections has no college linkage at all.
-- Adding college_canon_code enables college-level section reports.

ALTER TABLE course_sections
    ADD COLUMN IF NOT EXISTS college_canon_code TEXT
        REFERENCES seu_colleges_canon(internal_code) ON DELETE RESTRICT;

COMMENT ON COLUMN course_sections.college_canon_code IS
    'College the section belongs to. FK to seu_colleges_canon. '
    'Populated when the section is created from Banner/scraper data. '
    'Enables: "show all sections in COMP this term" queries.';

CREATE INDEX IF NOT EXISTS idx_cs_college_canon
    ON course_sections (college_canon_code, term_code)
    WHERE college_canon_code IS NOT NULL;


-- ── F. Additive columns: kg_faculty ──────────────────────────
-- Add college_internal_code FK for canonical college linkage.
-- Add arabic_rank TEXT for the original Arabic title from SEU website
-- (academic_rank stores the normalized English enum; arabic_rank
-- stores what the website actually said — both preserved).

ALTER TABLE kg_faculty
    ADD COLUMN IF NOT EXISTS college_internal_code TEXT
        REFERENCES seu_colleges_canon(internal_code) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS arabic_rank TEXT;
        -- Raw Arabic title: "أستاذ مشارك", "أستاذ مساعد", etc.
        -- academic_rank stores the normalized enum (associate_professor, etc.)

COMMENT ON COLUMN kg_faculty.college_internal_code IS
    'FK to seu_colleges_canon. Set when faculty profile is scraped. '
    'Enables: faculty count per college, coverage analysis per college.';

COMMENT ON COLUMN kg_faculty.arabic_rank IS
    'Raw Arabic academic title from SEU website profile page. '
    'academic_rank stores the normalized English enum equivalent.';

CREATE INDEX IF NOT EXISTS idx_kgf_college
    ON kg_faculty (college_internal_code)
    WHERE college_internal_code IS NOT NULL;


-- ── G. Helper: resolve_college_alias(text) → internal_code ───
-- Used by scrapers and workers to canonicalize any college string.
-- Returns NULL if unresolvable — caller surfaces alert to Cockpit.

CREATE OR REPLACE FUNCTION resolve_college_alias(p_alias TEXT)
RETURNS TEXT
LANGUAGE SQL STABLE AS $$
    SELECT college_internal_code
    FROM seu_org_aliases
    WHERE lower(alias_text) = lower(p_alias)
    LIMIT 1
$$;

COMMENT ON FUNCTION resolve_college_alias IS
    'Resolve any college string to seu_colleges_canon.internal_code. '
    'Returns NULL if unresolvable — caller must handle and surface alert. '
    'Usage: SELECT resolve_college_alias(''caic'') → ''COMP''';


-- ── H. Verify view: canon_coverage ────────────────────────────
-- Quick diagnostic: how well is the canon populated?
-- Run: SELECT * FROM canon_coverage_check;

CREATE OR REPLACE VIEW canon_coverage_check AS
SELECT
    scc.internal_code,
    scc.name_ar,
    scc.website_code,
    scc.faculty_count_last,
    COUNT(DISTINCT sp.program_code)     AS programs_in_canon,
    COUNT(DISTINCT soa.alias_text)      AS aliases_registered,
    COUNT(DISTINCT kgf.id)              AS faculty_in_kg
FROM seu_colleges_canon scc
LEFT JOIN seu_programs_canon sp  ON sp.college_internal_code = scc.internal_code
LEFT JOIN seu_org_aliases    soa ON soa.college_internal_code = scc.internal_code
LEFT JOIN kg_faculty         kgf ON kgf.college_internal_code = scc.internal_code
GROUP BY scc.internal_code, scc.name_ar, scc.website_code, scc.faculty_count_last
ORDER BY scc.internal_code;

COMMENT ON VIEW canon_coverage_check IS
    'Diagnostic view: programs, aliases, and faculty count per canon college. '
    'faculty_in_kg=0 means no faculty have been scraped into kg_faculty for that college yet. '
    'Run: SELECT * FROM canon_coverage_check;';


-- ── END OF MIGRATION 052 ──────────────────────────────────────
