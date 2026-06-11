-- Migration 041: Telegram Signal Layer
--
-- Converts raw Telegram messages into typed academic signals.
-- This is the second accumulating asset after student_interactions.
--
-- Design principle: per-message granularity (unlike message_signals which is
-- aggregated summaries). Every signal is traceable to its source message.
--
-- Four signal types collected now:
--   topic_mention    — a concept/topic is named
--   confusion        — student expresses confusion or asks for help
--   exam_emphasis    — something is flagged as important for the exam
--   answer_sharing   — an answer, solution, or summary is shared
--
-- topic_id is left NULL at insert time; topic_normalizer will fill it
-- asynchronously by matching extracted_topic → kg_topics.

-- ---------------------------------------------------------------------------
-- telegram_signals — one row per signal per message
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS telegram_signals (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',

    -- Source traceability
    source_message_id   UUID        NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    platform_chat_id    TEXT        NOT NULL,
    chat_name           TEXT,

    -- Academic attribution
    course_code         TEXT,       -- inferred from chat_name regex; NULL if unattributable

    -- Signal
    signal_type         TEXT        NOT NULL,
        -- 'topic_mention' | 'confusion' | 'exam_emphasis' | 'answer_sharing'
    extracted_topic     TEXT,       -- raw topic string from message
    topic_id            UUID        REFERENCES kg_topics(id) ON DELETE SET NULL,
        -- filled by topic_normalizer worker (async, not at insert time)
    raw_text            TEXT,       -- excerpt from message that triggered signal (≤200 chars)
    confidence          NUMERIC(4,3) NOT NULL DEFAULT 0.75,

    -- Temporal bucketing for trend queries
    week_of             DATE        NOT NULL,   -- Monday of the message's week
    message_sent_at     TIMESTAMPTZ,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ts_signal_type_check CHECK (signal_type IN (
        'topic_mention', 'confusion', 'exam_emphasis', 'answer_sharing'
    )),
    CONSTRAINT ts_confidence_range CHECK (confidence BETWEEN 0 AND 1)
);

-- Dedup: one signal type per message (a message can have multiple signal types
-- but not the same type twice)
CREATE UNIQUE INDEX IF NOT EXISTS ts_message_signal_unique
    ON telegram_signals (source_message_id, signal_type);

-- Primary analytics query: what's hot this week per course?
CREATE INDEX IF NOT EXISTS ts_course_week_idx
    ON telegram_signals (tenant_id, course_code, signal_type, week_of DESC)
    WHERE course_code IS NOT NULL;

-- AKG link: all signals about a specific concept
CREATE INDEX IF NOT EXISTS ts_topic_idx
    ON telegram_signals (topic_id)
    WHERE topic_id IS NOT NULL;

-- Backfill progress queries
CREATE INDEX IF NOT EXISTS ts_chat_week_idx
    ON telegram_signals (platform_chat_id, week_of DESC);

-- ---------------------------------------------------------------------------
-- telegram_signal_cursors — per-chat processing state
-- ---------------------------------------------------------------------------
-- Tracks which messages have been processed per chat.
-- Avoids touching the messages table and allows resumable processing.

CREATE TABLE IF NOT EXISTS telegram_signal_cursors (
    platform_chat_id    TEXT        PRIMARY KEY,
    chat_name           TEXT,
    course_code         TEXT,       -- inferred course code for this chat (if known)
    last_message_id     UUID,       -- last messages.id processed (NULL = not started)
    last_sent_at        TIMESTAMPTZ,
    processed_count     INT         NOT NULL DEFAULT 0,
    signal_count        INT         NOT NULL DEFAULT 0,
    last_run_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE telegram_signals IS
    'Per-message academic signals extracted from Telegram. '
    'Typed signals (topic_mention, confusion, exam_emphasis, answer_sharing) '
    'attributed to course codes inferred from chat names. '
    'topic_id filled asynchronously by topic_normalizer_worker.';

COMMENT ON TABLE telegram_signal_cursors IS
    'Processing cursor per Telegram chat. '
    'Enables resumable backfill without modifying the messages table.';
