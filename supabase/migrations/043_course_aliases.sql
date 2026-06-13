-- Migration 043: course_aliases — canonical alias layer for course codes
--
-- WHY: During the June 2026 exam attribution project we discovered that
-- exam files, student uploads, and historical ingestion use dozens of
-- non-canonical labels for the same SEU course:
--   ISLAM101 → ISLM101   (wrong prefix, 430+ questions affected)
--   FINAL2023 → IT351    (filename-as-code)
--   CS-001    → CS001    (dash variant)
--   MATH2022  → MATH001  (filename-as-code)
-- etc.
--
-- This table is the single source of truth for all known mappings.
-- Every attribution script, ingestion worker, and search API should
-- consult it before assigning course_code so the same mistake is
-- never repeated.
--
-- alias_type values:
--   wrong_code      — incorrect prefix/spelling used historically
--   filename_code   — filename token mistaken for course code
--   abbreviation    — shortened form (e.g. "CS" for "CS241")
--   arabic_name     — Arabic course name or common label
--   popular_name    — informal English label used by students
--   prep_label      — Foundation Year / preparatory level labels

CREATE TABLE IF NOT EXISTS course_aliases (
    id               UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id        UUID        NOT NULL
                                 DEFAULT '00000000-0000-0000-0000-000000000001',

    raw_label        TEXT        NOT NULL,
    alias_type       TEXT        NOT NULL
                                 CHECK (alias_type IN (
                                     'wrong_code','filename_code','abbreviation',
                                     'arabic_name','popular_name','prep_label'
                                 )),
    canonical_code   TEXT        NOT NULL,   -- must exist in inst_courses or be 'PREP'
    confidence       NUMERIC(4,3) NOT NULL DEFAULT 1.000,
    source           TEXT,                   -- how this mapping was discovered
    notes            TEXT,

    first_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (tenant_id, raw_label)
);

CREATE INDEX IF NOT EXISTS course_aliases_raw_label_idx
    ON course_aliases (tenant_id, raw_label);

CREATE INDEX IF NOT EXISTS course_aliases_canonical_idx
    ON course_aliases (canonical_code);

COMMENT ON TABLE course_aliases IS
    'Maps any non-canonical label (wrong code, filename token, Arabic name) '
    'to the official SEU course code. Consulted at ingestion time and query time.';

-- ── Seed: known aliases discovered during exam attribution (June 2026) ──────

INSERT INTO course_aliases
    (raw_label, alias_type, canonical_code, confidence, source, notes)
VALUES
-- ISLM vs ISLAM — systematic prefix error across 930+ questions
('ISLAM101', 'wrong_code',   'ISLM101', 0.980,
 'catalog_revert_june2026',
 'SEU prefix is ISLM not ISLAM. Confirmed by rumman_catalog_202520.json. '
 '430 questions corrected June 2026.'),
('ISLAM102', 'wrong_code',   'ISLM102', 0.980,
 'catalog_revert_june2026',
 'SEU prefix is ISLM not ISLAM. 491 questions identified June 2026.'),
('ISLAM103', 'wrong_code',   'ISLM103', 0.980,
 'catalog_revert_june2026',
 'SEU prefix is ISLM not ISLAM. 371 questions identified June 2026.'),
('ISLAM104', 'wrong_code',   'ISLM104', 0.980,
 'catalog_revert_june2026',
 'SEU prefix is ISLM not ISLAM. 216 questions corrected June 2026.'),

-- CS-001 dash variant
('CS-001',   'wrong_code',   'CS001',   0.980,
 'catalog_analysis_june2026',
 'CS001 = Introduction to AI and Computing (Foundation Year). '
 'Dash variant used in original ingestion.'),

-- Filename tokens mistaken for course codes
('FINAL2023', 'filename_code', 'IT351', 0.920,
 'gpt_catalog_inference_june2026',
 'File: Computer networks final 2023 (summer).pdf → IT351 Computer Networks. '
 '86 questions corrected June 2026.'),
('EXAM2025',  'filename_code', 'IT241', 0.950,
 'gpt_catalog_inference_june2026',
 'File: Operating Systems Midterm Exam 2025.pdf → IT241 Operating Systems. '
 '21 questions corrected June 2026.'),
('MATH2022',  'filename_code', 'MATH001', 0.950,
 'gpt_catalog_inference_june2026',
 'File: Quiz 2 math 2022.pdf → MATH001 Fundamentals of Math (Foundation Year). '
 '45 questions corrected June 2026.'),
('MID2025',   'filename_code', 'IT364', 0.950,
 'gpt_catalog_inference_june2026',
 'File: Mid2025 IT364CS364_250303_022821.pdf → IT364 IT Entrepreneurship and Innovation. '
 '44 questions corrected June 2026.'),
('QUIZ2021',  'filename_code', 'PREP',  0.950,
 'gpt_catalog_inference_june2026',
 'File: LEVEL 2 QUIZ 2021.pdf → Foundation Year / Preparatory. '
 '47 questions marked as PREP June 2026.'),
('TERM2023',  'filename_code', 'PREP',  0.900,
 'gpt_catalog_inference_june2026',
 'File: Final first term 2023-2024 → Foundation Year / Preparatory. '
 '9 questions marked as PREP June 2026.'),

-- ISLM misspellings (ISLM prefix retained but wrong number suffix)
('ISLM101',   'wrong_code',   'ISLM101', 0.980,
 'original_ingestion',
 'Correct canonical code. Alias entry for reverse-lookup completeness.'),

-- Popular / informal English names
('Computer Networks',          'popular_name', 'IT351',  0.850,
 'manual', 'IT351 = Computer Networks (IT program, level 3)'),
('Operating Systems',          'popular_name', 'IT241',  0.850,
 'manual', 'IT241 = Operating Systems (IT program, level 2)'),
('IT Entrepreneurship',        'popular_name', 'IT364',  0.850,
 'manual', 'IT364 = IT Entrepreneurship and Innovation'),
('Fundamentals of Math',       'popular_name', 'MATH001',0.850,
 'manual', 'MATH001 = Fundamentals of Math (Foundation Year)'),
('Introduction to Computing',  'popular_name', 'CS001',  0.850,
 'manual', 'CS001 = Introduction to AI and Computing (Foundation Year)')

ON CONFLICT (tenant_id, raw_label) DO UPDATE
    SET canonical_code = EXCLUDED.canonical_code,
        confidence     = EXCLUDED.confidence,
        source         = EXCLUDED.source,
        notes          = EXCLUDED.notes,
        last_seen_at   = now();
