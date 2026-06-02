# ADR-0005: Three-Layer Platform Architecture

## Status

Accepted — **Partially Superseded by Phase 2 completion (2026-06-01)**

The three-layer model in this ADR remains the correct architectural direction. Two specific status claims are no longer accurate:
1. "intelligence_worker.py is deliberately excluded from the Procfile" — it IS in the Procfile, gated by INTELLIGENCE_WORKER_ENABLED=true, and enabled in Railway.
2. Layer 2 "Does not yet exist" — Layer 2 is functionally operational via embed_worker.py, telegram_download_worker.py, and attribution_worker.py.

The formal knowledge_artifact / knowledge_chunks schema described in Layer 2 remains the target architecture. The current production implementation uses source_documents + document_chunks as the pragmatic equivalent.

See ADR-0008 for the Telegram three-account session architecture decision.
See `docs/07-knowledge-layer/knowledge-layer-overview.md` for current Layer 2 status.

## Context

RUMMAN is evolving beyond Telegram message ingestion toward a multimodal Operational Knowledge Intelligence Platform. Future ingestion modalities include audio, images, PDFs, spreadsheets, presentations, contracts, scanned documents, university materials, schedules, and reports.

As the platform scope expanded, three distinct architectural concerns emerged that were being conflated in the current codebase:

1. Getting data in safely and durably
2. Transforming raw data into structured knowledge
3. Reasoning over structured knowledge to produce intelligence

These concerns have different performance characteristics, different failure modes, different scaling pressures, and different tenant isolation requirements. Mixing them produces the same failure modes that ADR-0002 resolved for live ingestion vs. backfill: instability, blocked pipelines, and loss of operational clarity.

A single-layer or two-layer architecture cannot cleanly accommodate multimodal processing, replayable extraction, or multi-tenant intelligence at scale.

## Decision

RUMMAN is built across three explicitly separated architectural layers. Each layer has defined responsibilities, canonical entities, and clear interfaces to adjacent layers.

---

### Layer 1 — Data Spine

**Responsibility:** Receive, store, and coordinate all raw operational data.

**Canonical entities:** sources, ingestion_events, messages, raw_artifacts, telegram_sync_state, telegram_backfill_jobs, extraction_jobs, tenants.

**What it does:**
- Accepts inbound signals from source platforms (Telegram, email, WhatsApp, files)
- Normalizes signals into platform-agnostic ingestion_events
- Stores raw binary artifacts in Supabase Storage (immutable, tenant-scoped paths)
- Maintains synchronization checkpoints per source per chat per tenant
- Coordinates extraction work through a lease-based job queue
- Enforces tenant isolation at the row level

**What it must not do:**
- Perform knowledge extraction (OCR, transcription, NLP)
- Call LLMs for content analysis
- Produce intelligence outputs (tasks, decisions, entities)

**Current status:** Mostly implemented. Missing: tenant_id on tables, raw_artifacts table, sources table, listener heartbeat, schema-as-code.

---

### Layer 2 — Knowledge Layer

**Responsibility:** Transform raw artifacts and ingestion events into structured, queryable knowledge.

**Canonical entities:** knowledge_artifacts, knowledge_chunks, embeddings, entities, entity_relationships.

**What it does:**
- Runs extraction pipelines per artifact type (transcription, OCR, document parsing)
- Chunks extracted content into semantically coherent segments
- Generates and stores embeddings for semantic retrieval
- Extracts named entities and populates the entity graph
- Produces knowledge_artifacts that are the Layer 3 input

**What it must not do:**
- Write operational items (tasks, decisions, deadlines) — that is Layer 3
- Access source platforms directly — it reads from raw_artifacts in Storage
- Bypass the extraction job queue — all processing goes through jobs

**Current status (Phase 2 — 2026-06-01):** Functionally operational. `telegram_download_worker.py` (extraction), `embed_worker.py` (chunking + embedding into `document_chunks`), `attribution_worker.py` (course tagging) collectively perform Layer 2 responsibilities. The formal `knowledge_artifact`/`knowledge_chunks` entity model is the target architecture; `source_documents`+`document_chunks` is the current production equivalent.

**Gate condition:** Layer 2 must be operational before Layer 3 is enabled. This condition was satisfied before activating `intelligence_worker`.

---

### Layer 3 — Intelligence Layer

**Responsibility:** Reason over structured knowledge to produce operational intelligence.

**Canonical entities:** ai_runs, tasks, deadlines, decisions, memories, insights, intelligence_items.

**What it does:**
- Classifies and routes knowledge artifacts to intelligence processors
- Extracts operational items (tasks, decisions, deadlines) from knowledge
- Synthesizes operational memory across sources and tenants
- Runs agents and copilots over the knowledge graph
- Logs every AI operation in ai_runs with full traceability

**What it must not do:**
- Read from raw_artifacts or ingestion_events directly — it reads from Layer 2 outputs
- Run without a corresponding ai_runs record — all AI outputs must be traceable
- Run without per-tenant cost controls in place
- Run without a dead-letter path for failed extractions

**Current status (Phase 2 — 2026-06-01):** intelligence_worker.py IS in the Procfile, gated by INTELLIGENCE_WORKER_ENABLED=true. Enabled in Railway since Phase 2 completion. ai_runs table, 3K/day cost cap, and cursor-based dedup are in place. Gate conditions from this ADR were satisfied before activation.

---

## Interface Between Layers

Layer 1 → Layer 2: extraction_jobs queue (one job per artifact per processing stage). Layer 2 workers consume jobs; Layer 1 workers produce them.

Layer 2 → Layer 3: intelligence_jobs queue (one job per knowledge_artifact per intelligence pass). Layer 3 workers consume jobs; Layer 2 workers produce them after extraction is complete.

Both interfaces use the same lease-based job queue pattern. No direct function calls between layers. No shared in-process state.

---

## Consequences

### Positive

- Extraction is replayable: improve OCR model → re-run Layer 2 → Layer 3 automatically reflects improved extractions
- Tenant isolation is enforced at each layer boundary independently
- Each layer can scale independently
- Failure in Layer 3 does not affect Layer 1 ingestion
- Intelligence quality is decoupled from ingestion reliability
- Clear gate conditions prevent premature intelligence activation

### Negative

- Three job queues and three worker types to operate
- More tables and more schema to maintain
- Layer 2 must be built before Layer 3 can progress, adding time before intelligence value
- Debugging requires understanding which layer a failure occurred in

## Explicitly Avoided Approaches

**LangChain / CrewAI / agent frameworks:** These frameworks own your control flow. RUMMAN's pipeline is DB-driven — all job coordination, state, and retries live in Postgres. Routing control through an external framework breaks observability and replay. The same pipeline is achievable with asyncio + job tables + handler classes.

**External vector databases (Pinecone, Weaviate, Qdrant):** pgvector on Supabase handles millions of chunks with adequate ANN query performance when filtered by tenant_id. Introducing a dedicated vector DB adds operational complexity, egress cost, and another dependency before the bottleneck is proven. Migrate to a dedicated vector service only when pgvector is the measured bottleneck.

**Microservices per layer:** Each layer is an async Python process (or set of processes). Service mesh, gRPC, or HTTP between layers adds latency and operational overhead for no gain at current scale. Postgres is the inter-layer interface.
