-- Migration 044: Faculty–Course bridge tables
--
-- TWO tables with distinct roles:
--
-- kg_faculty_courses  — operational layer, clean, product-ready
--   "Dr. X teaches Course Y in Term Z (N sections, M students)"
--   What we can state with confidence. Features are built on this.
--
-- kg_faculty_sections_raw — reference layer, raw CSV import
--   One row per section (CRN). No features built on this yet.
--   Retained for future: student↔section linking, signal attribution,
--   schedule analysis. Not exposed to students.
--
-- IMPORTANT: The faculty→course relationship means "teaches this course",
-- NOT "authored its exam questions". Exam questions belong to the course,
-- not to individual instructors. See course_aliases and exam_questions for
-- the course-level corpus.

-- ── Operational layer ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS kg_faculty_courses (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       UUID        NOT NULL
                                DEFAULT '00000000-0000-0000-0000-000000000001',

    faculty_id      UUID        REFERENCES kg_faculty(id) ON DELETE CASCADE,
    instructor_email TEXT       NOT NULL,   -- preserved for unmatched rows
    instructor_name  TEXT,                  -- from kg_faculty or CSV

    course_code     TEXT        NOT NULL,
    course_name     TEXT,
    term_code       TEXT        NOT NULL DEFAULT '202520',

    section_count   INT         NOT NULL DEFAULT 1,
    total_capacity  INT,
    total_enrolled  INT,

    -- delivery breakdown (aggregated from sections)
    virtual_sections  INT DEFAULT 0,
    blended_sections  INT DEFAULT 0,
    in_person_sections INT DEFAULT 0,

    -- match quality
    faculty_matched BOOLEAN     NOT NULL DEFAULT false,  -- true = joined to kg_faculty
    source          TEXT        NOT NULL DEFAULT 'rumman_sections_202520',

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (tenant_id, instructor_email, course_code, term_code)
);

CREATE INDEX IF NOT EXISTS kgfc_faculty_id_idx
    ON kg_faculty_courses (faculty_id)
    WHERE faculty_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS kgfc_course_code_idx
    ON kg_faculty_courses (course_code);

CREATE INDEX IF NOT EXISTS kgfc_term_code_idx
    ON kg_faculty_courses (term_code);

COMMENT ON TABLE kg_faculty_courses IS
    'Operational bridge: which faculty member teaches which course in which term. '
    'Derived from kg_faculty_sections_raw. One row per (instructor, course, term). '
    'faculty_id is NULL when the email could not be matched to kg_faculty.';

COMMENT ON COLUMN kg_faculty_courses.faculty_matched IS
    'true = instructor_email was found in kg_faculty.email and faculty_id is set. '
    'false = email present but not in kg_faculty yet (new hire or data gap).';

-- ── Raw reference layer ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS kg_faculty_sections_raw (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       UUID        NOT NULL
                                DEFAULT '00000000-0000-0000-0000-000000000001',

    -- CSV columns, preserved verbatim
    term_code           TEXT,
    crn                 TEXT,
    subject             TEXT,
    course_number       TEXT,
    subject_course      TEXT,
    course_name         TEXT,
    section_number      TEXT,
    credit_hours        INT,
    schedule_type       TEXT,
    delivery_mode       TEXT,
    campus              TEXT,
    instructor          TEXT,
    instructor_email    TEXT,
    capacity            INT,
    enrolled            INT,
    remaining_seats     INT,
    wait_count          INT,
    status              TEXT,
    class_schedule      TEXT,
    exam_schedule       TEXT,   -- "R 03:00-05:00 [Final Exam] | R 04:00-05:00 [Mid Exam]"

    source_file     TEXT        NOT NULL DEFAULT 'rumman_sections_202520.csv',
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (tenant_id, term_code, crn)
);

CREATE INDEX IF NOT EXISTS kgfsr_subject_course_idx
    ON kg_faculty_sections_raw (subject_course);

CREATE INDEX IF NOT EXISTS kgfsr_instructor_email_idx
    ON kg_faculty_sections_raw (instructor_email);

COMMENT ON TABLE kg_faculty_sections_raw IS
    'Raw import of rumman_sections_202520.csv. One row per section (CRN). '
    'Reference layer only — no product features built on this directly. '
    'Used for: future student↔section linking, schedule analysis, '
    'Telegram group attribution. Do not use to claim faculty exam ownership.';
