-- ============================================================
-- Migration: 054_canon_propagation_exam_docs.sql
-- Date:      2026-06-13
--
-- Canon Propagation — exam_questions + source_documents
-- =====================================================
-- Adds college_canon_code (FK to seu_colleges_canon) to the two
-- highest-value data tables, then backfills existing rows.
--
-- This makes college-level coverage gaps visible in Cockpit:
--   "THEO has 256 faculty but 17,195 exam_questions with
--    zero THEO coverage" becomes a queryable fact.
--
-- Sections:
--   A. seu_course_college_map — prefix → college lookup table.
--      Auditable and extensible: add new prefixes without migration.
--   B. resolve_course_to_college(TEXT) → TEXT — resolves any
--      course code via:
--        1. inst_courses → inst_specializations → inst_colleges FK chain
--        2. Prefix match via seu_course_college_map (longest wins)
--   C. exam_questions — ADD college_canon_code + college_canon_method
--   D. source_documents — ADD college_canon_code + college_canon_method
--   E. Backfill exam_questions (17,195 rows)
--   F. Backfill source_documents (rows with non-NULL course_code)
--   G. Indexes for Cockpit queries
--
-- Backfill coverage expectations (pre-verified from data):
--   exam_questions: ~17,100/17,195 resolvable. Unmappable =
--     junk codes: UNKNOWN, MID2023, QUIZ2021, TERM2023 (~95 rows).
--   source_documents: ~262 rows have non-NULL course_code;
--     738 stay NULL (no course_code → no college inference possible).
--
-- Prefix rules used (verified against 121 distinct codes):
--   Longer prefix always beats shorter prefix (ORDER BY prefix_length DESC).
--   This prevents IS* → COMP from overriding ISL* → GENERAL.
--
-- Safety: 100% additive. No rows deleted. No columns dropped.
--   Backfill is idempotent via WHERE college_canon_code IS NULL.
-- ============================================================


-- ── A. seu_course_college_map ─────────────────────────────────
-- Prefix-based fallback for courses not found in inst_courses.
--
-- Why needed:
--   inst_courses uses code prefixes that don't always match what
--   exam_questions and source_documents contain (e.g. ACC vs ACCT).
--   This table bridges those gaps explicitly and auditabily.
--
-- prefix_length is stored explicitly so the resolver can ORDER BY it
-- rather than recomputing LENGTH() on every lookup.

CREATE TABLE IF NOT EXISTS seu_course_college_map (
    id                      UUID    PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The prefix to match. LIKE (course_prefix || '%') is the test.
    -- e.g. 'ACCT', 'MGT', 'IT', 'IS', 'ISL', 'ISLM'
    course_prefix           TEXT    NOT NULL,

    -- Precomputed length — used for longest-prefix-wins ordering
    prefix_length           INT     NOT NULL GENERATED ALWAYS AS (length(course_prefix)) STORED,

    college_internal_code   TEXT    NOT NULL
        REFERENCES seu_colleges_canon(internal_code) ON DELETE RESTRICT,

    -- How certain is this mapping? 1.0 = authoritative, <1.0 = inferred
    confidence              FLOAT   NOT NULL DEFAULT 1.0
                            CHECK (confidence > 0 AND confidence <= 1.0),

    -- Source of this mapping entry
    source                  TEXT    NOT NULL DEFAULT 'manual'
                            CHECK (source IN ('manual','inst_courses_derived','website_verified')),

    notes                   TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (course_prefix)
);

COMMENT ON TABLE seu_course_college_map IS
    'Prefix-to-college mapping for course code resolution. '
    'Used as fallback when inst_courses does not contain the exact course code. '
    'Longest prefix wins: ISL (len 3) beats IS (len 2) for ISL101. '
    'Add new prefix rows when new course series are discovered. '
    'Do NOT hard-code prefixes in application code — use resolve_course_to_college() instead.';

CREATE INDEX IF NOT EXISTS idx_sccm_prefix
    ON seu_course_college_map (course_prefix, prefix_length DESC);

