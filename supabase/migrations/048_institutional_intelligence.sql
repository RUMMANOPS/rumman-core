-- ============================================================
-- Migration: 048_institutional_intelligence.sql
-- Date:      2026-06-13
-- Author:    RUMMAN Platform
--
-- Purpose:
--   Institutional Intelligence Layer — the university as a
--   structured, enrichable knowledge graph, not just a data
--   source. Five additions:
--
--   A. ALTER kg_faculty       — add profile fields missing from 037
--   B. program_intelligence   — enriched program data (objectives,
--                               career paths, admission requirements)
--   C. course_sections        — Banner section instances (CRN, seats,
--                               schedule, gender, instructor)
--   D. section_seat_snapshots — time-series for seat monitoring
--   E. official_announcements — from SEU website / @Saudi_EUni
--
-- Compounding value:
--   • kg_faculty gains a direct link to institutional profiles →
--     Faculty node in the knowledge graph becomes a first-class
--     entity with verifiable identity
--   • course_sections bridges students ↔ faculty ↔ exam_questions
--     in one queryable table — enables schedule conflict detection,
--     seat alerts, and exam-date injection into proactive_surface_queue
--   • official_announcements feeds community_qa (verified_official),
--     academic_calendar enrichment, and proactive_surface_queue urgency
--
-- Safety:
--   100% additive — no DROP, no DELETE, no ALTER COLUMN TYPE,
--   no TRUNCATE anywhere in this file.
-- ============================================================


-- ── A. ALTER kg_faculty ───────────────────────────────────────
-- Add four columns missing from migration 037.
-- email, department, campus, college_code already exist — not repeated.
-- academic_rank normalizes free-form `title` into a queryable enum.
-- cv_url + profile_url enable direct linking to the institutional source.

ALTER TABLE kg_faculty
    ADD COLUMN IF NOT EXISTS academic_rank TEXT
        CHECK (academic_rank IN (
            'professor', 'associate_professor', 'assistant_professor',
            'lecturer', 'teaching_assistant', 'demonstrator'
        )),
    ADD COLUMN IF NOT EXISTS cv_url      TEXT,          -- /umbraco/Surface/Colleges/DoctorCV?...
    ADD COLUMN IF NOT EXISTS profile_url TEXT,          -- canonical staff directory URL
    ADD COLUMN IF NOT EXISTS profile_verified          BOOLEAN     NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS last_profile_verified_at  TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_kf_academic_rank
    ON kg_faculty (academic_rank)
    WHERE academic_rank IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_kf_profile_verified
    ON kg_faculty (profile_verified, last_profile_verified_at)
    WHERE profile_verified = true;

COMMENT ON COLUMN kg_faculty.academic_rank IS
    'Normalized faculty rank. Separate from free-form title column. '
    'Populated by institutional_scraper_worker (not yet active).';

COMMENT ON COLUMN kg_faculty.profile_verified IS
    'True when this row has been confirmed against the SEU staff directory. '
    'Unverified rows from Telegram inference have profile_verified = false.';


-- ── B. program_intelligence ───────────────────────────────────
-- Enriched program data from seu.edu.sa/ar/programs/{slug}/.
-- Separate from inst_specializations — enriches without touching it.
-- Linked via specialization_code when the code is known.

CREATE TABLE IF NOT EXISTS program_intelligence (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',

    program_slug            TEXT        NOT NULL,   -- SEU URL slug, e.g. 'master-in-cyber-s-ecurity'
    specialization_code     TEXT,                   -- FK to inst_specializations (nullable)

    program_type            TEXT
                            CHECK (program_type IN (
                                'bachelor', 'master', 'diploma', 'dual', 'bridging'
                            )),
    study_years             INT,
    levels                  INT,                    -- academic levels (semesters in plan)
    credit_units            INT,

    introduction            TEXT,                   -- program overview paragraph
    objectives              TEXT[],                 -- program learning outcomes (array)
    admission_requirements  TEXT,
    career_paths            TEXT[],                 -- "مهندس أمن شبكات", "محلل أمني"...

    official_url            TEXT,                   -- seu.edu.sa/ar/programs/{slug}/about/
    page_last_updated       TIMESTAMPTZ,            -- from "آخر تحديث" field on page
    content_hash            TEXT,                   -- SHA-256 of page content for change detection
    last_verified_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (program_slug)
);

