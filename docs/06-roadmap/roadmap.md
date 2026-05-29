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

**Current state:** 5 colleges, 21 specializations, 157 courses fully mapped. Official document ingestion pipeline exists but has not yet been run on the repository (93 files pending).

---

## Phase 2: Institutional Intelligence

Status: **Next**

The transition from search-over-community-content to grounded institutional + community intelligence.

### Goals

- Connect the institutional layer to retrieval: structured queries for course facts (credits, prerequisites, program requirements) bypass vector search and query `seu_*` tables directly
- Populate `seu_courses.name_ar/name_en` from `scripts/data/seu_courses.json` (82 courses ready)
- Ingest official university documents (93 files) through the pipeline with correct source metadata
- Add `source_authority` to `document_chunks` to distinguish official institutional content from community uploads
- Wire Telegram `chat_id` → college tagging at ingestion time (mapping already in `seu_colleges.telegram_chat_ids`)
- Enable `intelligence_worker.py` for entity/task/deadline extraction (requires ADR update)

### Deliverables

- Structured query path in search API for curriculum facts
- Official document corpus in `document_chunks` with authority metadata
- College-tagged community chunks (free precision improvement)
- Intelligence extraction running on a subset of Telegram streams
- Course coverage analytics (which courses are well-covered vs. gaps)

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
