-- =============================================================================
-- 026_analysis_runs.sql
--
-- Persistent store for analyst worker outputs.
--
-- Analysts (gap_analyst, qa_miner, etc.) are read-only over the corpus —
-- they never mutate messages or chunks. They DO write structured reports here
-- so findings persist across runs and can be queried by the retrieval layer.
--
-- Table: analysis_runs
--   Each row is one complete analyst execution. The "output" JSONB column
--   holds the full structured report (gaps list, qa pairs, etc.) —
--   schema varies by analyst_type.
--
-- Table: gap_items
--   Normalised rows extracted from gap_analyst output. Each row is one
--   identified knowledge gap cluster with enough metadata for the retrieval
--   layer to decide whether to inject a "gap hint" into synthesis context.
--
-- Design:
--   - tenant_id on both tables (ADR-0004 requirement).
--   - analysis_runs is append-only; never update, never delete.
--   - gap_items.resolved_at is set when the gap is closed (document ingested,
--     extracted_item added, etc.) — not deleted so we can track resolution rate.
--   - All changes additive (IF NOT EXISTS). Safe to re-apply.
-- =============================================================================


-- ── analysis_runs ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS analysis_runs (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL,
    analyst_type    TEXT        NOT NULL,   -- 'gap_analyst', 'qa_miner', etc.
    ran_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    window_start    TIMESTAMPTZ,            -- source data window start
    window_end      TIMESTAMPTZ,            -- source data window end
    event_count     INT,                    -- number of events / rows processed
    output          JSONB       NOT NULL DEFAULT '{}',  -- full structured report
    cost_usd        NUMERIC(10,6),
    model           TEXT,
    worker          TEXT,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_analysis_runs_tenant_type
    ON analysis_runs (tenant_id, analyst_type);

CREATE INDEX IF NOT EXISTS idx_analysis_runs_ran_at
    ON analysis_runs (ran_at DESC);


-- ── gap_items ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS gap_items (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL,
    analysis_run_id UUID        NOT NULL REFERENCES analysis_runs(id),

    -- Gap classification
    gap_type        TEXT        NOT NULL,
    -- 'content_gap'    — corpus has nothing on this topic
    -- 'retrieval_gap'  — content exists but similarity too low (needs better chunking/metadata)
    -- 'coverage_gap'   — partial content (one semester, not another)

    -- What the gap is about
    cluster_label   TEXT        NOT NULL,   -- human-readable topic label
    course_code     TEXT,                   -- null if cross-course
    example_queries TEXT[]      NOT NULL DEFAULT '{}',  -- representative raw queries
    occurrence_count INT        NOT NULL DEFAULT 1,     -- how many events hit this gap

    -- Severity (computed by analyst)
    severity        TEXT        NOT NULL DEFAULT 'medium',  -- 'high', 'medium', 'low'
    top_similarity  NUMERIC(5,4),  -- best similarity score seen for these queries (low = worse)

    -- Resolution tracking
    resolved_at     TIMESTAMPTZ,   -- null = still open
    resolved_by     TEXT,          -- 'ingest', 'extracted_item', 'manual', etc.

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gap_items_tenant_open
    ON gap_items (tenant_id, resolved_at)
    WHERE resolved_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_gap_items_course
    ON gap_items (course_code)
    WHERE course_code IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_gap_items_severity
    ON gap_items (severity, created_at DESC);
