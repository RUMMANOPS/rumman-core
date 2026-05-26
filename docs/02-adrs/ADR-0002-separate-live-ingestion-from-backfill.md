# ADR-0002: Separate Live Ingestion from Historical Backfill

## Status

Accepted

## Context

Early versions of RUMMAN attempted to process Telegram live ingestion and historical crawling inside the same runtime process.

This caused major architectural problems:

- startup delays
- unstable pipelines
- memory pressure
- uncontrolled Telegram crawling
- ingestion blocking
- inability to scale safely
- operational instability

The system also lacked checkpointing and resumable backfill logic.

## Decision

RUMMAN will permanently separate:

- Live ingestion
- Historical backfill

into independent execution paths.

### Live Ingestion Responsibilities

The live listener is responsible only for:

- receiving new Telegram messages
- lightweight normalization
- inserting messages into Supabase
- updating sync checkpoints

The live listener must never perform uncontrolled historical crawling.

### Historical Backfill Responsibilities

Historical ingestion is handled by dedicated workers.

Backfill workers:

- process controlled batches
- use checkpoint-based progress tracking
- support resumability
- use lease-based job coordination
- support retry and stale-job recovery
- operate independently from the live listener

## Consequences

### Positive

- stable live ingestion
- scalable architecture
- resumable backfill
- distributed worker support
- operational observability
- safer Telegram rate-limit handling
- cleaner operational boundaries

### Negative

- additional system complexity
- multiple runtime processes
- job orchestration requirements
- lease coordination overhead

## Long-Term Direction

This decision moves RUMMAN toward becoming a true Operational Intelligence Platform rather than a simple chatbot or message logger.
