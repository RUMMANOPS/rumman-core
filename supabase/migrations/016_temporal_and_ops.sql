-- =============================================================================
-- 016_temporal_and_ops.sql
--
-- Two operational foundations:
--
-- A. ACADEMIC CALENDAR — structured event table keyed by tenant, year,
--    semester, and event_type. Temporal queries (exam dates, deadlines,
--    registration windows) hit this table directly — NOT vector search.
--    Content in document_chunks gets tagged with semester_key so retrieval
--    can apply recency weighting.
--
-- B. WORKER HEARTBEATS — each Railway service writes a heartbeat every
--    N seconds. A query against this table answers "is X healthy?" and
--    "when did Y last process a job?" The weekly_report.py script reads this
--    for the ops summary. No external monitoring service required.
-- =============================================================================


-- ── A. ACADEMIC CALENDAR ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS academic_calendar (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES tenants(id),

    -- Identifiers
    academic_year   TEXT        NOT NULL,   -- '1447' (Hijri) or '2025-2026' (Gregorian)
    semester        TEXT        NOT NULL
                                CHECK (semester IN ('first', 'second', 'summer')),
    semester_key    TEXT        GENERATED ALWAYS AS
                                (academic_year || '-' || semester) STORED,

    -- What happened
    event_type      TEXT        NOT NULL,
        -- 'semester_start'    — first day of classes
        -- 'semester_end'      — last day of classes
        -- 'midterm_start'     — midterm exam window opens
        -- 'midterm_end'       — midterm exam window closes
        -- 'final_start'       — final exam window opens
        -- 'final_end'         — final exam window closes
        -- 'add_drop_end'      — last day to add/drop courses
        -- 'withdrawal_end'    — last day to withdraw without academic penalty
        -- 'results_release'   — grade publication date
        -- 'registration_start'— registration opens for next semester
        -- 'registration_end'  — registration closes
        -- 'holiday'           — university holiday
    event_name_ar   TEXT,
    event_name_en   TEXT,

    start_date      DATE,
    end_date        DATE,
    notes           TEXT,

    UNIQUE (tenant_id, academic_year, semester, event_type)
);

CREATE INDEX IF NOT EXISTS idx_academic_calendar_tenant_year
    ON academic_calendar(tenant_id, academic_year, semester);

CREATE INDEX IF NOT EXISTS idx_academic_calendar_dates
    ON academic_calendar(tenant_id, start_date, end_date)
    WHERE start_date IS NOT NULL;

-- Seed SEU 1447H / 2025-2026 calendar (from التقويم الأكاديمي 1447 in repo)
INSERT INTO academic_calendar
    (tenant_id, academic_year, semester, event_type, event_name_ar, event_name_en, start_date, end_date)
VALUES
    -- First semester 1447H
    ('00000000-0000-0000-0000-000000000001','1447','first','semester_start',
     'بداية الفصل الأول','First Semester Start','2025-09-07',NULL),
    ('00000000-0000-0000-0000-000000000001','1447','first','add_drop_end',
     'نهاية فترة الإضافة والحذف','Add/Drop Deadline','2025-09-20',NULL),
    ('00000000-0000-0000-0000-000000000001','1447','first','midterm_start',
     'بداية اختبارات المنتصف','Midterm Exams Start','2025-11-09','2025-11-20'),
    ('00000000-0000-0000-0000-000000000001','1447','first','withdrawal_end',
     'نهاية فترة الانسحاب','Withdrawal Deadline','2025-11-22',NULL),
    ('00000000-0000-0000-0000-000000000001','1447','first','final_start',
     'بداية اختبارات النهاية','Final Exams Start','2026-01-04','2026-01-15'),
    ('00000000-0000-0000-0000-000000000001','1447','first','semester_end',
     'نهاية الفصل الأول','First Semester End','2026-01-15',NULL),
    -- Second semester 1447H
    ('00000000-0000-0000-0000-000000000001','1447','second','semester_start',
     'بداية الفصل الثاني','Second Semester Start','2026-02-01',NULL),
    ('00000000-0000-0000-0000-000000000001','1447','second','midterm_start',
     'بداية اختبارات المنتصف','Midterm Exams Start','2026-03-22','2026-04-02'),
    ('00000000-0000-0000-0000-000000000001','1447','second','final_start',
     'بداية اختبارات النهاية','Final Exams Start','2026-05-17','2026-05-28'),
    ('00000000-0000-0000-0000-000000000001','1447','second','semester_end',
     'نهاية الفصل الثاني','Second Semester End','2026-05-28',NULL)
ON CONFLICT (tenant_id, academic_year, semester, event_type) DO NOTHING;

-- Add semester_key to document_chunks for temporal retrieval weighting
ALTER TABLE document_chunks
    ADD COLUMN IF NOT EXISTS semester_key TEXT;  -- e.g. '1447-first', '1446-second'

CREATE INDEX IF NOT EXISTS idx_document_chunks_semester
    ON document_chunks(semester_key, tenant_id)
    WHERE semester_key IS NOT NULL;


-- ── B. WORKER HEARTBEATS ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS worker_heartbeats (
    worker_id       TEXT        PRIMARY KEY,    -- e.g. 'listener-1', 'embed-1'
    service_name    TEXT        NOT NULL,       -- matches Procfile key: 'listener', 'embed', etc.
    tenant_id       UUID        REFERENCES tenants(id),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    jobs_processed  BIGINT      NOT NULL DEFAULT 0,
    jobs_failed     BIGINT      NOT NULL DEFAULT 0,
    last_job_id     TEXT,
    status          TEXT        NOT NULL DEFAULT 'running'
                                CHECK (status IN ('running', 'idle', 'error', 'stopped')),
    metadata        JSONB       NOT NULL DEFAULT '{}'
);

-- No index needed — tiny table, always read in full for ops reports.


-- ── QUERY_LOGS CLEANUP ────────────────────────────────────────────────────────
-- query_logs (migration 006) was superseded by learning_events (migration 007).
-- It has 0 rows. Drop it cleanly.

DROP TABLE IF EXISTS query_logs CASCADE;
