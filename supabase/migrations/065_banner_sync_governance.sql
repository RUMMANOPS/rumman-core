-- Migration 065: Banner sections sync governance
--
-- BANNER-GOV-1 / DRAFT. Applied manually after founder approval.
-- Additive only (CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).
-- No drops, no data writes, does NOT touch the term_sections UNIQUE(term_code, crn) key.
--
-- Row model (CONFIRMED): one term_sections row per (term_code, crn).
-- Multiple meeting days live inside term_sections.class_meetings (JSONB) — never extra rows.
-- SEU default tenant: 00000000-0000-0000-0000-000000000001

-- ── banner_sync_runs ─────────────────────────────────────────────────────────
-- One row per sync run. Observability + audit trail + freshness provenance:
-- term_sections.sync_run_id + last_checked_at point back to the run that set them.

CREATE TABLE IF NOT EXISTS banner_sync_runs (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    term_code           TEXT        NOT NULL,

    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    status              TEXT        NOT NULL DEFAULT 'running'
                            CHECK (status IN ('running', 'completed', 'failed', 'partial')),
    trigger             TEXT        NOT NULL DEFAULT 'scheduled'
                            CHECK (trigger IN ('scheduled', 'manual', 'pre_approval')),

    -- Counters
    source_total_count  INT,        -- totalCount reported by Banner (e.g. 361)
    sections_seen       INT         DEFAULT 0,
    sections_added      INT         DEFAULT 0,
    sections_updated    INT         DEFAULT 0,
    sections_not_seen   INT         DEFAULT 0,
    http_ok_count       INT         DEFAULT 0,
    http_error_count    INT         DEFAULT 0,

    -- Audit
    snapshot_path       TEXT,       -- path/key of raw snapshot in Supabase Storage (bucket created separately, not in SQL)
    error               TEXT,
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_banner_sync_runs_term_started
    ON banner_sync_runs (term_code, started_at DESC);

COMMENT ON TABLE  banner_sync_runs IS 'Audit log of Banner sections sync runs — observability, provenance, debugging';
COMMENT ON COLUMN banner_sync_runs.snapshot_path IS 'Reference to raw Banner JSON in Supabase Storage (bucket provisioned outside SQL); not stored in repo';


-- ── term_sections: sync governance columns (additive; key untouched) ─────────
-- sync_status is the SOURCE OF TRUTH for lifecycle. The existing is_active (migration 063)
-- is kept only for backward-compat and should mirror (sync_status = 'active').

ALTER TABLE term_sections
    ADD COLUMN IF NOT EXISTS last_checked_at  TIMESTAMPTZ,                 -- last sync that verified this section
    ADD COLUMN IF NOT EXISTS last_seen_at     TIMESTAMPTZ,                 -- last sync where it appeared in Banner
    ADD COLUMN IF NOT EXISTS sync_run_id      UUID REFERENCES banner_sync_runs(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS sync_status      TEXT
        DEFAULT 'active'
        CHECK (sync_status IN ('active', 'not_seen', 'removed')),         -- lifecycle (source of truth)
    ADD COLUMN IF NOT EXISTS wait_capacity    INT,
    ADD COLUMN IF NOT EXISTS wait_count       INT,
    ADD COLUMN IF NOT EXISTS wait_available   INT,
    ADD COLUMN IF NOT EXISTS part_of_term     TEXT,                        -- Banner partOfTerm (full/sub-term)
    ADD COLUMN IF NOT EXISTS source_hash      TEXT,                        -- hash of the NORMALIZED Banner section payload (change detection); NOT a hash of the full raw file
    ADD COLUMN IF NOT EXISTS raw_changed_at   TIMESTAMPTZ;                 -- when normalized content last actually changed

CREATE INDEX IF NOT EXISTS idx_term_sections_sync_status
    ON term_sections (term_code, sync_status);

COMMENT ON COLUMN term_sections.sync_status     IS 'active=present in last Banner sync; not_seen=missing from latest sync; removed=confirmed gone. SOURCE OF TRUTH (is_active is legacy mirror).';
COMMENT ON COLUMN term_sections.source_hash     IS 'hash of the normalized Banner section payload (seats/open/meetings/...) for change detection — not a hash of the raw file';
COMMENT ON COLUMN term_sections.last_checked_at IS 'timestamp of the most recent sync run that verified this section (freshness basis)';
