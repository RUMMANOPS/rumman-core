-- ============================================================
-- Migration: 047_student_os_foundation.sql
-- Date:      2026-06-13
-- Author:    RUMMAN Platform
--
-- Purpose:
--   Lays the data foundation for the Student OS vision —
--   the three tables that must exist from Day 1 so that
--   when worker logic is added later, historical data
--   is already accumulating.
--
-- Sections:
--   A. student_mastery        — per-topic learning state per student
--   B. student_academic_profile — inferred academic context per student
--   C. proactive_surface_queue  — what to show in the first 30 seconds
--
-- Philosophy:
--   These tables are write-once infrastructure. No worker populates
--   them yet. Their existence guarantees we collect data from the
--   moment they're created — retroactive computation from scratch
--   is impossible once 50K students have passed through.
--
-- Safety:
--   100% additive — no DROP, no DELETE, no ALTER COLUMN,
--   no TRUNCATE, no seed data.
-- ============================================================


-- ── A. student_mastery ───────────────────────────────────────
--
-- One row per (student × topic × course). Populated by
-- student_profile_worker (not yet active) reading learning_events
-- and student_interactions.
--
-- mastery_level progression:
--   unknown    — topic seen in their course but no interaction yet
--   exposed    — student queried the topic at least once
--   struggling — repeated queries + grounded=false signal
--   familiar   — successful retrievals outweigh failures
--   strong     — marked_strong or very high success_count
--
-- topic_id is a soft FK to kg_topics — nullable because student
-- queries may reference topics not yet normalized into the graph.
-- topic_name is always set; topic_id is enriched later by
-- topic_normalizer_worker.

CREATE TABLE IF NOT EXISTS student_mastery (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    user_id         UUID        NOT NULL,
    course_code     TEXT        NOT NULL,
    topic_name      TEXT        NOT NULL,
    topic_id        UUID,               -- FK to kg_topics.id — filled by normalizer
    mastery_level   TEXT        NOT NULL DEFAULT 'unknown'
                                CHECK (mastery_level IN (
                                    'unknown', 'exposed', 'struggling', 'familiar', 'strong'
                                )),
    encounter_count INT         NOT NULL DEFAULT 0,
    success_count   INT         NOT NULL DEFAULT 0,
    failure_count   INT         NOT NULL DEFAULT 0,
    last_computed   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT sm_unique_student_topic
        UNIQUE (user_id, course_code, topic_name)
);

COMMENT ON TABLE student_mastery IS
    'Per-topic mastery state per student. '
    'Natural key: (user_id, course_code, topic_name). '
    'topic_id is a denormalized soft FK filled asynchronously by topic_normalizer. '
    'Populated by student_profile_worker (gated — not yet active).';

-- Primary read: all topics for a student in a course
CREATE INDEX IF NOT EXISTS idx_sm_user_course
    ON student_mastery (user_id, course_code);

-- Filter by mastery level — power the gap detection query
CREATE INDEX IF NOT EXISTS idx_sm_user_level
    ON student_mastery (user_id, mastery_level)
    WHERE mastery_level IN ('unknown', 'exposed', 'struggling');

-- topic_id lookup once normalizer populates it
CREATE INDEX IF NOT EXISTS idx_sm_topic_id
    ON student_mastery (topic_id)
    WHERE topic_id IS NOT NULL;

-- Analytics: which topics are most students struggling with?
CREATE INDEX IF NOT EXISTS idx_sm_tenant_course_level
    ON student_mastery (tenant_id, course_code, mastery_level);


