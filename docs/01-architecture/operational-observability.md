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

## Future Direction

RUMMAN should eventually expose operational dashboards and real-time observability tooling.

Potential future integrations:

- Grafana
- Prometheus
- OpenTelemetry
- Supabase analytics
- Custom operational dashboards
