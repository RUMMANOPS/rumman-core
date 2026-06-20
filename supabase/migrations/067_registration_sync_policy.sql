-- Migration 067: Registration windows + self-operating Banner sync policy
--
-- DRAFT. Applied manually after founder approval. Additive only (ADD COLUMN IF NOT EXISTS).
-- Concern = Banner sync OPERATION (cadence), kept SEPARATE from the registration lifecycle (066).
--
-- Purpose: the banner_sync worker computes its interval each cycle from the current date vs
-- the registration window + sync_policy - so NO manual BANNER_SYNC_INTERVAL_SECONDS changes.
-- Window dates are sourced from the official SEU announcement (registration 24-27 June 2026),
-- populated later (not in this migration).

ALTER TABLE app_term_config
    ADD COLUMN IF NOT EXISTS registration_start_date DATE,   -- official Banner registration window start (e.g. 2026-06-24)
    ADD COLUMN IF NOT EXISTS registration_end_date   DATE,   -- official Banner registration window end   (e.g. 2026-06-27)
    ADD COLUMN IF NOT EXISTS sync_policy             JSONB
        DEFAULT '{"pre_registration": 900, "registration_open": 120, "post_registration": 21600, "normal": 43200}'::jsonb;

COMMENT ON COLUMN app_term_config.registration_start_date IS 'Official Banner registration window start; drives dynamic banner_sync cadence (no manual env change)';
COMMENT ON COLUMN app_term_config.registration_end_date   IS 'Official Banner registration window end; after this, sync drops to post_registration cadence';
COMMENT ON COLUMN app_term_config.sync_policy IS 'Dynamic banner_sync intervals in seconds per window: pre_registration / registration_open / post_registration / normal. Worker self-selects by date - env interval becomes a fallback only.';
