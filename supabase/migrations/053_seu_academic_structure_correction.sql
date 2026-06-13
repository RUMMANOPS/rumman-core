-- ============================================================
-- Migration: 053_seu_academic_structure_correction.sql
-- Date:      2026-06-13
--
-- Academic Structure Correction — Canon must mirror how SEU
-- sees itself, not how our internal codes happened to be named.
--
-- Source of truth for all data in this migration:
--   seu.edu.sa/ar/programs/   (bachelor + master listing)
--   ac.seu.edu.sa/ar/programs (diploma listing — separate subdomain)
--   Individual program pages fetched and verified 2026-06-13.
--
-- Changes in this migration:
--
--   A. Additive columns
--      • seu_programs_canon: credit_hours, duration_years,
--        source_url, concentration_of (self-ref FK)
--      • seu_colleges_canon: website_domain
--
--   B. Data corrections on seu_programs_canon (2 rows)
--      • MCS: degree_level 'bachelor' → 'master'
--        (website: "برنامج الماجستير في الأمن السيبراني", 36cr)
--      • MBADM: program_code → 'MDM', name corrected
--        (website: "ماجستير تسويق رقمي", code MDM)
--
--   C. Academic structure: ADMIN concentrations
--      • INSERT BSBA — parent program for the 4 ADMIN bachelor
--        concentrations (the university's actual structure)
--      • UPDATE MGT / ACC / FIN / ECOM: concentration_of = 'BSBA'
--
--   D. Applied College subdomain
--      • UPDATE APPLIED college: website_domain = 'ac.seu.edu.sa'
--
--   E. New table: seu_academic_tracks_canon
--      • Double Major and Minor tracks (ADMIN) — not degree programs,
--        but official academic paths that affect enrollment
--
--   F. 16 diploma programs under APPLIED college
--      Seeded from ac.seu.edu.sa/ar/programs (fetched 2026-06-13)
--      All URLs are numeric-ID based: /ar/programs/{id}
--
-- Safety: all changes are additive or correct wrong data.
--   No tables dropped. No columns removed.
--   The 2 data corrections (MCS, MBADM→MDM) are real errors
--   introduced in 052 from insufficient website verification.
-- ============================================================


-- ── A. Additive columns: seu_programs_canon ───────────────────

ALTER TABLE seu_programs_canon
    -- Credit hours (exact, from program pages)
    ADD COLUMN IF NOT EXISTS credit_hours      INT,
    -- Duration in years (1.0, 2.0, etc.)
    ADD COLUMN IF NOT EXISTS duration_years    NUMERIC(3,1),
    -- Provenance: URL from which this row's data was scraped/verified
    ADD COLUMN IF NOT EXISTS source_url        TEXT,
    -- For concentrations: points to the parent umbrella program
    -- NULL = standalone degree. Non-NULL = a track within another program.
    ADD COLUMN IF NOT EXISTS concentration_of  TEXT
        REFERENCES seu_programs_canon(program_code) ON DELETE SET NULL;

COMMENT ON COLUMN seu_programs_canon.credit_hours IS
    'Total credit hours required for this program. '
    'Verified from individual program pages on seu.edu.sa / ac.seu.edu.sa.';

COMMENT ON COLUMN seu_programs_canon.concentration_of IS
    'For concentration programs (MGT/ACC/FIN/ECOM): FK to the parent program code. '
    'NULL = this is a standalone degree program. '
    'Non-NULL = this program is a concentration/major within the parent program. '
    'SEU registers ADMIN bachelor as one program (BSBA) with 4 concentrations; '
    'each concentration is only 9 credit hours of differentiation out of 130 total.';


-- ── A. Additive column: seu_colleges_canon ────────────────────

ALTER TABLE seu_colleges_canon
    ADD COLUMN IF NOT EXISTS website_domain TEXT;
    -- Main colleges: seu.edu.sa (NULL = default)
    -- Applied College: ac.seu.edu.sa (separate subdomain)

COMMENT ON COLUMN seu_colleges_canon.website_domain IS
    'Domain for this college''s web presence. NULL = seu.edu.sa (default). '
    'Applied College uses ac.seu.edu.sa — a completely separate subdomain '
    'with its own program catalog, enrollment system, and URL structure. '
    'Any scraper targeting Applied College must use this domain.';


-- ── B. Data correction 1: MCS degree_level ───────────────────
-- Error in 052: MCS was seeded as 'bachelor'.
-- Website fact: "برنامج الماجستير في الأمن السيبراني"
--   36 credit hours | 4 levels | 12 courses | 2 years
--   URL: seu.edu.sa/ar/programs/master-in-cyber-s-ecurity/
-- There is NO Cybersecurity bachelor at SEU.
-- COMP bachelor programs = IT, CS, DS only.

UPDATE seu_programs_canon
SET
    degree_level = 'master',
    credit_hours = 36,
    duration_years = 2.0,
    source_url = 'https://seu.edu.sa/ar/programs/master-in-cyber-s-ecurity/',
    updated_at = now()
WHERE program_code = 'MCS';


-- ── B. Data correction 2: MBADM → MDM ────────────────────────
-- Error in 052: code was MBADM, name was "MBA - تسويق رقمي".
-- Website fact: the program is called "ماجستير تسويق رقمي"
--   Official code on the website: MDM
--   URL: seu.edu.sa/ar/programs/master-of-digital-marketing-mdm/
-- MBADM was a fabricated code — MDM is what the university uses.
-- Safe to UPDATE PK: no other tables have FK pointing to this row.

UPDATE seu_programs_canon
SET
    program_code = 'MDM',
    name_ar      = 'ماجستير تسويق رقمي',
    name_en      = 'Master of Digital Marketing',
    website_slug = '/ar/programs/master-of-digital-marketing-mdm/',
    source_url   = 'https://seu.edu.sa/ar/programs/master-of-digital-marketing-mdm/',
    updated_at   = now()
WHERE program_code = 'MBADM';


-- ── C. ADMIN concentration structure ─────────────────────────
-- The university has ONE bachelor program: "بكالوريوس العلوم في إدارة الأعمال"
-- with 4 concentrations, each adding only 9 credit hours to the 130-hour degree.
-- Our DB keeps 4 rows (MGT/ACC/FIN/ECOM) for operational reasons —
-- they map to different course code prefixes and exam groups.
-- The Canon now documents this honestly via concentration_of.

-- Insert the parent umbrella program (not enrollable on its own;
-- exists as the canonical anchor for the 4 concentrations).
INSERT INTO seu_programs_canon (
    program_code, college_internal_code, degree_level,
    name_ar, name_en,
    credit_hours, duration_years,
    source_url, is_active
) VALUES (
    'BSBA', 'ADMIN', 'bachelor',
    'بكالوريوس العلوم في إدارة الأعمال',
    'Bachelor of Science in Business Administration',
    130, 4.0,
    'https://seu.edu.sa/ar/programs/bachelor-of-science-in-business-administration-major-in-management/',
    true
)
ON CONFLICT (program_code) DO NOTHING;

-- Mark the 4 concentrations as children of BSBA
UPDATE seu_programs_canon
SET
    concentration_of = 'BSBA',
    credit_hours     = 130,
    duration_years   = 4.0,
    updated_at       = now()
WHERE program_code IN ('MGT', 'ACC', 'FIN', 'ECOM');


-- ── D. Applied College: website_domain ───────────────────────
-- Applied College runs on a completely separate subdomain.
-- Its diploma catalog, enrollment, and program pages all live on
-- ac.seu.edu.sa — invisible to scrapers targeting seu.edu.sa.

UPDATE seu_colleges_canon
SET
    website_domain    = 'ac.seu.edu.sa',
    updated_at        = now()
WHERE internal_code = 'APPLIED';


-- ── E. seu_academic_tracks_canon ─────────────────────────────
-- For academic paths that are NOT standalone degree programs:
-- Double Major, Minor, Concentration track, Elective track.
-- These affect enrollment, credit planning, and student_mastery
-- but do not appear in seu_programs_canon (they're not degrees).

CREATE TABLE IF NOT EXISTS seu_academic_tracks_canon (
    track_code              TEXT        PRIMARY KEY,
    -- e.g. BSBA-DM-MGT, BSBA-MN-FIN

    -- Which program this track belongs to
    parent_program_code     TEXT        NOT NULL
        REFERENCES seu_programs_canon(program_code) ON DELETE RESTRICT,

    college_internal_code   TEXT        NOT NULL
        REFERENCES seu_colleges_canon(internal_code) ON DELETE RESTRICT,

    track_type              TEXT        NOT NULL
        CHECK (track_type IN ('double_major', 'minor', 'concentration', 'track')),

    name_ar                 TEXT        NOT NULL,
    name_en                 TEXT        NOT NULL,

    -- Additional credit hours beyond the main program requirement
    additional_credit_hours INT,

    -- Whether this track is currently enrollable
    is_active               BOOLEAN     NOT NULL DEFAULT true,

    source_url              TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE seu_academic_tracks_canon IS
    'Academic tracks within programs: double majors, minors, concentration paths. '
    'These are NOT degree programs — students graduate with the parent program degree. '
    'A Minor student gets the same degree as a regular student, plus a transcript note. '
    'Populated from seu.edu.sa program pages. Do not confuse with seu_programs_canon rows.';

CREATE INDEX IF NOT EXISTS idx_satc_program
    ON seu_academic_tracks_canon (parent_program_code, track_type);

CREATE INDEX IF NOT EXISTS idx_satc_college
    ON seu_academic_tracks_canon (college_internal_code, track_type);


-- ── Seed: seu_academic_tracks_canon ──────────────────────────
-- Source: seu.edu.sa/ar/programs/ — Double Major and Minor sections
-- Verified 2026-06-13.

INSERT INTO seu_academic_tracks_canon
    (track_code, parent_program_code, college_internal_code, track_type, name_ar, name_en, source_url)
VALUES
    (
        'BSBA-DM-MGT', 'BSBA', 'ADMIN', 'double_major',
        'التخصص المزدوج - إدارة الأعمال',
        'Double Major in Business Administration',
        'https://seu.edu.sa/ar/programs/'
    ),
    (
        'BSBA-DM-FIN', 'BSBA', 'ADMIN', 'double_major',
        'التخصص المزدوج - المالية',
        'Double Major in Finance',
        'https://seu.edu.sa/ar/programs/'
    ),
    (
        'BSBA-MN-MGT', 'BSBA', 'ADMIN', 'minor',
        'التخصص الفرعي - إدارة الأعمال',
        'Minor in Business Administration',
        'https://seu.edu.sa/ar/programs/'
    ),
    (
        'BSBA-MN-FIN', 'BSBA', 'ADMIN', 'minor',
        'التخصص الفرعي - المالية',
        'Minor in Finance',
        'https://seu.edu.sa/ar/programs/'
    )
ON CONFLICT (track_code) DO NOTHING;


-- ── F. 16 Diploma Programs — Applied College ─────────────────
-- Source: ac.seu.edu.sa/ar/programs (fetched 2026-06-13)
-- All URLs verified: numeric-ID pattern /ar/programs/{id}
-- Duration and credit hours from program listing page.
-- No fabricated data — every field sourced from the listing.
--
-- Note: Applied College currently has no inst_college_id
-- (it was not in the original inst_colleges table).
-- These programs are fully canonical in seu_programs_canon.

INSERT INTO seu_programs_canon (
    program_code, college_internal_code, degree_level,
    name_ar, name_en,
    credit_hours, duration_years,
    source_url, is_active
) VALUES
    (
        'DIPL-OHS', 'APPLIED', 'diploma',
        'دبلوم الصحة والسلامة المهنية',
        'Occupational Health & Safety Diploma',
        30, 1.0,
        'https://ac.seu.edu.sa/ar/programs/467', true
    ),
    (
        'DIPL-INN', 'APPLIED', 'diploma',
        'دبلوم الابتكار وريادة الأعمال',
        'Innovation & Entrepreneurship Diploma',
        80, 2.0,
        'https://ac.seu.edu.sa/ar/programs/635', true
    ),
    (
        'DIPL-IS', 'APPLIED', 'diploma',
        'دبلوم أمن المعلومات',
        'Information Security Diploma',
        60, 2.0,
        'https://ac.seu.edu.sa/ar/programs/461', true
    ),
    (
        'DIPL-DMC', 'APPLIED', 'diploma',
        'دبلوم الإعلام الرقمي وصناعة المحتوى',
        'Digital Media & Content Production Diploma',
        40, 1.0,
        'https://ac.seu.edu.sa/ar/programs/641', true
    ),
    (
        'DIPL-ACCT', 'APPLIED', 'diploma',
        'دبلوم تقنيات المحاسبة الحديثة',
        'Modern Accounting Techniques Diploma',
        80, 2.0,
        'https://ac.seu.edu.sa/ar/programs/638', true
    ),
    (
        'DIPL-EXMGT', 'APPLIED', 'diploma',
        'دبلوم الإدارة التنفيذية',
        'Executive Management Diploma',
        40, 1.0,
        'https://ac.seu.edu.sa/ar/programs/634', true
    ),
    (
        'DIPL-PR', 'APPLIED', 'diploma',
        'دبلوم العلاقات العامة',
        'Public Relations Diploma',
        60, 2.0,
        'https://ac.seu.edu.sa/ar/programs/466', true
    ),
    (
        'DIPL-FM', 'APPLIED', 'diploma',
        'دبلوم الإدارة المالية',
        'Financial Management Diploma',
        30, 1.0,
        'https://ac.seu.edu.sa/ar/programs/459', true
    ),
    (
        'DIPL-GAI', 'APPLIED', 'diploma',
        'دبلوم الذكاء الاصطناعي التوليدي',
        'Generative AI Diploma',
        81, 2.0,
        'https://ac.seu.edu.sa/ar/programs/640', true
    ),
    (
        'DIPL-ENG', 'APPLIED', 'diploma',
        'دبلوم اللغة الإنجليزية',
        'English Language Diploma',
        60, 2.0,
        'https://ac.seu.edu.sa/ar/programs/633', true
    ),
    (
        'DIPL-DMKT', 'APPLIED', 'diploma',
        'دبلوم التسويق الرقمي',
        'Digital Marketing Diploma',
        30, 1.0,
        'https://ac.seu.edu.sa/ar/programs/460', true
    ),
    (
        'DIPL-HRM', 'APPLIED', 'diploma',
        'دبلوم إدارة الموارد البشرية',
        'Human Resources Management Diploma',
        80, 2.0,
        'https://ac.seu.edu.sa/ar/programs/636', true
    ),
    (
        'DIPL-OM', 'APPLIED', 'diploma',
        'دبلوم الإدارة المكتبية',
        'Office Administration Diploma',
        30, 1.0,
        'https://ac.seu.edu.sa/ar/programs/101', true
    ),
    (
        'DIPL-LSC', 'APPLIED', 'diploma',
        'دبلوم إدارة اللوجستيات وسلاسل الإمداد',
        'Logistics & Supply Chain Management Diploma',
        30, 1.0,
        'https://ac.seu.edu.sa/ar/programs/458', true
    ),
    (
        'DIPL-SS', 'APPLIED', 'diploma',
        'دبلوم الأمن والسلامة',
        'Security & Safety Diploma',
        64, 2.0,
        'https://ac.seu.edu.sa/ar/programs/106', true
    ),
    (
        'DIPL-BM', 'APPLIED', 'diploma',
        'دبلوم إدارة الأعمال',
        'Business Administration Diploma',
        30, 1.0,
        'https://ac.seu.edu.sa/ar/programs/361', true
    )
ON CONFLICT (program_code) DO NOTHING;


-- ── Verification queries (run after applying) ─────────────────
-- SELECT program_code, degree_level, name_ar FROM seu_programs_canon
--   WHERE program_code IN ('MCS','MDM','BSBA') ORDER BY program_code;
--
-- SELECT program_code, name_ar, concentration_of FROM seu_programs_canon
--   WHERE concentration_of IS NOT NULL ORDER BY program_code;
--
-- SELECT program_code, degree_level, credit_hours, duration_years
--   FROM seu_programs_canon WHERE college_internal_code = 'APPLIED'
--   ORDER BY program_code;
--
-- SELECT * FROM seu_academic_tracks_canon ORDER BY track_type, track_code;
--
-- SELECT internal_code, website_domain FROM seu_colleges_canon
--   WHERE website_domain IS NOT NULL;

-- ── END OF MIGRATION 053 ──────────────────────────────────────
