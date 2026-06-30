-- Migration 079: student_tasks — task_type expansion + section_crn + custom_task_type
--
-- WHY: The mobile Add-Task UI offers a rich academic taxonomy (واجب، كويز،
-- اختبار فصلي، اختبار نهائي، مناقشة، مشروع، محاضرة، شخصي، أخرى) but the
-- task_type CHECK from migration 059 only allowed 6 generic values. Saving any
-- academic type failed with student_tasks_task_type_check (SQLSTATE 23514).
--
-- This migration fixes the data model at the source — no notes-marker hacks,
-- no client-side type smuggling. task_type stores the real type; "other" gets a
-- dedicated custom_task_type column; section_crn links the task to a registered
-- section for future sharing.
--
-- BACKWARD COMPATIBLE: the new CHECK is a SUPERSET of the old one — every legacy
-- value (exam_prep, deadline, request, reading, assignment, personal) is still
-- accepted, so existing rows remain valid and no data is rewritten.

-- ── 1. Expand the task_type CHECK (old values kept + new academic values) ─────
ALTER TABLE student_tasks
    DROP CONSTRAINT IF EXISTS student_tasks_task_type_check;

ALTER TABLE student_tasks
    ADD CONSTRAINT student_tasks_task_type_check
    CHECK (task_type IN (
        -- New academic taxonomy (mobile UI)
        'assignment',     -- واجب
        'quiz',           -- كويز
        'midterm_exam',   -- اختبار فصلي
        'final_exam',     -- اختبار نهائي
        'discussion',     -- مناقشة
        'project',        -- مشروع
        'lecture',        -- محاضرة
        'personal',       -- شخصي
        'other',          -- أخرى (free-form type goes in custom_task_type)
        -- Legacy values (migration 059) — retained for backward compatibility
        'exam_prep',      -- استعداد لاختبار
        'deadline',       -- موعد رسمي
        'request',        -- متابعة طلب إداري
        'reading'         -- قراءة / مراجعة
    ));

-- ── 2. section_crn — link a task to a specific registered section (optional) ──
-- CRN from term_sections / student_registered_sections. Nullable; only set when
-- the student picks a course that has a CRN. Enables future task sharing with
-- section-mates. Never required.
ALTER TABLE student_tasks
    ADD COLUMN IF NOT EXISTS section_crn TEXT NULL;

-- ── 3. custom_task_type — the student's free-form type when task_type='other' ─
-- Keeps notes clean (notes = student notes only, never system metadata).
ALTER TABLE student_tasks
    ADD COLUMN IF NOT EXISTS custom_task_type TEXT NULL;

-- Partial index: fast lookup of a student's section-linked tasks (sharing).
CREATE INDEX IF NOT EXISTS idx_student_tasks_section_crn
    ON student_tasks (student_id, section_crn)
    WHERE section_crn IS NOT NULL;
