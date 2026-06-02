# Operational Observability

## Purpose

RUMMAN must evolve with strong operational visibility.

The system should expose enough operational state to understand:

- ingestion health
- queue health
- worker health
- synchronization progress
- backfill progress
- failure patterns
- throughput
- operational bottlenecks

## Core Metrics

### Live Ingestion

- live_messages_received
- live_messages_inserted
- live_duplicates_detected
- live_insert_failures
- live_ingestion_latency

### Backfill

- backfill_jobs_pending
- backfill_jobs_running
- backfill_jobs_completed
- backfill_jobs_failed
- backfill_messages_processed
- backfill_duplicates_detected
- backfill_retry_count
- backfill_batch_duration

### Workers

- active_workers
- stale_workers_detected
- worker_heartbeat_age
- lease_recovery_events

### Synchronization

- synced_chats
- unsynced_chats
- checkpoint_lag
- oldest_pending_message
- newest_synced_message

## Current Observability Implementation

All workers emit single-line structured log events to Railway stdout:

```
JOB_CLAIMED | id=abc123 | job_type=audio_transcribe | chat=-100123456
BATCH_PROCESSED | count=42 | chat=-100123456 | oldest_id=1234
EMBED_OK | chunk_id=xyz | course_code=IT362 | tokens=512
SYNTH_OK | session_id=abc | tokens_in=1024 | tokens_out=156 | ms=1200
```

These can be grepped in Railway logs. No structured log shipping is configured yet.

## Key Queries for Current State

```sql
-- Processing queue health
SELECT job_type, status, COUNT(*) FROM processing_jobs
WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
GROUP BY job_type, status ORDER BY job_type, status;

-- Knowledge coverage: chunks per source type
SELECT source_type, source_authority, COUNT(*) FROM document_chunks
WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
GROUP BY source_type, source_authority;

-- Search quality: zero-result rate (query_logs was dropped in migration 016; use learning_events)
SELECT event_type, COUNT(*) FROM learning_events
WHERE event_type IN ('query', 'zero_result', 'synthesis')
AND tenant_id = '00000000-0000-0000-0000-000000000001'
GROUP BY event_type;

-- Backfill status
SELECT chat_id, status, total_processed, retry_count FROM telegram_backfill_jobs
WHERE tenant_id = '00000000-0000-0000-0000-000000000001' ORDER BY status;
```

## Future Direction

- Automated bot error rate alerting on worker heartbeat gaps
- Weekly knowledge gap report from `learning_events` (zero_result rate by course_code)
- Dashboard over `learning_events` + `ai_runs` + `processing_jobs`
- Cost observability: `SELECT SUM(cost_usd) FROM ai_runs WHERE DATE(created_at) = CURRENT_DATE`
