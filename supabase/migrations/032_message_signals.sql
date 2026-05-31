-- migration 032: message_signals
-- Typed intelligence signals extracted from Telegram messages.
-- Complement to document_chunks: documents tell us what EXISTS,
-- messages tell us what MATTERS (exam emphasis, difficulty, resource recs, confusion clusters).
--
-- Populated by: scripts/message_signal_worker.py (run on demand)
-- Consumed by: _build_context_block() in search_api.py (injected into synthesis prompt)
-- Refresh: monthly, or after significant new message ingestion

CREATE TABLE IF NOT EXISTS message_signals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    course_code     TEXT,                  -- nullable: signals may be general or multi-course
    chat_name       TEXT NOT NULL,
    signal_type     TEXT NOT NULL,         -- exam_emphasis | difficulty | professor_note | resource_rec | confusion_cluster
    signal_content  TEXT NOT NULL,         -- short natural-language summary of the signal
    source_count    INT  NOT NULL DEFAULT 1, -- number of messages supporting this signal
    source_message_ids  BIGINT[],          -- platform_message_id values from messages table
    confidence      NUMERIC(4,3) CHECK (confidence BETWEEN 0 AND 1),
    semester_hint   TEXT,                  -- e.g. "Fall 2025", null if unclear
    is_current_semester BOOLEAN NOT NULL DEFAULT FALSE,
    extracted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,           -- NULL = permanent; set for time-bound signals
    model           TEXT,                  -- which model extracted this
    tokens_used     INT
);

-- Index for synthesis lookup: course + type + current semester first
CREATE INDEX IF NOT EXISTS idx_message_signals_course_type
    ON message_signals (course_code, signal_type, is_current_semester DESC, source_count DESC)
    WHERE course_code IS NOT NULL;

-- Index for tenant filtering
CREATE INDEX IF NOT EXISTS idx_message_signals_tenant
    ON message_signals (tenant_id, extracted_at DESC);

-- Index for expiry pruning
CREATE INDEX IF NOT EXISTS idx_message_signals_expires
    ON message_signals (expires_at)
    WHERE expires_at IS NOT NULL;

COMMENT ON TABLE message_signals IS
    'Community intelligence signals from Telegram messages: what students say matters (exam emphasis, difficulty, confusion, resources).';

COMMENT ON COLUMN message_signals.signal_type IS
    'exam_emphasis: professor/student flagging importance for exams. '
    'difficulty: recurring confusion or struggle signals. '
    'professor_note: direct instructor guidance. '
    'resource_rec: recommended resources (videos, books, sites). '
    'confusion_cluster: multiple students asking the same thing = knowledge gap.';

COMMENT ON COLUMN message_signals.source_count IS
    'Number of distinct messages supporting this signal. Higher = more reliable.';

COMMENT ON COLUMN message_signals.is_current_semester IS
    'True if signal was extracted from messages within the current academic semester.';
