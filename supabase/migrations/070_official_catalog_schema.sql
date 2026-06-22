-- Migration 070 (DRAFT — NOT APPLIED): Official Active Catalog schema (Hybrid design)
-- Status: DRAFT for review. Do NOT apply. No DROP, no INSERT, no candidate data, no views.
-- Scope: additive new tables only (cat_* + catalog_versions). Existing inst_*/course_*/term_*
--        tables are untouched and not dropped. Compatibility VIEWS are intentionally deferred to
--        a later migration (073_catalog_compatibility_views.sql) so schema and projection stay separate.
--
-- Design source: outputs/catalog_rebuild/ACTIVE_CATALOG_REBUILD_DESIGN.md (Hybrid, approved).
-- Supports: bachelor / master / executive_master / diploma; Arabic-coded + English-coded courses;
--           shared courses via a program×course junction; elective pools; tracks/concentrations;
--           internships/co-op; conflicting official readings; full source provenance; versioned releases.
--
-- Convention: every table carries tenant_id (FK tenants) + catalog_version_id (FK catalog_versions),
--             a metadata JSONB, and CHECK-constrained enums. canonical_course_code is NEVER null.
--
-- DESIGN DECISIONS (v2, post-review 070_SCHEMA_REVIEW.md):
--   * Single source of truth for credits = cat_program_courses.credit_hours (cat_courses has NO credits column).
--   * Elective/track/concentration membership is NOT duplicated: the member courses are the
--     cat_program_courses rows whose elective_group = cat_elective_groups.group_key (no array, no junction).
--   * College link = cat_programs.college_id (FK) only; the official college code lives in cat_colleges.college_code.
--   * RLS: DEFERRED. In v1 these cat_* tables are INTERNAL / SERVICE-ONLY (all DB access via the service-role
--     key, matching inst_*). Add RLS only if an anon/auth client ever reads cat_* directly.
--   * Enums: enforced via CHECK constraints (value sets are small & stable); not lookup tables. Allowed
--     values are documented in column comments. Revisit lookups only if values churn.
--   * Compatibility VIEWS are deferred to 073_catalog_compatibility_views.sql (kept out of this schema migration).

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1) catalog_versions — one versioned release of the official catalog
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS catalog_versions (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE
                                 DEFAULT '00000000-0000-0000-0000-000000000001',
    version_code     TEXT        NOT NULL,               -- e.g. 'official-2026-06'
    source_snapshot  TEXT,                               -- note: which candidate snapshot this came from
    status           TEXT        NOT NULL DEFAULT 'draft'
                                 CHECK (status IN ('draft','validated','active','archived')),
    notes            TEXT,
    metadata         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    validated_at     TIMESTAMPTZ,
    activated_at     TIMESTAMPTZ,
    UNIQUE (tenant_id, version_code)
);
-- At most ONE active release per tenant (atomic cutover guard).
CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_versions_one_active
    ON catalog_versions (tenant_id) WHERE status = 'active';