CREATE INDEX IF NOT EXISTS idx_sccm_college
    ON seu_course_college_map (college_internal_code);


-- ── Seed: seu_course_college_map ──────────────────────────────
-- Verified against all 121 distinct course codes in exam_questions
-- (17,195 rows) and source_documents prefix distribution.
-- Ordered here by college for readability; DB is unordered.
--
-- Prefix ordering (longest first within each college) ensures the
-- GENERATED ALWAYS AS column and ORDER BY prefix_length DESC work.

INSERT INTO seu_course_college_map (course_prefix, college_internal_code, confidence, source, notes)
VALUES
    -- ── COMP (كلية الحوسبة والمعلوماتية) ──────────────────────
    -- inst_courses confirmed: CS1*/CS2*/CS3*/CS4* → COMP
    -- IS1*/IS2*/IS3*/IS4*/IS5*/IS7* → COMP (Info Systems)
    -- IT* → COMP, DS* → COMP
    -- NOTE: IS prefix conflicts with ISL/ISLM/ISLAM → handled by longer entries below
    ('IS',   'COMP', 1.0, 'inst_courses_derived', 'Info Systems — longer ISL/ISLM/ISLAM entries override for Islamic courses'),
    ('IT',   'COMP', 1.0, 'inst_courses_derived', 'Information Technology'),
    ('CS',   'COMP', 1.0, 'inst_courses_derived', 'Computer Science'),
    ('DS',   'COMP', 1.0, 'inst_courses_derived', 'Data Science'),
    ('MCS',  'COMP', 1.0, 'manual',               'Cybersecurity master (MCS prefix in some older docs)'),

    -- ── ADMIN (كلية العلوم الإدارية والمالية) ─────────────────
    -- inst_courses confirmed: ACC/ACT/MGT/FIN/ECO/MIS/MG2 → ADMIN
    -- ACCT not in inst_courses (exam data uses 4-letter ACCT, DB uses ACC)
    ('ACCT', 'ADMIN', 1.0, 'manual',               'Accounting — exam data uses ACCT prefix; inst_courses uses ACC'),
    ('ECOM', 'ADMIN', 1.0, 'inst_courses_derived', 'E-Commerce'),
    ('ECON', 'ADMIN', 1.0, 'manual',               'Economics courses (undergraduate ECON prefix)'),
    ('ECN',  'ADMIN', 1.0, 'manual',               'Economics graduate series (ECN500)'),
    ('FIN',  'ADMIN', 1.0, 'inst_courses_derived', 'Finance and Investment'),
    ('MGT',  'ADMIN', 1.0, 'inst_courses_derived', 'Business Administration / Management'),
    ('MIS',  'ADMIN', 1.0, 'inst_courses_derived', 'Management Information Systems'),
    ('ACC',  'ADMIN', 1.0, 'inst_courses_derived', 'Accounting (DB-native prefix)'),
    ('ACT',  'ADMIN', 1.0, 'inst_courses_derived', 'Accounting electives'),

    -- ── HEALTH (كلية العلوم الصحية) ───────────────────────────
    -- inst_courses confirmed: HCI*/HCM*/PHC* → HEALTH
    ('HCI',  'HEALTH', 1.0, 'inst_courses_derived', 'Health Informatics'),
    ('HCM',  'HEALTH', 1.0, 'inst_courses_derived', 'Healthcare Management'),
    ('PHC',  'HEALTH', 1.0, 'inst_courses_derived', 'Public Health Community courses'),
    ('PH',   'HEALTH', 1.0, 'manual',               'Public Health (PH prefix variant)'),

    -- ── THEO (كلية العلوم والدراسات النظرية) ──────────────────
    -- inst_courses confirmed: LAW*/LOW* → THEO
    -- ENG* → THEO (ENGT program). Note: some foundation ENG courses
    -- are shared across colleges but managed by THEO.
    ('LAW',  'THEO', 1.0, 'inst_courses_derived', 'Law'),
    ('LOW',  'THEO', 1.0, 'inst_courses_derived', 'Law electives (LOW prefix in older catalog)'),
    ('ENG',  'THEO', 0.85, 'manual',              'English Language and Translation — 85% confidence, some foundation ENG may be GENERAL'),
    ('TRA',  'THEO', 1.0, 'manual',               'Translation courses'),
    ('DM',   'THEO', 1.0, 'manual',               'Digital Media (DM prefix)'),

    -- ── GENERAL (مواد مشتركة) ─────────────────────────────────
    -- inst_courses confirmed: MAT/SCI/SCL/STA/ISL → GENERAL
    -- ISLM/ISLAM — longer entries, must beat IS (COMP) mapping above
    ('ISLAM', 'GENERAL', 1.0, 'inst_courses_derived', '5-char prefix — beats IS(2) and ISL(3) for ISLAM* codes'),
    ('ISLM',  'GENERAL', 1.0, 'inst_courses_derived', '4-char prefix — beats IS(2) for ISLM* codes'),
    ('MATH',  'GENERAL', 1.0, 'manual',               '4-char — beats MAT(3) for MATH* codes'),
    ('ISL',   'GENERAL', 1.0, 'inst_courses_derived', 'Islamic Studies — beats IS(2) for ISL* codes'),
    ('MAT',   'GENERAL', 1.0, 'inst_courses_derived', 'Mathematics'),
    ('SCI',   'GENERAL', 1.0, 'inst_courses_derived', 'General Sciences'),
    ('SCL',   'GENERAL', 1.0, 'inst_courses_derived', 'Social Science / Common courses'),
    ('STA',   'GENERAL', 1.0, 'inst_courses_derived', 'Statistics (common requirement)'),
    ('STAT',  'GENERAL', 1.0, 'manual',               '4-char — beats STA(3) for STAT* codes'),
    ('RES',   'GENERAL', 0.9, 'manual',               'Research Methods graduate course — 90% confidence (common across graduate programs)')