-- ── B. student_academic_profile ──────────────────────────────
--
-- One row per student. Inferred from their interaction history
-- — NOT from official enrollment data (which we don't have).
--
-- inferred_year: derived from the distribution of course levels
--   in learning_events (100-level → freshman, 400-level → senior).
--
-- academic_level: text label for inferred_year
--   freshman | sophomore | junior | senior | unknown
--
-- study_pattern: behavioral signal from learning_events timestamps
--   cramming    — activity spikes 1-3 days before known exam dates
--   regular     — spread activity throughout the semester
--   morning     — majority of queries before 12:00
--   evening     — majority of queries after 18:00
--
-- current_courses: course codes seen in learning_events in the
--   last 90 days — not official registration data.

CREATE TABLE IF NOT EXISTS student_academic_profile (
    user_id         UUID        PRIMARY KEY,
    tenant_id       UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    program         TEXT,               -- 'BSCS' | 'BSMGT' | ... (inferred from course prefixes)
    inferred_year   INT                 CHECK (inferred_year BETWEEN 1 AND 4),
    academic_level  TEXT        NOT NULL DEFAULT 'unknown'
                                CHECK (academic_level IN (
                                    'freshman', 'sophomore', 'junior', 'senior', 'unknown'
                                )),
    current_courses TEXT[]      NOT NULL DEFAULT '{}',
    study_pattern   TEXT        NOT NULL DEFAULT 'unknown'
                                CHECK (study_pattern IN (
                                    'cramming', 'regular', 'morning', 'evening', 'unknown'
                                )),
    last_computed   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE student_academic_profile IS
    'Inferred academic context for each student — derived from interaction history, '
    'NOT from official enrollment data. '
    'One row per student; upserted by student_profile_worker (gated — not yet active). '
    'program and inferred_year are nullable until sufficient history accumulates.';

-- Tenant-level analytics — how many freshmen vs seniors?
CREATE INDEX IF NOT EXISTS idx_sap_tenant_level
    ON student_academic_profile (tenant_id, academic_level);

-- program distribution per tenant
CREATE INDEX IF NOT EXISTS idx_sap_tenant_program
    ON student_academic_profile (tenant_id, program)
    WHERE program IS NOT NULL;

-- Recency: find stale profiles needing recomputation
CREATE INDEX IF NOT EXISTS idx_sap_last_computed
    ON student_academic_profile (last_computed);


-- ── C. proactive_surface_queue ────────────────────────────────
--
-- The "first 30 seconds" table. Each row is one item that should
-- be surfaced to a student when they open the app unprompted.
--
-- surface_type values:
--   exam_alert      — exam is N days away, here is the critical topic
--   gap_warning     — student has a weak topic that repeats in exams
--   cohort_signal   — N students are asking about X this week
--   professor_note  — instructor posted something in the course channel
--   milestone       — academic milestone (registration open, grade release)
--
-- priority:
--   1 = urgent     (exam in < 3 days, or critical gap detected)
--   2 = important  (exam in < 14 days, or cohort signal)
--   3 = informational (milestone, professor note)
--
-- Lifecycle:
--   created → shown (shown_at set) → dismissed (dismissed_at set)
--   Expired rows (expires_at < now()) are not surfaced regardless of shown_at.
--
-- content JSONB shape is surface_type-dependent. Examples:
--   exam_alert:   {course_code, exam_type, days_away, critical_topic, year_count}
--   gap_warning:  {course_code, topic_name, failure_count, exam_frequency}
--   cohort_signal:{course_code, topic_name, student_count, week_start}

CREATE TABLE IF NOT EXISTS proactive_surface_queue (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    user_id         UUID        NOT NULL,
    surface_type    TEXT        NOT NULL
                                CHECK (surface_type IN (
                                    'exam_alert', 'gap_warning', 'cohort_signal',
                                    'professor_note', 'milestone'
                                )),
    priority        INT         NOT NULL DEFAULT 2
                                CHECK (priority BETWEEN 1 AND 3),
    content         JSONB       NOT NULL DEFAULT '{}',
    expires_at      TIMESTAMPTZ,
    shown_at        TIMESTAMPTZ,            -- NULL = not yet surfaced
    dismissed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE proactive_surface_queue IS
    'Surface items to show a student in their first 30 seconds — before any query is typed. '
    'Populated by proactive_intelligence_worker (gated — not yet active). '
    'One row = one surface item. priority 1 = urgent, 2 = important, 3 = informational. '
    'Rows are dequeued by the bot or app layer; expired rows are silently skipped.';

-- Primary read: what should this student see right now?
CREATE INDEX IF NOT EXISTS idx_psq_user_priority
    ON proactive_surface_queue (user_id, priority, created_at DESC)
    WHERE shown_at IS NULL AND dismissed_at IS NULL;

-- Expiry cleanup: find expired unseen items
CREATE INDEX IF NOT EXISTS idx_psq_expires_at
    ON proactive_surface_queue (expires_at)
    WHERE shown_at IS NULL AND expires_at IS NOT NULL;

-- Analytics: how many items are pending per tenant?
CREATE INDEX IF NOT EXISTS idx_psq_tenant_type
    ON proactive_surface_queue (tenant_id, surface_type, created_at DESC)
    WHERE shown_at IS NULL;


-- ── END OF MIGRATION 047 ──────────────────────────────────────
