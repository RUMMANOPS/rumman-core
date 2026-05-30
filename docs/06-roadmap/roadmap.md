# RUMMAN Roadmap

## Phase 0: Foundation

Status: **Complete**

- Railway deployment, Telegram user client session, Supabase message storage
- Live listener (`rumman_engine.py`) with `ENABLE_BACKFILL=False` guard
- `telegram_sync_state` checkpoints, `telegram_backfill_jobs` queue
- Backfill worker with lease lifecycle
- GitHub docs structure and ADR framework

---

## Phase 1: Data Spine + Search Layer

Status: **Complete** *(evolved significantly beyond original scope)*

The original Phase 1 goal was ingestion stabilization. The system evolved further:

**Ingestion (original goals — complete):**
- Stable live ingestion with deduplication
- Resumable backfill with lease + heartbeat
- Audio transcription pipeline (`audio_worker.py` → `telegram_download_worker.py`)
- Media download and OCR pipeline (`telegram_download_worker.py`)
- Embedding pipeline (`embed_worker.py` → `document_chunks`)

**Search and retrieval (added during Phase 1):**
- Query understanding pipeline: normalization → intent hints → GPT-4o-mini classification → search routing (`query_understanding.py`)
- Vector search via pgvector with dual Arabic/English queries (`search_api.py`)
- Answer synthesis with grounding (`/synthesize` endpoint)
- Student-facing Telegram bot with session management and feedback (`telegram_bot.py`)

**Institutional knowledge layer (added during Phase 1):**
- SEU curriculum structure: colleges → specializations → courses (`seu_*` tables, migrations 008–009)
- University knowledge repository: 93 official SEU documents organized across 6 domains
- `scripts/ingest_document.py` — CLI to push official documents through the pipeline
- `scripts/seed_courses.py` — seeds structured course data (names, descriptions, prerequisites)

**Current state:** 5 colleges, 21 specializations, 161 courses seeded in `inst_courses` (renamed from `seu_*` tables for multi-tenancy). Official document ingestion pipeline (`batch_ingest_seu.py`) exists but has not yet been bulk-run on the repository (93 files pending).

---

## Phase 2: Institutional Intelligence

Status: **In Progress** *(core infrastructure live; official docs not yet ingested)*

The transition from search-over-community-content to grounded institutional + community intelligence.

### Already Completed (as of 2026-05-30)

- **Structured query path** — `search_api.py` queries `inst_courses` directly for course codes detected in queries, bypassing vector search (exact match, similarity=0.95)
- **Academic calendar layer** — `search_api.py` injects calendar events for `exam_schedule`/`deadline` intents
- **Intelligence layer** — `active_extracted_items` view feeds synthesis for course-specific and temporal queries
- **Course data** — 161 courses seeded in `inst_courses` (renamed from `seu_*` for multi-tenancy)
- **Claim model** — `valid_from/valid_until/superseded_by` on `document_chunks` and `extracted_items` (migration 025)
- **Gap analyst** — `scripts/gap_analyst.py` clusters zero-result events into actionable gaps
- **QA mining** — `app/qa_mining_worker.py` extracts implicit Q&A from 72K Telegram messages

### Remaining

- **Official document corpus** — 93 files in university repository not yet bulk-ingested (`batch_ingest_seu.py` ready)
- **Intelligence worker** — `intelligence_worker.py` in Procfile but gated behind `INTELLIGENCE_WORKER_ENABLED=true`
- **College tagging** — Telegram `chat_id` → college mapping exists in `inst_colleges.telegram_chat_ids`; wiring complete in `rumman_engine.py` but mapping not yet populated in DB

---

## Phase 3: Multi-University Expansion

Status: **Planned**

### Goals

- Second university onboarded using the same institutional + community framework
- Rename `seu_*` tables to `inst_*` (tenant_id handles university scoping)
- University knowledge repository formalized: repeatable onboarding process documented
- Per-university bot deployment or routing layer

### Deliverables

- `inst_colleges`, `inst_specializations`, `inst_courses` schema migration
- Second university knowledge repository (same folder contract as SEU)
- Onboarding runbook for new institutions
- Bot routing layer or per-tenant deployment

---

## Phase 4: Intelligence Layer

Status: **Planned**

- Proactive intelligence: deadline detection, exam date extraction, assignment announcements
- Student context: personalization based on declared program and level
- Daily/weekly brief generation from Telegram streams
- Operational dashboard for knowledge coverage and system health

---

## Phase 5: Platform Layer

Status: **Future**

- Multi-channel ingestion (WhatsApp, email, files)
- B2B analytics layer (institutional view of course activity, student confusion patterns)
- External API for third-party integrations