-- ─────────────────────────────────────────────────────────────────────────────
-- 2) cat_colleges — official colleges (incl. Applied College)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cat_colleges (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE
                                   DEFAULT '00000000-0000-0000-0000-000000000001',
    catalog_version_id UUID        NOT NULL REFERENCES catalog_versions(id) ON DELETE CASCADE,
    college_code       TEXT        NOT NULL,             -- 'COMP','ADMIN','HEALTH','THEO','APPLIED'
    official_name_ar   TEXT,                             -- VERBATIM or null
    official_name_en   TEXT,                             -- VERBATIM or null
    degree_scope       TEXT[],                           -- e.g. {bachelor,master} ; null = unspecified
    status             TEXT        NOT NULL DEFAULT 'ready'
                                   CHECK (status IN ('ready','needs_review')),
    source_url         TEXT,
    metadata           JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (catalog_version_id, college_code)
);
CREATE INDEX IF NOT EXISTS idx_cat_colleges_ver ON cat_colleges (catalog_version_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 3) cat_programs — official programs (bachelor/master/executive_master/diploma)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cat_programs (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE
                                        DEFAULT '00000000-0000-0000-0000-000000000001',
    catalog_version_id      UUID        NOT NULL REFERENCES catalog_versions(id) ON DELETE CASCADE,
    college_id              UUID        REFERENCES cat_colleges(id) ON DELETE SET NULL,
    program_code            TEXT        NOT NULL,        -- canonical, e.g. 'MDM','CS','PH'
    legacy_code             TEXT,                        -- e.g. 'MBADM' ; null if none
    degree_type             TEXT        NOT NULL
                                        CHECK (degree_type IN ('bachelor','master','executive_master','diploma','other')),
    official_program_name_ar TEXT,                       -- VERBATIM or null
    official_program_name_en TEXT,                       -- VERBATIM or null
    source_program_name_raw  TEXT,                       -- verbatim source phrasing (may differ from canonical)
    display_name_ar         TEXT,                        -- filled LATER (not now) — website display
    display_name_en         TEXT,
    total_credits_official  INT,
    total_credits_alt       INT,                         -- e.g. LAW live-page 140 (nullable)
    num_levels              INT,
    support_level           TEXT        NOT NULL DEFAULT 'active'
                                        CHECK (support_level IN ('active','future','reference')),
    status                  TEXT        NOT NULL DEFAULT 'ready'
                                        CHECK (status IN ('ready','needs_review','provisional_conflicted','reference','excluded_from_active')),
    source_document         TEXT,
    source_url              TEXT,
    source_pdf_url          TEXT,
    source_sha256           TEXT,
    source_version          TEXT,
    metadata                JSONB       NOT NULL DEFAULT '{}'::jsonb,  -- e.g. PH official_split, flags
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (catalog_version_id, program_code)
);
CREATE INDEX IF NOT EXISTS idx_cat_programs_ver        ON cat_programs (catalog_version_id);
CREATE INDEX IF NOT EXISTS idx_cat_programs_degree     ON cat_programs (catalog_version_id, degree_type);
CREATE INDEX IF NOT EXISTS idx_cat_programs_active     ON cat_programs (catalog_version_id)
    WHERE support_level = 'active' AND status NOT IN ('reference','excluded_from_active','provisional_conflicted');
CREATE INDEX IF NOT EXISTS idx_cat_programs_legacy     ON cat_programs (catalog_version_id, legacy_code);

-- ─────────────────────────────────────────────────────────────────────────────
-- 4) cat_courses — OFFICIAL COURSE IDENTITY (not tied to one program)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cat_courses (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE
                                        DEFAULT '00000000-0000-0000-0000-000000000001',
    catalog_version_id      UUID        NOT NULL REFERENCES catalog_versions(id) ON DELETE CASCADE,
    official_course_code_raw TEXT       NOT NULL,        -- verbatim, e.g. 'صحة 131' / 'ENG 201' / '100 دار'
    normalized_course_code  TEXT        NOT NULL,        -- technical normalize (spaces stripped, upper for latin)
    canonical_course_code   TEXT        NOT NULL,        -- = normalized official code; NEVER NULL, NEVER invented
    official_title_ar       TEXT,                        -- VERBATIM or null
    official_title_en       TEXT,                        -- VERBATIM or null
    source_language         TEXT        CHECK (source_language IN ('ar','en','mixed')),
    official_raw_text       TEXT,                        -- verbatim source snippet
    metadata                JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT cat_courses_canonical_not_blank CHECK (length(btrim(canonical_course_code)) > 0),
    UNIQUE (catalog_version_id, canonical_course_code)
);
CREATE INDEX IF NOT EXISTS idx_cat_courses_ver   ON cat_courses (catalog_version_id);
CREATE INDEX IF NOT EXISTS idx_cat_courses_norm  ON cat_courses (catalog_version_id, normalized_course_code);