ON CONFLICT (course_prefix) DO NOTHING;


-- ── B. resolve_course_to_college() ────────────────────────────
-- Primary resolver for any course code → college_internal_code.
--
-- Step 1: exact match through FK chain
--   inst_courses.code → inst_specializations → inst_colleges → seu_colleges_canon
-- Step 2: prefix fallback via seu_course_college_map
--   Longest matching prefix wins (ORDER BY prefix_length DESC LIMIT 1)
-- Returns NULL if unresolvable (junk codes like UNKNOWN, MID2023, etc.)
--
-- Workers and importers MUST call this before writing college references.
-- Never hard-code college inference logic in Python — use this RPC.

CREATE OR REPLACE FUNCTION resolve_course_to_college(p_course_code TEXT)
RETURNS TEXT
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_college TEXT;
BEGIN
    IF p_course_code IS NULL OR length(trim(p_course_code)) = 0 THEN
        RETURN NULL;
    END IF;

    -- Step 1: exact FK chain through institutional catalog
    SELECT scc.internal_code
    INTO v_college
    FROM inst_courses ic
    JOIN inst_specializations isp ON isp.id = ic.specialization_id
    JOIN inst_colleges icol        ON icol.id = isp.college_id
    JOIN seu_colleges_canon scc    ON scc.inst_college_id = icol.id
    WHERE ic.code = p_course_code
    LIMIT 1;

    IF v_college IS NOT NULL THEN
        RETURN v_college;
    END IF;

    -- Step 2: prefix fallback (longest prefix wins)
    SELECT college_internal_code
    INTO v_college
    FROM seu_course_college_map
    WHERE p_course_code LIKE (course_prefix || '%')
    ORDER BY prefix_length DESC
    LIMIT 1;

    RETURN v_college;
END;
$$;

COMMENT ON FUNCTION resolve_course_to_college IS
    'Resolve any course code to seu_colleges_canon.internal_code. '
    'Step 1: inst_courses → inst_specializations → inst_colleges FK chain. '
    'Step 2: seu_course_college_map prefix fallback (longest prefix wins). '
    'Returns NULL for unresolvable codes (UNKNOWN, MID2023, etc.). '
    'Usage: SELECT resolve_course_to_college(''MGT401'') → ''ADMIN''';


