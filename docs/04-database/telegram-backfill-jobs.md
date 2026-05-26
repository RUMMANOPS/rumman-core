# telegram_backfill_jobs

## Purpose

`telegram_backfill_jobs` stores controlled historical Telegram ingestion work.

Each row represents a backfill job for one Telegram chat.

## Why It Exists

Historical Telegram ingestion must be:

- chunked
- resumable
- observable
- retryable
- worker-safe
- rate-limit aware

## Key Fields

- platform_chat_id: target Telegram chat
- chat_name: human-readable chat name
- chat_type: private, group, supergroup, or channel
- status: pending, running, completed, or failed
- batch_size: number of messages to process per run
- last_processed_message_id: last message processed by the worker
- oldest_reached_message_id: oldest message reached so far
- total_processed: total backfilled messages processed
- retry_count: number of failed attempts
- worker_id: worker currently processing the job
- heartbeat_at: last worker heartbeat
- lease_expires_at: when the current worker lease expires
- error_message: latest failure reason

## Worker Lifecycle

1. Worker claims a pending job.
2. Job moves to running.
3. Worker receives a lease.
4. Worker processes one batch.
5. Worker updates progress.
6. Job returns to pending if more history remains.
7. Job becomes completed when no older messages remain.

## Fault Tolerance

If a worker crashes, the job lease expires.

A future worker can release stale jobs and retry them.

## Operational Rule

Backfill jobs must never block live ingestion.