-- ─────────────────────────────────────────────────────────────────────────────
-- 5) cat_program_courses — JUNCTION: a course's membership inside a program
--    (the model fix — same course can belong to many programs with distinct level/role)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cat_program_courses (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE
                                     DEFAULT '00000000-0000-0000-0000-000000000001',
    catalog_version_id   UUID        NOT NULL REFERENCES catalog_versions(id) ON DELETE CASCADE,
    program_id           UUID        NOT NULL REFERENCES cat_programs(id) ON DELETE CASCADE,
    course_id            UUID        NOT NULL REFERENCES cat_courses(id)  ON DELETE CASCADE,
    level                INT,
    credit_hours         INT         NOT NULL,           -- authoritative credits in THIS program context
    category             TEXT        NOT NULL
                                     CHECK (category IN (
                                        'university_requirement','college_requirement','department_requirement',
                                        'required_course','track_elective','concentration_requirement',
                                        'free_elective','elective_pool','track_pool',
                                        'internship_or_coop_requirement','health_core_unclassified','unclear_requirement')),
    category_confidence  TEXT        NOT NULL DEFAULT 'high'
                                     CHECK (category_confidence IN ('high','low')),
    is_required          BOOLEAN     NOT NULL DEFAULT TRUE,
    is_elective          BOOLEAN     NOT NULL DEFAULT FALSE,
    elective_group       TEXT,
    track                TEXT,
    choose_rule          TEXT        NOT NULL DEFAULT 'all_required'
                                     CHECK (choose_rule IN ('all_required','choose_n_of_m','choose_credits_from_pool','choose_one_track','none')),
    choose_count         INT,
    choose_credits       INT,
    requirement_status   TEXT        NOT NULL DEFAULT 'ok'
                                     CHECK (requirement_status IN ('ok','unclear')),
    needs_human_review   BOOLEAN     NOT NULL DEFAULT FALSE,
    requirement_note     TEXT,
    source_page_or_section TEXT,
    official_raw_text    TEXT        NOT NULL,            -- every membership row keeps its verbatim source
    metadata             JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (catalog_version_id, program_id, course_id)
);
CREATE INDEX IF NOT EXISTS idx_cpc_program ON cat_program_courses (catalog_version_id, program_id);
CREATE INDEX IF NOT EXISTS idx_cpc_course  ON cat_program_courses (catalog_version_id, course_id);
-- elective_group is the membership link to cat_elective_groups.group_key (no array / no junction table)
CREATE INDEX IF NOT EXISTS idx_cpc_elective_group ON cat_program_courses (catalog_version_id, program_id, elective_group)
    WHERE elective_group IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cpc_review  ON cat_program_courses (catalog_version_id)
    WHERE needs_human_review = TRUE OR requirement_status = 'unclear';

