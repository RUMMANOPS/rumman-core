-- Migration 037: Faculty Registry
--
-- kg_faculty         — 718 faculty members imported from seu_academic_contacts.csv
-- kg_faculty_sections— who teaches which section in which semester
--
-- This enables:
--   - "من يدرّس IT353 هذا الفصل؟"
--   - Doctor-filtered question attribution (Pass 3 future)
--   - Student-facing professor profiles + ratings
--   - Routing logic: "تواصل مع د. الزهراني عبر Blackboard للمسائل المتعلقة بـ IT353"

CREATE TABLE IF NOT EXISTS kg_faculty (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    external_id             TEXT        UNIQUE,     -- SEU-FAC-0001 from seu_academic_contacts
    name_ar                 TEXT,
    name_en                 TEXT,
    title                   TEXT,                   -- 'أستاذ' | 'أستاذ مشارك' | 'محاضر' ...
    role_type               TEXT,
        -- 'faculty_member'|'department_head'|'vice_dean'|'dean'|'program_coordinator'
    college_code            TEXT,
    department              TEXT,
    email                   TEXT,
    campus                  TEXT,

    -- Contact routing (from SEU_ROUTING_LOGIC.csv context)
    contact_channel         TEXT,                   -- 'blackboard' | 'email' | 'office_hours'
    contact_when            TEXT,                   -- when to reach them
    problem_types_fit       TEXT[],                 -- what problems they handle
    is_escalation_contact   BOOLEAN DEFAULT false,  -- false = first contact, true = escalate

    -- Student ratings (populated from Excel + future Telegram analysis)
    clarity_score           FLOAT,                  -- 0-100
    responsiveness_score    FLOAT,
    fairness_score          FLOAT,
    exam_alignment_score    FLOAT,                  -- exam matches what was taught
    organization_score      FLOAT,
    difficulty_level        INT,                    -- 1=easy, 5=very hard
    workload_level          INT,                    -- 1=light, 5=very heavy
    student_sentiment       TEXT,                   -- 'positive'|'mixed'|'negative'
    common_notes            TEXT[],                 -- recurring student comments
    review_count            INT         NOT NULL DEFAULT 0,

    data_source             TEXT        NOT NULL DEFAULT 'import',
    imported_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS kf_email_idx ON kg_faculty (email) WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS kf_college_idx ON kg_faculty (tenant_id, college_code);
CREATE INDEX IF NOT EXISTS kf_campus_idx ON kg_faculty (campus);

-- ---------------------------------------------------------------------------
-- kg_faculty_sections — faculty ↔ term section mapping
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS kg_faculty_sections (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    faculty_id      UUID        NOT NULL REFERENCES kg_faculty(id) ON DELETE CASCADE,
    section_crn     TEXT,                   -- CRN from term_sections
    course_code     TEXT        NOT NULL,
    semester        TEXT,                   -- '202520' | '202510' | '202550'
    academic_year   TEXT,
    source          TEXT        NOT NULL DEFAULT 'official',
        -- 'official' | 'inferred_from_telegram' | 'student_report'
    confidence      FLOAT       NOT NULL DEFAULT 1.0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT kfs_confidence_range CHECK (confidence BETWEEN 0 AND 1),
    UNIQUE (faculty_id, course_code, semester)
);

CREATE INDEX IF NOT EXISTS kfs_course_semester_idx
    ON kg_faculty_sections (tenant_id, course_code, semester);

CREATE INDEX IF NOT EXISTS kfs_faculty_idx
    ON kg_faculty_sections (faculty_id);

-- ---------------------------------------------------------------------------
-- Provenance: link exam_questions to faculty (future pass)
-- Enables "سؤال دكتور الزهراني" vs "سؤال دكتورة العمري"
-- ---------------------------------------------------------------------------

ALTER TABLE exam_questions
    ADD COLUMN IF NOT EXISTS faculty_id UUID REFERENCES kg_faculty(id) ON DELETE SET NULL;
    -- NULL until faculty-section mapping enables attribution
