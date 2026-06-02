# Current Architecture

*Last updated: 2026-06-02 — Phase 2 complete. See RUMMAN_MASTER_DOSSIER.md for complete system detail.*

## Overview

RUMMAN runs as **eight independent processes** on Railway. All share Supabase as the coordination and data plane. Processes communicate exclusively via Postgres tables — no inter-process HTTP calls, no message brokers.

n8n is **not yet deployed**. It is planned for orchestration in a future phase. See `docs/03-workflows/n8n-workflows.md`.

## Runtime Processes (Current Procfile)

```
listener:     python3 app/rumman_engine.py
backfill:     python3 app/telegram_backfill_worker.py
media:        python3 app/telegram_download_worker.py
embed:        python3 app/embed_worker.py
search:       uvicorn app.search_api:app --host 0.0.0.0 --port ${PORT:-8000}
bot:          python3 app/telegram_bot.py
intelligence: python3 app/intelligence_worker.py          [gated: INTELLIGENCE_WORKER_ENABLED=true]
attribution:  python3 app/attribution_worker.py           [gated: ATTRIBUTION_WORKER_ENABLED=true]
```

See `docs/03-workflows/railway-processes.md` for full specifications of each process.

## Three-Layer Architecture Status

RUMMAN is built across three explicitly separated architectural layers (ADR-0005).

### Layer 1 — Data Spine (Operational)
Ingestion, synchronization, raw artifact storage, job queues, lease coordination, operational state, tenant management.

Workers: `listener`, `backfill`, `media` (ingestion side)

### Layer 2 — Knowledge Layer (Operational)
Extraction pipelines (OCR, transcription, document parsing), semantic chunking, embedding generation, course attribution.

Workers: `media` (extraction side), `embed`, `attribution`

Layer 2 is functionally operational as of Phase 2. The formal `knowledge_artifacts` / `knowledge_chunks` schema described in ADR-0007 is the target architecture; the current production implementation uses `source_documents` + `document_chunks` as the pragmatic equivalent.

### Layer 3 — Intelligence Layer (Gated, Active)
Operational item extraction, intelligence synthesis, student context, search and synthesis.

Workers: `intelligence` (gated by INTELLIGENCE_WORKER_ENABLED), `search`, `bot`

The intelligence worker is in the Procfile and enabled in Railway (`INTELLIGENCE_WORKER_ENABLED=true`) as of Phase 2. The gate condition — stable Layer 2 extraction — was satisfied before enabling.

## Design Invariants

**Live ingestion and backfill are permanently separated.** (ADR-0002) The listener never calls `iter_messages`. The backfill worker never handles live messages.

**Layer boundary rule.** Layer 3 workers do not read raw messages or raw_artifacts directly. They read from Layer 2 outputs (document_chunks, intelligence_items, extracted_items). This makes the platform replayable, auditable, and multi-tenant safe.

**Postgres as coordination plane.** Job queues, worker state, cursor positions, lease acquisition, heartbeats — everything in Postgres. The system is observable and debuggable from Postgres alone.

## What Supabase Is For

Supabase is the operational data spine. See `docs/04-database/supabase-schema.md` for the full table inventory.

Key concern areas:
- All Layer 1 ingestion state (messages, telegram_sync_state, telegram_backfill_jobs, processing_jobs)
- All Layer 2 knowledge objects (source_documents, document_chunks with pgvector)
- All Layer 3 intelligence (intelligence_items, extracted_items, message_signals, ai_runs)
- Platform identity (tenants, rumman_users, rumman_sessions, student_context)
- Institutional layer (inst_colleges, inst_specializations, inst_courses)
- Observability (learning_events, worker_heartbeats, analysis_runs, gap_items)
