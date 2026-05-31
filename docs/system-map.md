# RUMMAN System Map
**Last updated:** 2026-05-31 | **Phase:** 2 (In Progress)

This is the single reference document for RUMMAN's current state. It is a snapshot, not a living architecture doc — update it when the system changes materially.

---

## 1. Architecture Tree

```
RUMMAN Operational Intelligence OS
│
├── INGESTION LAYER  (raw signal → structured corpus)
│   ├── listener           app/rumman_engine.py
│   │   └─ Live NewMessage handler → messages + telegram_sync_state
│   │      Gap detection on arrival (threshold=10) → ensures gap_fill jobs
│   │
│   ├── backfill           app/telegram_backfill_worker.py
│   │   └─ Claims telegram_backfill_jobs (lease + heartbeat)
│   │      Priority: gap_fill jobs first → then full backfill
│   │      Completion hook: writes backfill_completed + coverage_verified_at
│   │
│   └── media              app/telegram_download_worker.py
│       └─ Unified audio_transcribe + telegram_media handler
│          Resolve entity → download → store → mark processed
│
├── PROCESSING LAYER  (corpus → indexed intelligence)
│   ├── embed              app/embed_worker.py
│   │   └─ Polls embed_chunk jobs → OpenAI embeddings → document_chunks
│   │
│   ├── intelligence       app/intelligence_worker.py  [GATED]
│   │   └─ Extracts assignments/deadlines → intelligence_items
│   │
│   └── attribution        app/attribution_worker.py   [GATED]
│       └─ AI-assisted course attribution for untagged chunks
│
├── SEARCH LAYER  (indexed intelligence → grounded answers)
│   └── search             app/search_api.py
│       └─ FastAPI: intent detection → pgvector retrieval → synthesis
│          Endpoints: /synthesize, /v1/courses/inventory
│
├── INTERFACE LAYER  (grounded answers → student)
│   └── bot                app/telegram_bot.py
│       └─ Long-polls Telegram → routes: planning / academic / general
│          Calls /synthesize, /v1/courses/inventory
│
├── INSTITUTIONAL LAYER  (ground truth)
│   └── Supabase: inst_colleges, inst_specializations, inst_courses
│      161 courses | 5 colleges | seeds from scripts/data/seu_courses.json
│
└── KNOWLEDGE REPOSITORY  (official documents)
    └── .../0-Universities/1- Saudi Electronic University/
        StudyPlans/ | Regulations/ | AcademicCalendar/ | CourseContent/
        Ingested via: scripts/ingest_document.py + scripts/batch_ingest_seu.py
```

---

## 2. Service Table

