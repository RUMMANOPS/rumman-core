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

Status: **Complete** *(as of 2026-06-01)*

The transition from search-over-community-content to grounded institutional + community intelligence.

### Completed

- **Structured query path** — `search_api.py` queries `inst_courses` directly for course codes detected in queries, bypassing vector search (exact match, similarity=0.95)
- **Academic calendar layer** — `search_api.py` injects calendar events for `exam_schedule`/`deadline` intents
- **Intelligence layer** — `active_extracted_items` view feeds synthesis for course-specific and temporal queries
- **Course data** — 161 courses seeded in `inst_courses`; name_ar populated for all courses including MGT425 and FIN416
- **Claim model** — `valid_from/valid_until/superseded_by` on `document_chunks` and `extracted_items` (migration 025)
- **Gap analyst** — `scripts/gap_analyst.py` clusters zero-result events into actionable gaps
- **QA mining** — `app/qa_mining_worker.py` extracts implicit Q&A from 72K Telegram messages
- **Official document corpus** — 153 files bulk-ingested (all regulations, study plans, course syllabi, diplomas). Confirmed complete 2026-06-01.
- **Intelligence worker** — LIVE with `INTELLIGENCE_WORKER_ENABLED=true`; processes new messages continuously
- **Attribution worker** — LIVE with `ATTRIBUTION_WORKER_ENABLED=true`; budget 8M tokens/run
- **College tagging** — `inst_colleges.telegram_chat_ids` populated for all 5 colleges; wired in `rumman_engine.py`
- **Session architecture** — Three dedicated Telegram accounts: غيث (listener), راوي (backfill), إبراهيم (media)
- **Message signals** — 1,000+ signals extracted (exam_emphasis, difficulty, professor_note, resource_rec, confusion_cluster)
- **Corpus** — 120K+ document_chunks, all embedded; 263 exam_intelligence records; 338 course_intelligence_profiles

### Open Items (not blocking Phase 3)

- MGT425 and FIN416 still exam-heavy — official PDF materials needed to close content gaps fully
- 41 public SEU Telegram groups — راوي/إبراهيم need to join manually (20-30/day limit); 18 groups need admin access
- ~21K document_chunks with null course_code — attribution_worker draining continuously

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