-- ─────────────────────────────────────────────────────────────────────────────
-- 6) cat_prerequisites — per-program prerequisite / corequisite edges (+ conflict capture)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cat_prerequisites (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE
                                     DEFAULT '00000000-0000-0000-0000-000000000001',
    catalog_version_id   UUID        NOT NULL REFERENCES catalog_versions(id) ON DELETE CASCADE,
    program_id           UUID        NOT NULL REFERENCES cat_programs(id) ON DELETE CASCADE,
    program_course_id    UUID        REFERENCES cat_program_courses(id) ON DELETE CASCADE, -- the dependent membership
    course_id            UUID        NOT NULL REFERENCES cat_courses(id) ON DELETE CASCADE,
    requires_course_id   UUID        REFERENCES cat_courses(id) ON DELETE SET NULL,        -- resolved target (nullable)
    requires_code_raw    TEXT        NOT NULL,            -- verbatim prereq code as printed
    relation             TEXT        NOT NULL DEFAULT 'prerequisite'
                                     CHECK (relation IN ('prerequisite','corequisite')),
    raw_text             TEXT,                            -- verbatim prerequisite phrasing
    conflict_note        TEXT,                            -- e.g. FIN: by-levels vs description disagreement
    needs_review         BOOLEAN     NOT NULL DEFAULT FALSE,
    confidence           NUMERIC(4,3) NOT NULL DEFAULT 1.000,
    metadata             JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_cat_prereq_prog   ON cat_prerequisites (catalog_version_id, program_id);
CREATE INDEX IF NOT EXISTS idx_cat_prereq_course ON cat_prerequisites (catalog_version_id, course_id);
CREATE INDEX IF NOT EXISTS idx_cat_prereq_review ON cat_prerequisites (catalog_version_id) WHERE needs_review = TRUE;

-- ─────────────────────────────────────────────────────────────────────────────
-- 7) cat_elective_groups — pools / tracks / concentrations with choose-rules
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cat_elective_groups (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE
                                     DEFAULT '00000000-0000-0000-0000-000000000001',
    catalog_version_id   UUID        NOT NULL REFERENCES catalog_versions(id) ON DELETE CASCADE,
    program_id           UUID        NOT NULL REFERENCES cat_programs(id) ON DELETE CASCADE,
    group_key            TEXT        NOT NULL,            -- stable id within program (e.g. 'finance_concentration')
    official_name_ar     TEXT,
    official_name_en     TEXT,
    track                TEXT,
    choose_rule          TEXT        NOT NULL
                                     CHECK (choose_rule IN ('choose_n_of_m','choose_credits_from_pool','choose_one_track')),
    choose_count         INT,
    choose_credits       INT,
    -- NOTE: pool/track membership is NOT stored here. The courses in a group are the
    --       cat_program_courses rows whose elective_group = this group_key (no array, no
    --       separate junction). See idx_cpc_elective_group for the lookup path.
    requirement_status   TEXT        NOT NULL DEFAULT 'ok'
                                     CHECK (requirement_status IN ('ok','unclear')),
    needs_review         BOOLEAN     NOT NULL DEFAULT FALSE,
    official_raw_text    TEXT,
    metadata             JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (catalog_version_id, program_id, group_key)
);
CREATE INDEX IF NOT EXISTS idx_cat_elec_prog ON cat_elective_groups (catalog_version_id, program_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 8) cat_course_aliases — code aliases (designed; seeded later only from verified matches)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cat_course_aliases (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE
                                     DEFAULT '00000000-0000-0000-0000-000000000001',
    catalog_version_id   UUID        NOT NULL REFERENCES catalog_versions(id) ON DELETE CASCADE,
    alias_label          TEXT        NOT NULL,            -- the raw/foreign label
    canonical_course_code TEXT       NOT NULL,            -- target canonical code in cat_courses
    course_id            UUID        REFERENCES cat_courses(id) ON DELETE CASCADE,
    alias_type           TEXT        NOT NULL
                                     CHECK (alias_type IN ('arabic_code','latin_code','003_series','banner_subject_course','wrong_code','filename_code','popular_name')),
    source               TEXT,
    confidence           NUMERIC(4,3) NOT NULL DEFAULT 1.000,
    notes                TEXT,
    metadata             JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (catalog_version_id, alias_label, canonical_course_code)
);
CREATE INDEX IF NOT EXISTS idx_cat_alias_canon ON cat_course_aliases (catalog_version_id, canonical_course_code);
CREATE INDEX IF NOT EXISTS idx_cat_alias_label ON cat_course_aliases (catalog_version_id, alias_label);

-- ─────────────────────────────────────────────────────────────────────────────
-- Comments (sensitive points)
-- ─────────────────────────────────────────────────────────────────────────────
COMMENT ON TABLE  catalog_versions IS 'Versioned releases of the official catalog. Exactly one status=active per tenant (partial unique index). Rollback = flip status; all cat_* rows are scoped by catalog_version_id.';
COMMENT ON COLUMN catalog_versions.status IS 'draft -> validated (passed QA gate) -> active (served via views) -> archived (superseded).';

COMMENT ON TABLE  cat_colleges IS 'Official colleges incl. Applied College (college_code=APPLIED). Names verbatim (ar/en), nullable.';
COMMENT ON COLUMN cat_colleges.college_code IS 'Authoritative official college code (e.g. COMP/ADMIN/HEALTH/THEO/APPLIED). cat_programs links here via college_id (FK), not a duplicated code.';