-- ── C. exam_questions — ADD columns ──────────────────────────

ALTER TABLE exam_questions
    ADD COLUMN IF NOT EXISTS college_canon_code   TEXT
        REFERENCES seu_colleges_canon(internal_code) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS college_canon_method TEXT
        CHECK (college_canon_method IN ('inst_courses_fk','prefix_map','manual'));

COMMENT ON COLUMN exam_questions.college_canon_code IS
    'Canonical college FK. Resolved via resolve_course_to_college(course_code). '
    'NULL = unresolvable course code (junk data like UNKNOWN, MID2023). '
    'Used by Cockpit: coverage gaps per college, panic_index per college.';

COMMENT ON COLUMN exam_questions.college_canon_method IS
    'How college_canon_code was resolved: '
    'inst_courses_fk = authoritative FK chain, '
    'prefix_map = inferred from seu_course_college_map, '
    'manual = set by human review.';


-- ── D. source_documents — ADD columns ────────────────────────

ALTER TABLE source_documents
    ADD COLUMN IF NOT EXISTS college_canon_code   TEXT
        REFERENCES seu_colleges_canon(internal_code) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS college_canon_method TEXT
        CHECK (college_canon_method IN ('inst_courses_fk','prefix_map','manual'));

COMMENT ON COLUMN source_documents.college_canon_code IS
    'Canonical college FK for this document. '
    'NULL if course_code is NULL (738 community uploads have no course tag). '
    'Used by Cockpit: document coverage per college, official doc gaps.';

COMMENT ON COLUMN source_documents.college_canon_method IS
    'Resolution method — same semantics as exam_questions.college_canon_method.';


-- ── E. Backfill: exam_questions ───────────────────────────────
-- 17,195 rows total. Two passes: FK chain first, prefix fallback second.
-- Unmappable junk codes stay NULL (UNKNOWN, MID2023, QUIZ2021, etc.).

-- Pass 1: exact inst_courses FK chain (highest confidence)
UPDATE exam_questions eq
SET
    college_canon_code   = scc.internal_code,
    college_canon_method = 'inst_courses_fk'
FROM inst_courses ic
JOIN inst_specializations isp ON isp.id = ic.specialization_id
JOIN inst_colleges icol        ON icol.id = isp.college_id
JOIN seu_colleges_canon scc    ON scc.inst_college_id = icol.id
WHERE eq.course_code         = ic.code
  AND eq.college_canon_code IS NULL;

-- Pass 2: prefix fallback for remaining rows
UPDATE exam_questions eq
SET
    college_canon_code = (
        SELECT college_internal_code
        FROM seu_course_college_map
        WHERE eq.course_code LIKE (course_prefix || '%')
        ORDER BY prefix_length DESC
        LIMIT 1
    ),
    college_canon_method = 'prefix_map'
WHERE eq.college_canon_code IS NULL
  AND eq.course_code IS NOT NULL
  AND eq.course_code NOT IN ('UNKNOWN', 'MID2023', 'QUIZ2021', 'TERM2023', 'CS-001', 'ENG-003');


-- ── F. Backfill: source_documents ─────────────────────────────
-- ~1,000 rows in current sample; 738 have NULL course_code (stay NULL).
-- Official documents (authority_tier=official) should all resolve.

-- Pass 1: exact FK chain
UPDATE source_documents sd
SET
    college_canon_code   = scc.internal_code,
    college_canon_method = 'inst_courses_fk'
FROM inst_courses ic
JOIN inst_specializations isp ON isp.id = ic.specialization_id
JOIN inst_colleges icol        ON icol.id = isp.college_id
JOIN seu_colleges_canon scc    ON scc.inst_college_id = icol.id
WHERE sd.course_code         = ic.code
  AND sd.college_canon_code IS NULL;

