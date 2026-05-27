-- Migration 001: Daily Brief tables
-- Run in Supabase SQL editor (Dashboard → SQL Editor → New query)
-- Required before running app/daily_brief.py

-- Audit trail for each brief extraction run (one row per chat per execution)
CREATE TABLE IF NOT EXISTS brief_runs (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           text NOT NULL DEFAULT 'default',

    worker              text NOT NULL DEFAULT 'daily_brief_v1',
    prompt_version      text NOT NULL,
    model               text NOT NULL,

    chat_name           text,
    platform_chat_id    text,
    window_start        timestamptz NOT NULL,
    window_end          timestamptz NOT NULL,
    message_count       int NOT NULL DEFAULT 0,
    source_message_ids  text[] NOT NULL DEFAULT '{}',

    input_tokens        int,
    output_tokens       int,
    cost_usd            float,

    raw_output          jsonb,
    context_summary     text,

    status              text NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    error               text,

    started_at          timestamptz DEFAULT now(),
    completed_at        timestamptz,
    created_at          timestamptz DEFAULT now()
);

-- Extracted operational claims (tasks, deadlines, decisions, risks, follow-ups)
CREATE TABLE IF NOT EXISTS extracted_items (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           text NOT NULL DEFAULT 'default',

    item_type           text NOT NULL
                            CHECK (item_type IN ('task', 'deadline', 'decision', 'risk', 'follow_up')),
    content             text NOT NULL,
    due_date            date,
    course_code         text,

    valid_from          timestamptz DEFAULT now(),
    valid_until         timestamptz,

    -- Claim validity state machine (see docs/architecture/claim-model.md)
    validity_status     text NOT NULL DEFAULT 'machine_asserted'
                            CHECK (validity_status IN (
                                'machine_asserted', 'confirmed', 'rejected',
                                'machine_rejected', 'expired', 'superseded'
                            )),
    confidence          float NOT NULL CHECK (confidence >= 0 AND confidence <= 1),

    -- Provenance (both required — see docs/constraints/hard-boundaries.md)
    chat_name           text NOT NULL,
    source_message_ids  text[] NOT NULL,
    brief_run_id        uuid NOT NULL REFERENCES brief_runs(id),

    created_at          timestamptz DEFAULT now(),
    updated_at          timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_extracted_items_status_chat
    ON extracted_items(validity_status, chat_name);

CREATE INDEX IF NOT EXISTS idx_extracted_items_run
    ON extracted_items(brief_run_id);

CREATE INDEX IF NOT EXISTS idx_extracted_items_type
    ON extracted_items(item_type, validity_status);

CREATE INDEX IF NOT EXISTS idx_brief_runs_status
    ON brief_runs(status, created_at DESC);
