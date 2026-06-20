-- Migration 064: app_term_config — instruction dates + term discovery
--
-- BANNER-GOV-1 / DRAFT. Applied manually after founder approval.
-- Additive only (ADD COLUMN IF NOT EXISTS). No drops, no data writes.
--
-- Instruction dates (بداية/نهاية الدراسة) are DERIVED later by BANNER-SYNC-1 from
-- Banner's live meetingTime.startDate/endDate (min start / max end across active
-- sections). They are NOT populated here. Banner may return these as Hijri strings,
-- so we keep both the raw value (as Banner gives it) and the converted Gregorian DATE.
-- No assumption about format is made in this migration.

ALTER TABLE app_term_config
    -- Gregorian instruction window (filled by sync after a verified conversion)
    ADD COLUMN IF NOT EXISTS instruction_start_date        DATE,
    ADD COLUMN IF NOT EXISTS instruction_end_date          DATE,
    -- Raw values exactly as Banner returns them (e.g. Hijri "12/محرم/1448") — never lost
    ADD COLUMN IF NOT EXISTS instruction_start_raw         TEXT,
    ADD COLUMN IF NOT EXISTS instruction_end_raw           TEXT,
    -- Provenance of the instruction dates
    ADD COLUMN IF NOT EXISTS instruction_dates_source      TEXT,          -- e.g. 'banner_sync'
    ADD COLUMN IF NOT EXISTS instruction_dates_verified_at TIMESTAMPTZ,
    -- Active-term discovery governance
    ADD COLUMN IF NOT EXISTS active_term_status            TEXT
        DEFAULT 'discovered'
        CHECK (active_term_status IN ('discovered', 'verified', 'stale')),
    ADD COLUMN IF NOT EXISTS last_banner_discovery_at      TIMESTAMPTZ;

COMMENT ON COLUMN app_term_config.instruction_start_date IS 'بداية الدراسة (Gregorian) — derived by BANNER-SYNC from min meetingTime.startDate of active sections';
COMMENT ON COLUMN app_term_config.instruction_end_date   IS 'نهاية الدراسة (Gregorian) — derived by BANNER-SYNC from max meetingTime.endDate of active sections';
COMMENT ON COLUMN app_term_config.instruction_start_raw  IS 'raw start date exactly as Banner returns (may be Hijri); preserved before conversion';
COMMENT ON COLUMN app_term_config.instruction_end_raw    IS 'raw end date exactly as Banner returns (may be Hijri); preserved before conversion';
COMMENT ON COLUMN app_term_config.active_term_status      IS 'discovered=seen in Banner getTerms; verified=confirmed active; stale=needs re-discovery';
