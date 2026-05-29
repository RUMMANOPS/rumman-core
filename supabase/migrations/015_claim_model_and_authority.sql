-- =============================================================================
-- 015_claim_model_and_authority.sql
--
-- Two foundations shipped together because they are tightly coupled:
--
-- A. AUTHORITY TIER — every piece of content carries a grade:
--      official   → extracted from university-sourced documents
--      verified   → student-produced content that has been validated
--      community  → raw community Telegram content (default)
--    Propagated from source_documents → document_chunks at ingest time.
--    Used by synthesis prompt to cite sources by tier, not just by course.
--
-- B. CLAIM MODEL — every AI-generated assertion has a provenance trail:
--      ai_runs   → one row per AI job (attribution, extraction, brief, etc.)
--      attribution columns on document_chunks → link each AI-assigned
--        course_code back to the ai_run that produced it, with a validity
--        state machine so machine guesses never silently become facts.
--
-- Without B, bulk AI attribution is irrecoverable if wrong.
-- Without A, official university answers compete at equal weight with rumors.
-- =============================================================================


-- ── A. AUTHORITY TIER ────────────────────────────────────────────────────────

ALTER TABLE source_documents
    ADD COLUMN IF NOT EXISTS authority_tier TEXT NOT NULL DEFAULT 'community'
        CHECK (authority_tier IN ('official', 'verified', 'community'));

ALTER TABLE document_chunks
    ADD COLUMN IF NOT EXISTS authority_tier TEXT DEFAULT 'community'
        CHECK (authority_tier IN ('official', 'verified', 'community'));

-- Backfill: propagate tier from source_documents to their chunks.
UPDATE document_chunks dc
SET    authority_tier = sd.authority_tier
FROM   source_documents sd
WHERE  dc.source_document_id = sd.id
AND    dc.authority_tier IS NULL;

-- Official documents: anything ingested via batch_ingest_seu.py from the
-- official SEU knowledge repository is 'official'. Source types that are
-- definitionally official:
UPDATE source_documents
SET    authority_tier = 'official'
WHERE  source_type IN ('study_plan', 'regulation', 'course_description')
AND    institution = 'SEU';

-- Propagate the 'official' upgrade to existing chunks.
UPDATE document_chunks dc
SET    authority_tier = 'official'
FROM   source_documents sd
WHERE  dc.source_document_id = sd.id
AND    sd.authority_tier = 'official'
AND    dc.authority_tier = 'community';

CREATE INDEX IF NOT EXISTS idx_document_chunks_authority
    ON document_chunks(authority_tier, tenant_id);

CREATE INDEX IF NOT EXISTS idx_source_documents_authority
    ON source_documents(authority_tier, source_type);


-- ── B. AI RUNS — provenance trail for every AI-generated claim ───────────────

CREATE TABLE IF NOT EXISTS ai_runs (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        REFERENCES tenants(id),

    -- What ran
    worker          TEXT        NOT NULL,   -- 'attribution_worker', 'intelligence_worker', etc.
    model           TEXT        NOT NULL,   -- 'gpt-4o-mini', 'gpt-4o', 'gpt-4o-vision', etc.
    prompt_version  TEXT        NOT NULL DEFAULT '1.0',
    job_type        TEXT,                   -- mirrors processing_jobs.job_type if originated there

    -- What it cost
    input_tokens    INT,
    output_tokens   INT,
    cost_usd        FLOAT,
    duration_ms     INT,

    -- What it touched
    subject_type    TEXT,                   -- 'document_chunk', 'message', 'source_document'
    subject_id      UUID,                   -- PK of the row this run processed

    -- Non-sensitive summary for audit (NO raw content, NO PII)
    input_summary   TEXT,
    output_summary  TEXT,

    -- Lifecycle
    status          TEXT        NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ai_runs_subject
    ON ai_runs(subject_type, subject_id);

CREATE INDEX IF NOT EXISTS idx_ai_runs_worker_status
    ON ai_runs(worker, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ai_runs_tenant
    ON ai_runs(tenant_id, created_at DESC);


-- ── ATTRIBUTION STATE MACHINE on document_chunks ─────────────────────────────
--
-- Every chunk starts as 'original' (course_code came from ingest metadata).
-- Bulk AI attribution sets status → 'machine_asserted' + links the ai_run.
-- Human/downstream validation can promote to 'confirmed' or 'rejected'.
-- A rejected attribution reverts course_code to NULL; the chunk is re-queued.
--
-- This means we can always answer: "did a human verify this course tag?"

ALTER TABLE document_chunks
    ADD COLUMN IF NOT EXISTS attribution_status TEXT DEFAULT 'original'
        CHECK (attribution_status IN (
            'original',         -- set at ingest from file metadata
            'machine_asserted', -- set by AI attribution worker
            'confirmed',        -- validated by human or downstream logic
            'rejected'          -- AI was wrong; course_code cleared
        ));

ALTER TABLE document_chunks
    ADD COLUMN IF NOT EXISTS attribution_confidence FLOAT;

ALTER TABLE document_chunks
    ADD COLUMN IF NOT EXISTS attribution_ai_run_id UUID
        REFERENCES ai_runs(id) ON DELETE SET NULL;

-- Backfill: all existing chunks with a course_code came from original ingest.
UPDATE document_chunks
SET    attribution_status = 'original'
WHERE  attribution_status IS NULL;

CREATE INDEX IF NOT EXISTS idx_document_chunks_attribution
    ON document_chunks(attribution_status, tenant_id)
    WHERE attribution_status IN ('machine_asserted', 'rejected');