| Service | Process File | Role | Session | Status | Notes |
|---|---|---|---|---|---|
| **listener** | `app/rumman_engine.py` | Live Telegram NewMessage → `messages` + `telegram_sync_state`. Gap detection on every message. Startup gap scan. | `TELEGRAM_SESSION_STRING` (Personal #1) | **LIVE** | Never crawls history. `ENABLE_BACKFILL=False` guard. |
| **backfill** | `app/telegram_backfill_worker.py` | Claims `telegram_backfill_jobs` with lease+heartbeat. Gap-fill jobs processed first. Writes coverage on completion. | `TELEGRAM_BACKFILL_SESSION_STRING` (Personal #2) | **LIVE — SESSION BUG** | All 3 Telegram services currently share the same session string. Time bomb. |
| **media** | `app/telegram_download_worker.py` | Unified audio+media handler. Downloads files, extracts text, transcribes audio via Whisper. | `TELEGRAM_WORKER_SESSION_STRING` (RUMMAN/غيث) | **LIVE — SESSION BUG** | 18,355 pending telegram_media jobs. Null byte bug in PDF text causes silent failures. |
| **embed** | `app/embed_worker.py` | Polls `embed_chunk` jobs → OpenAI text-embedding-3-small → `document_chunks`. | — | **LIVE** | 212 embed_chunk jobs pending. Heartbeat active. |
| **search** | `app/search_api.py` | FastAPI search: intent → pgvector → synthesis. Serves bot + external callers. | — | **LIVE** | Port `$PORT`. SYNTHESIZE_ERROR 500s observed 01:48–02:05 UTC (root cause unknown). |
| **bot** | `app/telegram_bot.py` | Student-facing bot. Routes: planning → academic → general. Calls /synthesize. | `TELEGRAM_BOT_TOKEN` | **LIVE** | Routing: planning detection → inventory-first → synthesis. |
| **intelligence** | `app/intelligence_worker.py` | Extract assignments/deadlines from messages → `intelligence_items`. | — | **GATED** | Requires `INTELLIGENCE_WORKER_ENABLED=true`. Zero items currently. |
| **attribution** | `app/attribution_worker.py` | AI course attribution for untagged chunks → `machine_asserted`. | — | **GATED** | Requires `ATTRIBUTION_WORKER_ENABLED=true`. Heartbeat shows idle. |

**Off-Procfile workers (run locally on demand):**

| File | Purpose | When to run |
|---|---|---|
| `scripts/ingest_document.py` | Ingest a single official document | When adding SEU knowledge repo files |
| `scripts/batch_ingest_seu.py` | Bulk-ingest all 93 SEU docs | Next planned batch run |
| `app/pdf_worker.py` | Extract text from PDF source_documents | On demand during ingestion |
| `app/qa_mining_worker.py` | Extract Q&A pairs from 72K messages | On demand, requires migrations 026+027 |
| `scripts/gap_analyst.py` | Cluster zero-result events → gap_items | Weekly or after new query volume |
| `scripts/weekly_report.py` | Ops + product health → Telegram ops channel | Monday 08:00 AST cron |

---

## 3. Data Flows

### Flow 1: Live Message Ingestion
```
Telegram group message
  → listener (rumman_engine.py)
  → INSERT messages (platform_chat_id + platform_message_id unique constraint)
  → UPSERT telegram_sync_state (newest_message_id, last_live_seen_at)
  │    ↓ if new_id > known_newest + 10 (GAP_THRESHOLD)
  │    → INSERT processing_jobs (telegram_gap_fill, target_key=gap:<chat>:<min>:<max>)
  → if has_media: INSERT processing_jobs (telegram_media)
  → if has_audio: INSERT processing_jobs (audio_transcribe)
```

### Flow 2: Historical Backfill
```
telegram_backfill_jobs row (status=pending)
  → backfill worker claims with lease (status=running, worker_id, lease_expires_at)
  → client.iter_messages(offset_id=cursor, limit=100)
  → for each message: INSERT messages (409 dedup OK)
  → UPSERT telegram_sync_state (oldest_message_id, total_messages_seen)
  → heartbeat every 50 msgs (lease renewal)
  → on completion: backfill_completed=True, coverage_verified_at=NOW()
```

### Flow 3: Gap Fill
```
processing_jobs (job_type=telegram_gap_fill, status=pending)
  → backfill worker claims (PRIORITY over full backfill)
  → parses fill_min_id, fill_max_id from payload
  → client.iter_messages(min_id=fill_min-1, max_id=fill_max+1, limit=None)
  → INSERT each missing message
  → marks job completed/failed
```

### Flow 4: Media / Audio Processing
```
processing_jobs (job_type=telegram_media or audio_transcribe)
  → media worker claims
  → resolve_entity (bare channel ID via _bare_channel_id helper)
  → download file to temp
  → if PDF: extract text → INSERT source_documents → INSERT processing_jobs (embed_chunk)
  → if audio: Whisper transcription → INSERT media_files → UPDATE message
  → mark job completed
```

### Flow 5: Embedding
```
processing_jobs (job_type=embed_chunk)
  → embed worker claims
  → reads chunk text from document_chunks (pre-inserted stub)
  → OpenAI text-embedding-3-small
  → UPDATE document_chunks SET embedding=<vector>
  → mark job completed
```

### Flow 6: Academic Query (Student → Answer)
```
Telegram student message
  → bot (telegram_bot.py)
  → _detect_academic_signal() → if academic: POST /synthesize
    → search_api: _detect_intent() [gpt-4o-mini] → course codes + keywords
    → pgvector similarity search on document_chunks (top 15)
    → institutional lookup: inst_courses WHERE course_code IN (...)
    → if results: GPT-4o synthesis with grounded context
    → if no results: zero_result learning_event → "لم أجد محتوى"
  → bot formats + sends reply
  → INSERT learning_events (query, synthesis, or zero_result)
```

### Flow 7: Planning Query
```
Telegram message: "كيف أكمل بكالوريوس CS"
  → bot detects planning intent (_is_planning_query)
  → POST /v1/courses/inventory → inst_courses lookup by name/code
  → if course found: return structured course list
  → bot formats plan-style reply
```

---

## 4. Data Model (Key Tables)

| Table | Purpose | Key Columns | Notes |
|---|---|---|---|
| `messages` | Canonical raw messages | `platform_chat_id`, `platform_message_id` (unique together), `content`, `has_media`, `media_type` | 89,349 rows. Only insert path; never update content. |
| `telegram_sync_state` | Per-chat ingestion cursor | `platform_chat_id` (PK), `newest_message_id`, `oldest_message_id`, `backfill_completed`, `last_live_seen_at`, `coverage_verified_at` | One row per tracked chat. Coverage proof lives here. |
| `telegram_backfill_jobs` | Controlled historical crawl | `platform_chat_id`, `status`, `worker_id`, `lease_expires_at`, `oldest_processed_id`, `total_processed` | 18 completed, 69 pending, 1 running. |
| `processing_jobs` | Generic async work queue | `job_type`, `status`, `payload`, `target_key`, `retry_count` | 26,439 rows. Types: telegram_media, audio_transcribe, embed_chunk, telegram_gap_fill. |
| `source_documents` | Files awaiting/post-extraction | `storage_path`, `source_type`, `course_code`, `extraction_status` | 3,714 rows. Feeds embed pipeline. |
| `document_chunks` | Vector retrieval corpus | `chunk_text`, `embedding` (1536-dim), `course_code`, `source_type`, `superseded_by` | 94,637 rows, 100% embedded. Primary retrieval table. |
| `media_files` | Audio transcription results | `message_id`, `transcription`, `duration_seconds`, `model` | Created by Whisper transcription. |
| `learning_events` | Query telemetry | `event_type`, `query_raw`, `course_codes`, `latency_ms`, `metadata` (tokens, cost) | 51 rows (22 zero_result, 16 synthesis, 13 query). |
| `intelligence_items` | Extracted operational items | `item_type`, `title`, `due_date`, `course_code`, `source_message_id` | 0 rows — intelligence worker gated. |
| `inst_courses` | SEU course master data | `course_code` (PK), `name_ar`, `name_en`, `college_id`, `credit_hours`, `description` | 161 courses across 5 colleges. |
| `inst_colleges` | SEU colleges | `id`, `name_ar`, `name_en`, `code` | 5 colleges. |
| `analysis_runs` | Append-only analyst log | `analyst_type`, `config`, `summary`, `item_count` | Gap analyst + QA miner outputs. |
| `gap_items` | Normalised knowledge gaps | `cluster_label`, `example_queries`, `frequency`, `run_id` | Created by gap_analyst.py. |
| `worker_heartbeats` | Liveness tracking | `worker_id`, `worker_type`, `last_beat`, `status` | embed_worker + attribution_worker present. backfill + media absent. |

**Active views:**
- `active_extracted_items` — intelligence_items filtered by temporal validity, not rejected/superseded
- `active_document_chunks` — document_chunks filtered by superseded_by IS NULL

---

## 5. Production Status Dashboard

*Snapshot: 2026-05-31*

### Corpus
| Metric | Value | Health |
|---|---|---|
| Total messages ingested | 89,349 | ✅ Growing (backfill active) |
| Document chunks | 94,637 | ✅ |
| Chunks embedded (%) | 100% | ✅ |
| Unattributed chunks | ~TBD | ⚠️ check needed |
| Source documents | 3,714 | ✅ |
| Intelligence items | 0 | ❌ Worker gated |

### Chunk breakdown by source type
| Source | Chunks |
|---|---|
| exam | 54,573 |
| upload | 37,423 |
| telegram_export | 1,689 |
| study_plan | 525 |
| course_description | 308 |
| regulation | 129 |

### Pipeline
| Queue | Pending | Failed | Health |
|---|---|---|---|
| telegram_media | 18,355 | ~0 | ⚠️ Large queue, draining |
| embed_chunk | 212 | ~0 | ✅ Draining |
| audio_transcribe | ~1 stuck | — | ⚠️ 1 stuck since 2026-05-28 |
| telegram_gap_fill | 0 | 0 | ✅ Both filled |

### Backfill
| Status | Count | Messages |
|---|---|---|
| completed | 18 | 19,866 |
| running | 1 | 59,500+ (لمّاح \| SEU — active) |
| pending | 69 | — |

### Product (past 7 days)
| Metric | Value |
|---|---|
| Total queries | 51 learning_events |
| Synthesis calls | 16 |
| Zero-result rate | 43% (22/51) |
| Avg latency | — (low volume) |
| Est. OpenAI cost | < $0.01 |

### Service Health
| Service | Status | Last issue |
|---|---|---|
| listener | ✅ Running | — |
| backfill | ✅ Running | SESSION BUG (shared session) |
| media | ✅ Running | SESSION BUG + null byte bug |
| embed | ✅ Running | — |
| search | ✅ Running | SYNTHESIZE_ERROR 500 (01:48-02:05 UTC, unresolved) |
| bot | ✅ Running | — |
| intelligence | ⛔ Gated | INTELLIGENCE_WORKER_ENABLED not set |
| attribution | ⛔ Gated | ATTRIBUTION_WORKER_ENABLED not set |

---

## 6. What Changed (This Engineering Session)

### Self-Healing Ingestion Architecture — commit 9d9a958

**Migration 028** (`supabase/migrations/028_self_healing_ingestion.sql`):
- Added `last_live_seen_at TIMESTAMPTZ` to `telegram_sync_state` — written on every live message
- Added `coverage_verified_at TIMESTAMPTZ` — written when backfill completes

**`app/rumman_engine.py` (listener)**:
- Added `_GAP_THRESHOLD = 10` — if arriving message ID is 10+ ahead of known newest, gap detected
- Added `ensure_gap_fill_job()` — idempotent INSERT into `processing_jobs` with `target_key=gap:<chat>:<min>:<max>`
- Added `scan_for_listener_gaps()` — startup scan: calls `iter_messages(limit=1)` per tracked chat, creates gap_fill jobs for any detected holes
- Modified `update_sync_state()` — gap detection on every message, writes `last_live_seen_at`

**`app/telegram_backfill_worker.py` (backfill)**:
- Added `GAP_FILL_JOB_TYPE = "telegram_gap_fill"` constant
- Added `release_stale_gap_fill_jobs()` — resets stuck gap_fill jobs at startup
- Added `claim_gap_fill_job()` — claims from `processing_jobs` table
- Added `process_gap_fill_job()` — fetches exact `[fill_min, fill_max]` inclusive range
- Modified main loop — gap-fill jobs checked FIRST before full backfill
- Modified `update_job_progress(done=True)` — writes `backfill_completed=True`, `oldest_message_id`, `coverage_verified_at` to sync_state

**`app/telegram_download_worker.py` (media)**:
- Added `_bare_channel_id()` helper — strips `-100` prefix for PeerChannel() calls
- Fixed `resolve_entity()` — was passing raw negative IDs to PeerChannel, causing entity resolution failures

**Earlier this session — commit a2dac70:**
- `app/search_api.py`: Added `CourseInventoryRequest` model + `/v1/courses/inventory` endpoint
- `app/telegram_bot.py`: Added `_is_planning_query()` + `_handle_planning()` handler + routing update

**Production operations:**
- Applied migration 028 via Supabase management API
- Refreshed PostgREST schema cache via `NOTIFY pgrst, 'reload schema'`
- Reset 26 stuck `processing` jobs to `pending`
- Backfilled `backfill_completed` / `oldest_message_id` for 3 historically completed chats

---

## 7. Remaining Risks

### Critical
| Risk | Description | Impact | Fix Required |
|---|---|---|---|
| **SESSION SHARING** | All 3 Telegram services (listener, backfill, media) share the SAME session string. Currently working by coincidence — they're not colliding because they connect at different times. | Any collision → `AuthKeyDuplicatedError` → services drop → data gap | Ibrahim must generate 2 fresh sessions from different phone numbers (Personal #2 + RUMMAN/غيث) and update Railway env vars |

### High
| Risk | Description | Impact | Fix |
|---|---|---|---|
| **Media null byte bug** | Some PDFs contain ` ` characters. PostgreSQL rejects them (error 22P05). Extraction fails silently. | Some PDFs never enter corpus | Strip null bytes before INSERT in `telegram_download_worker.py` |
| **SYNTHESIZE_ERROR 500** | ~8 synthesis failures on 2026-05-31 01:48-02:05 UTC. Root cause unknown. | Students get error responses | Need search API logs from that window |
| **Intelligence layer off** | `intelligence_items` = 0. No assignment/deadline extraction. | Core Phase 2 value missing | Set `INTELLIGENCE_WORKER_ENABLED=true` on Railway |

### Medium
| Risk | Description | Impact | Fix |
|---|---|---|---|
| **Audio job stuck** | 1 audio_transcribe job stuck in `processing` since 2026-05-28 | Stuck forever, backlog illusion | `UPDATE processing_jobs SET status='pending', retry_count=0 WHERE status='processing' AND job_type='audio_transcribe'` |
| **No heartbeat for backfill/media** | Only embed_worker + attribution_worker write to `worker_heartbeats` | Can't detect silent crashes | Add heartbeat writes to backfill + media workers |
| **18K media queue** | 18,355 telegram_media jobs growing as backfill adds new chats | Media never caught up if worker is slow | Monitor drain rate; consider parallel media workers |
| **Zero-result rate 43%** | Nearly half of queries return nothing | Poor student experience | Run batch_ingest_seu.py for 93 official docs; run qa_mining_worker.py |
| **30 orphaned job types** | `message_ingested` and other legacy jobs clog processing_jobs | Noise in monitoring | DELETE WHERE job_type NOT IN ('telegram_media','audio_transcribe','embed_chunk','telegram_gap_fill','pdf_extract') AND status='completed' |

### Low
| Risk | Description | Impact | Fix |
|---|---|---|---|
| **No coverage dashboard** | No per-chat % complete view | Hard to know when ingestion is done | SQL view or weekly_report addition |
| **No alerting** | No page when services fall behind | Issues found manually | Add Railway health checks or external ping |
| **Cost model rough** | weekly_report estimates cost at flat $/token blends | Actual cost may vary | Not urgent — production volume is low |

---

## 8. Recommended Roadmap

### Immediate (next 1-2 days) — fixes before anything new
1. **Fix session architecture** — Ibrahim generates 2 sessions; update Railway; verify 3 services use 3 different accounts
2. **Fix media null byte bug** — strip ` ` in `telegram_download_worker.py` before INSERT
3. **Reset stuck audio job** — 1 SQL UPDATE
4. **Investigate SYNTHESIZE_ERROR 500** — check Railway search service logs for 01:48-02:05 UTC window

### This Week — Phase 2 completion
5. **Enable intelligence worker** — `INTELLIGENCE_WORKER_ENABLED=true` → verify `intelligence_items` populate
6. **Run batch_ingest_seu.py** — ingest all 93 SEU official docs → reduce 43% zero-result rate
7. **Run qa_mining_worker.py** — extract Q&A pairs from 72K messages → enrich corpus
8. **Enable attribution worker** — reduce unattributed chunks → improves retrieval precision
9. **Add backfill/media heartbeats** — operational hygiene before volume grows

### Later — Phase 3 groundwork
10. **Populate college chat_id mapping** — required for college-scoped intelligence
11. **Gap Analyst** — run scripts/gap_analyst.py on growing zero-result log → identify top knowledge gaps
12. **Coverage dashboard** — per-chat % view in weekly_report or standalone
13. **Multi-tenancy prep** — ensure new tables include tenant_id per ADR-0004
14. **Evaluation harness** — run scripts/eval_bot_quality.py after corpus improvement to measure delta

---

## 9. Product Rating

*Score 1–10, current state vs. production-ready standard*

| Dimension | Score | Notes |
|---|---|---|
| **Data ingestion reliability** | 7/10 | Self-healing architecture in place. Session bug is a live risk. Media queue growing. |
| **Corpus coverage** | 5/10 | 94K chunks is good, but 43% zero-result rate means major coverage gaps. Official docs not yet ingested. |
| **Answer quality** | 6/10 | Synthesis works, grounding works. Zero-result rate too high to rate higher. |
| **Latency** | 7/10 | Low volume, hard to measure. Architecture is sound (intent→search→synthesis in one hop). |
| **Institutional knowledge** | 6/10 | 161 courses seeded. Planning query routing works. Study plans and regulations not yet in corpus. |
| **Operational intelligence** | 1/10 | Intelligence items = 0. Core Phase 2 promise not delivered yet. |
| **Observability** | 4/10 | learning_events exist. worker_heartbeats partial. No alerting. SYNTHESIZE_ERROR mystery unresolved. |
| **Resilience** | 6/10 | Gap detection + gap repair works. Session architecture is fragile. Null byte bug causes silent failures. |
| **Overall** | **5.5/10** | Solid foundation. Two blockers (session bug, zero-result rate) limit real-world utility. Fix those, enable intelligence layer, ingest official docs → jumps to 8/10. |

---

## 10. Executive Summary

RUMMAN is a working operational intelligence system deployed on Railway with 8 services. The core data pipeline — live Telegram ingestion, historical backfill, vector embedding, semantic search, synthesis, and student-facing bot — is live and producing results.

**What works today:**
- 89,349 messages ingested across 88 Telegram groups (18 fully covered, 70 in progress)
- 94,637 document chunks, 100% embedded, serving real queries
- Self-healing ingestion: gaps detected on arrival, filled automatically by backfill worker
- Student bot answers academic questions with source-grounded synthesis
- Planning queries return structured course inventory
- Coverage tracking: completion state written to sync_state on backfill finish

**What doesn't work yet:**
- Intelligence layer (assignment/deadline extraction) is gated — zero intelligence items
- 43% of student queries return no results — official university documents not yet in corpus
- Session architecture is fragile — all 3 Telegram workers share one session (must fix before any collision)
- Media null byte bug causes silent PDF extraction failures

**The two highest-leverage actions:**
1. Fix Telegram session architecture (30 minutes, eliminates a critical single point of failure)
2. Run `batch_ingest_seu.py` for 93 official SEU documents (1 hour, likely cuts zero-result rate from 43% to under 15%)

After those two actions plus enabling the intelligence worker, RUMMAN delivers on its Phase 2 promise: a live corpus, grounded answers, and operational awareness of what is happening inside the university.