-- Pass 2: prefix fallback
UPDATE source_documents sd
SET
    college_canon_code = (
        SELECT college_internal_code
        FROM seu_course_college_map
        WHERE sd.course_code LIKE (course_prefix || '%')
        ORDER BY prefix_length DESC
        LIMIT 1
    ),
    college_canon_method = 'prefix_map'
WHERE sd.college_canon_code IS NULL
  AND sd.course_code IS NOT NULL;


-- ── G. Indexes ────────────────────────────────────────────────

-- exam_questions: primary Cockpit query pattern — coverage by college
CREATE INDEX IF NOT EXISTS idx_eq_college_canon
    ON exam_questions (college_canon_code, course_code)
    WHERE college_canon_code IS NOT NULL;

-- exam_questions: find unresolved rows quickly
CREATE INDEX IF NOT EXISTS idx_eq_canon_null
    ON exam_questions (course_code)
    WHERE college_canon_code IS NULL AND course_code IS NOT NULL;

-- source_documents: coverage by college + source_type
CREATE INDEX IF NOT EXISTS idx_sd_college_type
    ON source_documents (college_canon_code, source_type, authority_tier)
    WHERE college_canon_code IS NOT NULL;

-- source_documents: official docs without college (gap finder)
CREATE INDEX IF NOT EXISTS idx_sd_official_no_college
    ON source_documents (source_type, authority_tier)
    WHERE college_canon_code IS NULL AND authority_tier = 'official';


-- ── H. Cockpit gap view ───────────────────────────────────────
-- Immediate value: shows exam coverage by college in one query.
-- Shows the THEO/satsc gap (256 faculty, near-zero exam coverage).

CREATE OR REPLACE VIEW college_exam_coverage AS
SELECT
    scc.internal_code,
    scc.name_ar,
    scc.website_code,
    scc.faculty_count_last,

    -- Exam bank coverage
    COUNT(DISTINCT eq.id)                                   AS exam_questions_total,
    COUNT(DISTINCT eq.course_code)                          AS distinct_courses_covered,
    COUNT(DISTINCT eq.id) FILTER (WHERE eq.college_canon_method = 'inst_courses_fk')
                                                            AS fk_resolved_count,
    COUNT(DISTINCT eq.id) FILTER (WHERE eq.college_canon_method = 'prefix_map')
                                                            AS prefix_resolved_count,

    -- Document coverage
    COUNT(DISTINCT sd.id)                                   AS source_docs_total,
    COUNT(DISTINCT sd.id) FILTER (WHERE sd.authority_tier = 'official')
                                                            AS official_docs,
    COUNT(DISTINCT sd.id) FILTER (WHERE sd.source_type = 'exam')
                                                            AS exam_docs,
    COUNT(DISTINCT sd.id) FILTER (WHERE sd.source_type = 'study_plan')
                                                            AS study_plan_docs,

    -- Gap signal: faculty with no exam questions
    CASE
        WHEN scc.faculty_count_last > 0 AND COUNT(DISTINCT eq.id) = 0
            THEN 'ZERO_COVERAGE'
        WHEN scc.faculty_count_last > 0
             AND COUNT(DISTINCT eq.id)::FLOAT / GREATEST(1, scc.faculty_count_last) < 5
            THEN 'LOW_COVERAGE'
        ELSE 'OK'
    END AS coverage_signal

FROM seu_colleges_canon scc
LEFT JOIN exam_questions eq   ON eq.college_canon_code = scc.internal_code
LEFT JOIN source_documents sd ON sd.college_canon_code = scc.internal_code
GROUP BY scc.internal_code, scc.name_ar, scc.website_code, scc.faculty_count_last
ORDER BY exam_questions_total DESC;

COMMENT ON VIEW college_exam_coverage IS
    'Cockpit gap detector: exam questions and source docs per college. '
    'coverage_signal = ZERO_COVERAGE if a college with faculty has no exam data. '
    'Run: SELECT * FROM college_exam_coverage; '
    'The THEO row (satsc, 256 faculty) is expected to show ZERO_COVERAGE initially.';


-- ── END OF MIGRATION 054 ──────────────────────────────────────
