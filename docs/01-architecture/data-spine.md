# Data Spine Architecture

## Purpose

The Data Spine is Layer 1 of the RUMMAN platform. It is responsible for receiving, storing, tracking, and coordinating all operational data before any intelligence processing begins.

The Data Spine makes no intelligence decisions. It ingests, stores, and coordinates. Everything above it depends on it being correct, stable, and observable.

---

## Responsibilities

- Receive messages and signals from all source platforms (Telegram today; email, WhatsApp, files in future)
- Store raw source events in a normalized, platform-agnostic form
- Store raw binary artifacts (audio, images, PDFs, documents) in object storage
- Track synchronization state per source per chat per tenant
- Coordinate historical backfill through a controlled, lease-based job queue
- Coordinate extraction jobs for Layer 2 processing
- Provide tenant-scoped access boundaries on all operational data
- Emit observable state for operational monitoring

---

## Core Tables

### sources
One row per connected platform identity per tenant. Tracks which Telegram account, bot, email address, or other connector belongs to which tenant.

Not yet implemented — required before multi-tenant support.

### ingestion_events
Normalized inbound signals from any source platform. Platform-specific fields (telegram_chat_type, etc.) are stored alongside normalized fields (platform, platform_chat_id, platform_message_id).

Currently this role is served by the `messages` table. Future: `messages` becomes the Telegram-specific view; `ingestion_events` is the platform-agnostic canonical.

### messages
Current canonical message store. Normalized Telegram messages.
Unique on (platform_chat_id, platform_message_id). 409 = duplicate — safe to retry.
Contains raw_json for full message replay.

### raw_artifacts
One row per binary file associated with an ingestion event (audio, image, PDF, video, document).
Stores metadata only — the binary is in Supabase Storage at path `{tenant_id}/{source_id}/{artifact_id}.{ext}`.
Status: pending → uploaded → extraction_queued → processed.

Not yet implemented — currently audio is handled inline in audio_worker.py.

### telegram_sync_state
One row per (tenant, chat). Tracks live ingestion progress.
Updated on every successful live message insert.
Fields: newest_message_id, oldest_message_id, total_messages_seen, backfill_completed.

### telegram_backfill_jobs
Controlled historical ingestion work queue. Lease-based, resumable, worker-safe.
Status: pending → running → pending (if more history) → completed / failed.
Includes heartbeat and stale-lease recovery.

### extraction_jobs (future unification of processing_jobs)
Unified job queue for all Layer 2 extraction work.
Replaces current processing_jobs with consistent lease/heartbeat pattern.
job_type: audio_transcribe, image_ocr, pdf_extract, video_transcribe, chunk_embed, entity_extract.

### tenants (not yet implemented)
Required before multi-tenant support can be enforced.
Fields: id, name, plan, status, created_at.

---

## Storage Model

Postgres (Supabase):
- All metadata, job state, sync state, entities, operational items
- Structured data only — no binary blobs

Supabase Storage:
- All raw binary artifacts (audio, images, PDFs, documents)
- Path structure: `{tenant_id}/{source_id}/{artifact_id}.{ext}`
- Immutable once written — never modified after upload

pgvector (Supabase extension — Layer 2):
- knowledge_chunks + embeddings
- Always filtered by tenant_id — never cross-tenant queries

---

## Key Design Principles

**Immutability of raw artifacts.** Once a binary file is uploaded to Supabase Storage, it is never modified. Extraction may be re-run; the source file is the audit record.

**Deduplication at the DB layer.** The messages table unique constraint makes insert idempotency a database guarantee, not an application concern. Workers treat 409 as "already done."

**Lease coordination via Postgres.** The backfill job lifecycle uses optimistic conditional PATCH to acquire leases. No external lock service needed. Workers verify ownership after claiming.

**No binary in Postgres.** Raw audio, PDFs, images are never stored in Postgres columns. Large extracted text (OCR of a 200-page document) should also use Supabase Storage, not a text column.

---

## Current Gaps (Phase 1 hardening targets)

- Schema is not in version control. Must be addressed before any Layer 2 work begins.
- tenant_id absent from all tables. Must be added before multi-tenant ingestion.
- raw_artifacts table does not exist. Audio is downloaded inline and discarded — originals are lost.
- processing_jobs lacks the lease/heartbeat pattern that telegram_backfill_jobs has.
- listener has no heartbeat mechanism. Cannot detect silent disconnection.
- No FloodWaitError handling in listener or backfill worker.
- sources table does not exist. Multi-tenant source routing is undefined.
