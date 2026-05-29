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

-- Search quality: zero-result rate
SELECT detected_intent, COUNT(*) FILTER (WHERE result_count = 0) as no_results,
       COUNT(*) as total, ROUND(AVG(response_time_ms)) as avg_ms
FROM query_logs GROUP BY detected_intent;

-- Backfill status
SELECT chat_id, status, total_processed, retry_count FROM telegram_backfill_jobs
WHERE tenant_id = '00000000-0000-0000-0000-000000000001' ORDER BY status;
```

## Future Direction

- n8n WF-003: automated bot error rate alerting
- n8n WF-005: weekly knowledge gap report from query_logs
- Dashboard over query_logs + feedback + processing_jobs
- Cost observability: OpenAI spend per model per day
