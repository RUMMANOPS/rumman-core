-- =============================================================================
-- 025_claim_model_temporal_and_contradiction.sql
--
-- Extends the claim model (015) with two missing dimensions:
--
-- A. TEMPORAL VALIDITY
--    document_chunks   — adds valid_from / valid_until (documents are permanent
--                        historical records; expiry is via superseded_by, not dates)
--    extracted_items   — valid_from / valid_until columns already exist from 001
--                        but valid_until was never populated; backfilled here
--
-- B. CONTRADICTION & SUPERSESSION TRACKING
--    Both tables get contradiction_ids UUID[] and superseded_by UUID.
--    contradiction_ids: set of claims in the same table that directly contradict
--      this one. Populated by contradiction detection workers (not yet built).
--    superseded_by: FK to a newer version of this claim. When a study plan
--      is re-ingested or a brief item is corrected, old row is not deleted —
--      it is preserved with superseded_by → the replacement.
--
-- C. VIEWS FOR TEMPORAL-SAFE RETRIEVAL
--    active_extracted_items — filters expired, rejected, and superseded rows
--    active_document_chunks — same for chunks (today: only gates superseded rows)
--
-- Design decisions:
--    - document_chunks.valid_until is always NULL for now. Documents are
--      permanent historical records; only intelligence items (extracted_items)
--      have semester-bounded TTLs. This keeps the match_documents RPC unchanged.
--    - All changes are additive (IF NOT EXISTS / ALTER ADD COLUMN). Safe to
--      re-apply.
--    - New rows get valid_from = CURRENT_DATE automatically via column DEFAULT.
-- =============================================================================


-- ── A. TEMPORAL VALIDITY on document_chunks ───────────────────────────────────

ALTER TABLE document_chunks
    ADD COLUMN IF NOT EXISTS valid_from  DATE DEFAULT CURRENT_DATE,
    ADD COLUMN IF NOT EXISTS valid_until DATE;

-- Backfill valid_from from ingested_at for all existing rows.
UPDATE document_chunks
   SET valid_from = ingested_at::date
 WHERE valid_from IS NULL;

-- valid_until stays NULL for all document_chunks — permanent historical records.
-- Versioning is tracked via superseded_by, not date expiry.


-- ── A. valid_until BACKFILL on extracted_items ────────────────────────────────
--
-- All 28 existing rows are current-semester intelligence (1447-second).
-- Semester 1447-second closes on exam appeal deadline: 2026-06-25.
-- Items created before 2026-01-01 would be prior-semester stragglers; leave
-- their valid_until NULL (treated as no-expiry) so nothing disappears silently.

UPDATE extracted_items
   SET valid_until = '2026-06-25 23:59:59+00'
 WHERE valid_until IS NULL
   AND created_at >= '2026-01-01';


-- ── B. CONTRADICTION & SUPERSESSION — document_chunks ─────────────────────────

ALTER TABLE document_chunks
    ADD COLUMN IF NOT EXISTS contradiction_ids UUID[]   DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS superseded_by     UUID     REFERENCES document_chunks(id);


-- ── B. CONTRADICTION & SUPERSESSION — extracted_items ────────────────────────

ALTER TABLE extracted_items
    ADD COLUMN IF NOT EXISTS contradiction_ids UUID[]   DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS superseded_by     UUID     REFERENCES extracted_items(id);


-- ── INDEXES ───────────────────────────────────────────────────────────────────

-- Temporal range queries on document_chunks (future: find expiring chunks)
CREATE INDEX IF NOT EXISTS idx_document_chunks_valid_from
    ON document_chunks (valid_from);

CREATE INDEX IF NOT EXISTS idx_document_chunks_valid_until
    ON document_chunks (valid_until)
    WHERE valid_until IS NOT NULL;

-- Supersession lookups: "what has been superseded by this chunk?"
CREATE INDEX IF NOT EXISTS idx_document_chunks_superseded_by
    ON document_chunks (superseded_by)
    WHERE superseded_by IS NOT NULL;

-- GIN index for contradiction_ids array queries (e.g. @> '{uuid}')
CREATE INDEX IF NOT EXISTS idx_document_chunks_contradiction_ids
    ON document_chunks USING gin (contradiction_ids)
    WHERE contradiction_ids != '{}';

-- Temporal queries on extracted_items (gap analyst, expiry sweeps)
CREATE INDEX IF NOT EXISTS idx_extracted_items_valid_until
    ON extracted_items (valid_until)
    WHERE valid_until IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_extracted_items_superseded_by
    ON extracted_items (superseded_by)
    WHERE superseded_by IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_extracted_items_contradiction_ids
    ON extracted_items USING gin (contradiction_ids)
    WHERE contradiction_ids != '{}';


-- ── C. VIEWS FOR TEMPORAL-SAFE RETRIEVAL ──────────────────────────────────────

-- active_extracted_items
-- The retrieval layer (search_api._retrieve_intelligence_items) should query
-- this view instead of the raw table. Three filters applied automatically:
--   1. Temporal: not yet expired
--   2. Validity: not rejected by manual review
--   3. Versioning: not superseded by a newer item
CREATE OR REPLACE VIEW active_extracted_items AS
SELECT *
FROM   extracted_items
WHERE  (valid_until IS NULL OR valid_until >= NOW())
  AND  validity_status != 'rejected'
  AND  superseded_by IS NULL;

-- active_document_chunks
-- Today: only gates superseded chunks (valid_until is always NULL for documents).
-- In future, when telegram_export chunks get semester-scoped valid_until,
-- this view gates them automatically without changing any retrieval code.
-- The match_documents RPC still queries the raw table for vector search;
-- this view is for non-vector retrieval paths (curriculum facts, gap analyst).
CREATE OR REPLACE VIEW active_document_chunks AS
SELECT *
FROM   document_chunks
WHERE  (valid_until IS NULL OR valid_until >= CURRENT_DATE)
  AND  attribution_status != 'rejected'
  AND  superseded_by IS NULL;
