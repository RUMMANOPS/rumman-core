# Ingestion Architecture

## Purpose

Ingestion is the process of receiving source platform signals, normalizing them, and routing them into the Data Spine. Ingestion does not extract knowledge and does not produce intelligence. It gets data in safely and correctly.

---

## Two-Speed Ingestion Model

RUMMAN uses a permanently separated two-speed ingestion architecture (ADR-0002):

### Live Ingestion (fast path)
- Receives events in real time from source platforms
- Normalizes and inserts immediately
- Updates sync state checkpoints
- Must never perform historical crawling
- Must be lightweight, supervised, and resilient to restart

### Historical Backfill (slow path)
- Processes old messages in controlled, bounded batches
- Uses lease-based job queue (telegram_backfill_jobs)
- Supports resumability, retry, and stale-lease recovery
- Operates independently from the live listener
- Triggered on demand, not continuously

---

## Current Ingestion Flow (Telegram)

```
Telegram MTProto API
        │
   Telethon client (StringSession)
        │
   events.NewMessage handler
        │
   build_payload() → normalized message dict
        │
   POST /rest/v1/messages (PostgREST)
   ├─ 201 → inserted → update telegram_sync_state
   ├─ 409 → duplicate → skip
   └─ 4xx → log error → discard (no retry)
```

Current gap: failed inserts are logged and discarded with no dead-letter path. At scale, a consistently failing message silently disappears.

Current gap: listener restart loses messages received during downtime. No catch-up mechanism exists (by design per ADR-0002, but the gap is unaddressed).

---

## Future Ingestion Flow (Multimodal)

As new source modalities are added, ingestion converges on a platform-agnostic pattern:

```
Source Platform (Telegram / email / WhatsApp / file upload / ...)
        │
   Source Connector (one per platform)
        │
   ingestion_event record (normalized, platform-agnostic)
        │
   ┌────┴────────────────────────────────────┐
   │ Has binary content?                     │
   │                                         │
   ├─ No  → message_text field sufficient    │
   │        → downstream: intelligence jobs  │
   │                                         │
   └─ Yes → upload binary to Supabase Storage│
            → raw_artifact record            │
            → enqueue extraction_job         │
              (one job per processing stage) │
```

The key principle: ingestion responsibility ends at raw_artifact creation and extraction_job enqueueing. It does not perform extraction itself.

---

## Source Connector Model (future)

Each source platform has one connector. A connector is responsible for:
- Authenticating to the platform
- Receiving or polling for new signals
- Normalizing to the ingestion_event schema
- Uploading binaries to Supabase Storage at the correct tenant-scoped path
- Enqueueing extraction jobs

A connector must not:
- Perform extraction (OCR, transcription, etc.)
- Call the intelligence pipeline
- Manage tenant routing (that belongs to the source registry)

The Telegram user client (rumman_engine.py) is the first connector implementation. It currently mixes connector and inline processing concerns (e.g., file_meta() is an extraction step). These should be separated as Layer 2 is built.

---

## Listener Resilience Requirements

The live listener must be engineered for long-running stability:

- Telethon FloodWaitError: must be caught and respected (exponential backoff)
- Session expiry: must detect and surface without silently reconnecting with stale state
- Restart message loss: mitigated by enqueueing a bounded catch-up backfill job on startup for each chat where newest_message_id < current Telegram state (this respects ADR-0002 by delegating to the backfill system)
- Heartbeat: listener must write a heartbeat row periodically so external monitoring can detect silent disconnection
- httpx connection reuse: module-level AsyncClient, not per-message instantiation

---

## Backfill Worker Safety Model

The backfill worker is designed for safety, not speed:

- Claims exactly one job per run
- Holds a time-bounded lease (heartbeat renewal prevents expiry on slow batches)
- Releases stale leases from crashed workers before claiming a new job
- Processes one batch (default 500 messages) then yields — job returns to pending if history remains
- Uses BATCH_SLEEP_SECONDS between batches to avoid Telegram rate limits
- Must never run inside the live listener process

Deployment note: the backfill worker's exit-after-one-job behavior conflicts with its Procfile entry. Railway restarts it up to 10 times then stops. This should be resolved by moving backfill to an on-demand invocation model (Railway one-off or n8n trigger) rather than a continuously managed process.

---

## Ingestion and Tenant Isolation

Every ingestion_event and every raw_artifact must carry tenant_id. Ingestion routing — knowing which tenant owns which source — requires a sources table and tenant_sources mapping that does not yet exist.

Until this exists, all data implicitly belongs to the single founder-level tenant. This is the Phase 1 reality. It becomes a migration when tenant_id is added.

The choice of Telegram identity model (user client per tenant vs. shared Bot API) has significant implications for ingestion architecture. This decision must be made before building the sources/tenant_sources tables. See ADR-0005 (identity model decision pending).