COMMENT ON TABLE program_intelligence IS
    'Enriched program descriptions from SEU website. '
    'career_paths[] powers future "ما الفرص الوظيفية لتخصصي؟" queries. '
    'Populated by institutional_scraper_worker (gated — not yet active). '
    'Links to inst_specializations via specialization_code.';

CREATE INDEX IF NOT EXISTS idx_pi_specialization_code
    ON program_intelligence (specialization_code)
    WHERE specialization_code IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_pi_tenant_type
    ON program_intelligence (tenant_id, program_type);

CREATE INDEX IF NOT EXISTS idx_pi_stale
    ON program_intelligence (last_verified_at);


-- ── C. course_sections ────────────────────────────────────────
-- One row per section per term. Populated from Banner's
-- /StudentRegistrationSsb/ssb/searchResults endpoint.
--
-- This table is the bridge between:
--   student (who wants to register) ↔ faculty (who teaches) ↔
--   course (what is taught) ↔ exam_questions (what will be asked)
--
-- campus_gender is derived from campus_code suffix:
--   01M/OM = male, 01F/OF = female, ON = online
-- instructor_email matches kg_faculty.email for JOIN.
-- exam_date from Banner meetingType=MEXM enriches academic_calendar.

CREATE TABLE IF NOT EXISTS course_sections (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',

    term_code           TEXT        NOT NULL,   -- '202510' | '202520' | '202550'
    crn                 TEXT        NOT NULL,   -- Course Reference Number (unique per term)
    course_code         TEXT        NOT NULL,   -- 'MGT401'
    section_number      TEXT,                   -- '01', '05', 'B01'

    campus_code         TEXT,                   -- raw Banner code, e.g. '01M'
    campus_gender       TEXT
                        CHECK (campus_gender IN ('male', 'female', 'online', 'mixed')),
    delivery_mode       TEXT,                   -- 'In Person', 'Online', 'Hybrid'

    instructor_email    TEXT,                   -- matches kg_faculty.email
    instructor_name     TEXT,                   -- display name from Banner

    seats_capacity      INT,
    seats_enrolled      INT,
    seats_available     INT,
    waitlist_capacity   INT,
    waitlist_count      INT,
    credit_hours        NUMERIC(4, 2),
    part_of_term        TEXT,

    schedule_days       TEXT[],                 -- ['sunday', 'tuesday']
    start_time          TEXT,                   -- '0800' (HHMM format from Banner)
    end_time            TEXT,                   -- '0950'

    exam_date           DATE,                   -- from Banner meetingType=MEXM
    exam_start_time     TEXT,
    exam_end_time       TEXT,

    content_hash        TEXT,                   -- for change detection (seats, instructor, times)
    snapshotted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    active              BOOLEAN     NOT NULL DEFAULT true,

    UNIQUE (term_code, crn)
);

COMMENT ON TABLE course_sections IS
    'Banner section instances — one row per CRN per term. '
    'instructor_email → kg_faculty.email enables JOIN to exam patterns. '
    'exam_date from MEXM meeting type enriches academic_calendar awareness. '
    'Populated by banner_sections_worker (gated — requires auth decision).';

CREATE INDEX IF NOT EXISTS idx_cs_course_term
    ON course_sections (course_code, term_code)
    WHERE active = true;

CREATE INDEX IF NOT EXISTS idx_cs_instructor
    ON course_sections (instructor_email, term_code)
    WHERE instructor_email IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_cs_seats_available
    ON course_sections (term_code, seats_available)
    WHERE active = true AND seats_available > 0;

CREATE INDEX IF NOT EXISTS idx_cs_exam_date
    ON course_sections (exam_date, course_code)
    WHERE exam_date IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_cs_gender_term
    ON course_sections (tenant_id, term_code, campus_gender)
    WHERE active = true;