COMMENT ON TABLE  cat_programs IS 'Official programs (bachelor/master/executive_master/diploma). College link is college_id (FK) only.';
COMMENT ON COLUMN cat_programs.program_code IS 'Canonical program code (e.g. MDM, CS, PH). Legacy/source codes go to legacy_code; never overwrite official source text.';
COMMENT ON COLUMN cat_programs.legacy_code IS 'Legacy/alternate program code (e.g. MBADM -> MDM) for resolution; not the canonical identity.';
COMMENT ON COLUMN cat_programs.official_program_name_ar IS 'VERBATIM official Arabic name; never translated. official_* differs from display_* (filled later from website).';
COMMENT ON COLUMN cat_programs.official_program_name_en IS 'VERBATIM official English name; never translated.';
COMMENT ON COLUMN cat_programs.total_credits_alt IS 'Alternate published total when sources conflict (e.g. LAW live-page 140 vs handbook 128). Nullable.';
COMMENT ON COLUMN cat_programs.support_level IS 'active = served to app; future = diplomas/not in registration; reference = retained but excluded from active surfaces (e.g. LAW).';
COMMENT ON COLUMN cat_programs.status IS 'ready | needs_review (PH/FIN/MDM) | provisional_conflicted (LAW) | reference | excluded_from_active.';
COMMENT ON COLUMN cat_programs.metadata IS 'Program-level flags/JSON (e.g. PH official_split 34/33/57/9; FIN conflict flags).';

COMMENT ON TABLE  cat_courses IS 'Official COURSE IDENTITY within a catalog_version (one row per canonical course; shared courses are NOT duplicated). No credits column — credits live on cat_program_courses.';
COMMENT ON COLUMN cat_courses.canonical_course_code IS 'NEVER null, NEVER an invented/translated code. = normalized official code. Aliases live in cat_course_aliases and never overwrite this.';
COMMENT ON COLUMN cat_courses.official_course_code_raw IS 'VERBATIM source code (may be Arabic, e.g. ''صحة 131'' / ''100 دار'').';
COMMENT ON COLUMN cat_courses.official_raw_text IS 'Verbatim source snippet proving the course identity.';

COMMENT ON TABLE  cat_program_courses IS 'JUNCTION: a course''s membership inside one program. The single source of truth for credit_hours, level, role, and choose-rule. Same course can appear in many programs with different values.';
COMMENT ON COLUMN cat_program_courses.credit_hours IS 'AUTHORITATIVE credits for this course in this program context. May be 0 (non-credit internship/co-op).';
COMMENT ON COLUMN cat_program_courses.category IS 'Includes health_core_unclassified (PH: official college/dept split not published) and internship_or_coop_requirement (credit_hours may be 0).';
COMMENT ON COLUMN cat_program_courses.choose_rule IS 'all_required | choose_n_of_m | choose_credits_from_pool | choose_one_track | none. Group rule details in cat_elective_groups.';
COMMENT ON COLUMN cat_program_courses.elective_group IS 'If set, links this course to cat_elective_groups.group_key (membership lives here, not in an array).';
COMMENT ON COLUMN cat_program_courses.official_raw_text IS 'Verbatim source row proving code/title/hours/level/role for this program.';

COMMENT ON TABLE  cat_prerequisites IS 'Per-PROGRAM prerequisite/corequisite edges (rule only; per-student satisfied/unmet state belongs to student_course_progress, not here).';
COMMENT ON COLUMN cat_prerequisites.conflict_note IS 'Captures official-document disagreements (e.g. FIN by-levels vs course-description prereq). Primary edge stored; conflicting reading noted here.';
COMMENT ON COLUMN cat_prerequisites.requires_course_id IS 'Resolved target course (nullable = unresolved/external/unknown target -> set needs_review).';

COMMENT ON TABLE  cat_elective_groups IS 'Pool/track/concentration RULE holder (choose_count/choose_credits). Member courses = cat_program_courses rows with elective_group = this group_key.';

COMMENT ON TABLE  cat_course_aliases IS 'Code aliases (Arabic<->canonical, 001<->003, Banner subject_course, wrong/filename/popular). Seeded later from VERIFIED matches only; advisory, never overrides official codes.';

COMMIT;

-- ── Deferred (NOT in this migration) ────────────────────────────────────────
-- 071_official_catalog_seed.sql            — loader + v2.1 retrofit + seed (candidates → cat_*)
-- 072_prerequisites_electives_aliases.sql  — prereqs (+FIN conflicts), elective groups, verified aliases
-- 073_catalog_compatibility_views.sql      — views projecting active cat_* into legacy inst_* shape
-- NOTE: compatibility views are deliberately separated from this schema migration per the design.
