-- Migration 045: Exam Bank Coverage & Allowlist
--
-- exam_bank_coverage — one row per course that has exam questions.
--
-- Populated (and refreshed) by scripts/build_exam_bank_allowlist.py.
-- Serves as the Mobile/API source of truth for:
--   - Which courses are ready for the Exam Bank feature
--   - Why blocked courses are hidden
--   - Coverage statistics per course
--
-- ALLOWLIST RULES:
--   is_exam_bank_ready = true  → Grade A or B, non-foundation, in catalog
--   is_exam_bank_ready = false → Grade C, D, F, or foundation pseudo-code
--   blocked_reason is NULL when is_exam_bank_ready = true

CREATE TABLE IF NOT EXISTS exam_bank_coverage (
    course_code         TEXT        PRIMARY KEY,
    tenant_id           UUID        NOT NULL
                                    DEFAULT '00000000-0000-0000-0000-000000000001',

    -- Course identity
    course_name         TEXT,
    in_catalog          BOOLEAN     NOT NULL DEFAULT false,
    is_foundation       BOOLEAN     NOT NULL DEFAULT false,

    -- Coverage statistics (refreshed by script)
    coverage_grade      TEXT        NOT NULL CHECK (coverage_grade IN ('A','B','C','D','F')),
    coverage_score      INT         NOT NULL DEFAULT 0,  -- 0–100
    question_count      INT         NOT NULL DEFAULT 0,
    midterm_count       INT         NOT NULL DEFAULT 0,
    final_count         INT         NOT NULL DEFAULT 0,
    quiz_count          INT         NOT NULL DEFAULT 0,
    other_count         INT         NOT NULL DEFAULT 0,
    year_span           INT         NOT NULL DEFAULT 0,  -- distinct year labels
    years_covered       TEXT[]      DEFAULT '{}',        -- e.g. {'2023','2024','2025'}

    -- Data richness flags
    has_faculty_bridge  BOOLEAN     NOT NULL DEFAULT false,
    has_signals         BOOLEAN     NOT NULL DEFAULT false,
    has_source_docs     BOOLEAN     NOT NULL DEFAULT false,

    -- Allowlist decision
    is_exam_bank_ready  BOOLEAN     NOT NULL DEFAULT false,
    blocked_reason      TEXT,
        -- NULL when ready.
        -- 'foundation_pseudo_code'  — CS-001, UNKNOWN, QUIZ2021, TERM2023, MATH001
        -- 'coverage_grade_c_thin'   — Grade C: <10q, single type, single year
        -- 'quiz_only_no_year'       — only quiz questions, no exam_year recorded

    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Mobile queries
CREATE INDEX IF NOT EXISTS ebc_ready_idx
    ON exam_bank_coverage (is_exam_bank_ready)
    WHERE is_exam_bank_ready = true;

CREATE INDEX IF NOT EXISTS ebc_grade_idx
    ON exam_bank_coverage (coverage_grade);

CREATE INDEX IF NOT EXISTS ebc_tenant_idx
    ON exam_bank_coverage (tenant_id, is_exam_bank_ready);

COMMENT ON TABLE exam_bank_coverage IS
    'Exam Bank coverage stats and allowlist. One row per course with exam questions. '
    'is_exam_bank_ready=true means the course passes Grade A/B threshold and is '
    'safe to show students. Refreshed by scripts/build_exam_bank_allowlist.py.';

COMMENT ON COLUMN exam_bank_coverage.blocked_reason IS
    'NULL when is_exam_bank_ready=true. Set to a short reason code when blocked: '
    'foundation_pseudo_code | coverage_grade_c_thin | quiz_only_no_year';

COMMENT ON COLUMN exam_bank_coverage.coverage_score IS
    'Numeric 0-100. Scoring: volume(40) + exam_type_diversity(25) + '
    'year_span(15) + faculty_bridge(10) + signals(5) + source_docs(5). '
    'A>=80, B>=65, C>=45, D>=25, F<25.';