-- ── D. section_seat_snapshots ─────────────────────────────────
-- Time-series of seat availability. Written ONLY when seats change
-- significantly (not every poll). Enables:
--   • "Section 03 just opened 5 seats" alert
--   • Historical demand analysis per section
--   • Waitlist pressure signals during registration

CREATE TABLE IF NOT EXISTS section_seat_snapshots (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    term_code       TEXT        NOT NULL,
    crn             TEXT        NOT NULL,
    seats_available INT,
    waitlist_count  INT,
    snapshotted_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE section_seat_snapshots IS
    'Append-only seat availability history per section. '
    'Written by banner_sections_worker only when content_hash changes. '
    'Enables "alert when seat opens" and registration demand analysis.';

CREATE INDEX IF NOT EXISTS idx_sss_crn_time
    ON section_seat_snapshots (crn, term_code, snapshotted_at DESC);

CREATE INDEX IF NOT EXISTS idx_sss_recent
    ON section_seat_snapshots (snapshotted_at DESC);


-- ── E. official_announcements ─────────────────────────────────
-- Official university announcements from:
--   • seu.edu.sa/ar/news/   (primary source of truth)
--   • @Saudi_EUni on X      (secondary — speed detection only)
--   • manual               (ops team entry)
--
-- urgency_score 0-100: drives proactive_surface_queue injection.
-- verified_official = true only for seu.edu.sa or @Saudi_EUni (verified badge).
-- sources JSONB: merges duplicate news from multiple platforms into one row.
-- category links to community_qa for answer promotion.

CREATE TABLE IF NOT EXISTS official_announcements (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',

    source_platform     TEXT        NOT NULL
                        CHECK (source_platform IN ('seu_website', 'twitter_x', 'manual')),
    post_id             TEXT        NOT NULL,    -- unique per platform ('070626', tweet id)
    source_url          TEXT,

    title               TEXT        NOT NULL,
    body                TEXT,
    published_at        TIMESTAMPTZ,
    hijri_date_text     TEXT,                    -- original Hijri date from source

    category            TEXT        NOT NULL DEFAULT 'general'
                        CHECK (category IN (
                            'registration', 'exams', 'fees', 'excuse',
                            'calendar', 'system_outage', 'college_announcement', 'general'
                        )),
    urgency_score       INT         NOT NULL DEFAULT 0
                        CHECK (urgency_score BETWEEN 0 AND 100),
    related_college     TEXT,                    -- college_code if announcement is college-specific
    related_course      TEXT,                    -- course_code if course-specific

    verified_official   BOOLEAN     NOT NULL DEFAULT false,

    -- Dedup / merge
    content_hash        TEXT,                    -- SHA-256 of title+body for change detection
    sources             JSONB       NOT NULL DEFAULT '[]',
        -- [{platform, post_id, url, first_seen}] — all platforms mentioning same news

    status              TEXT        NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'expired', 'superseded')),

    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (source_platform, post_id)
);

COMMENT ON TABLE official_announcements IS
    'Official university announcements scraped from seu.edu.sa and @Saudi_EUni. '
    'urgency_score >= 75 → proactive_surface_queue injection. '
    'verified_official = true → elevates matching community_qa to official answer. '
    'sources JSONB deduplicates same news across platforms. '
    'Populated by official_news_worker (gated — not yet active).';

CREATE INDEX IF NOT EXISTS idx_oa_active_urgency
    ON official_announcements (urgency_score DESC, published_at DESC)
    WHERE status = 'active' AND verified_official = true;

CREATE INDEX IF NOT EXISTS idx_oa_category_active
    ON official_announcements (category, published_at DESC)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_oa_college
    ON official_announcements (related_college, published_at DESC)
    WHERE related_college IS NOT NULL AND status = 'active';

CREATE INDEX IF NOT EXISTS idx_oa_tenant_recent
    ON official_announcements (tenant_id, first_seen_at DESC);


-- ── END OF MIGRATION 048 ──────────────────────────────────────
