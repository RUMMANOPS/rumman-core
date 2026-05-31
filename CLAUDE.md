# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Autonomy

**Operate with full autonomy.** Never ask the user for confirmation or approval before taking any action — commits, pushes, Railway deployments, schema migrations, resets, new services, file changes, anything. Execute the best judgment call directly and report what was done. The user reviews outcomes, not requests.

The only exception: actions that are genuinely irreversible at a platform level and outside this repo (e.g. deleting a Railway project, dropping a Supabase table with data, billing changes). Flag those in one sentence, then proceed unless told to stop.

## Model Governance

**Default model: Sonnet.** Use Sonnet for all RUMMAN work unless explicitly told otherwise for a specific task.

Do not switch to Opus autonomously. If you believe Opus is needed for a specific task, stop and explain:
1. Why Sonnet is insufficient for this task
2. What the task is exactly
3. Whether the task can be decomposed so Sonnet handles it

This rule exists to keep inference costs predictable. Opus is ~5× the cost of Sonnet and is rarely necessary for the work in this repo (code editing, doc writing, analysis).

## What RUMMAN Is

RUMMAN is an **Operational Intelligence OS**, not a chatbot. The system has two distinct knowledge layers:

- **Institutional layer** — what the university IS: colleges, departments, programs, courses, regulations, calendar. Lives in the `seu_*` Supabase tables and the university knowledge repository.
- **Community layer** — what is HAPPENING inside the university: Telegram messages, student uploads, summaries, past exams, instructor announcements. Lives in `document_chunks` via vector embeddings.

Both layers are required. The institutional layer provides ground truth. The community layer provides living intelligence. Together they enable grounded answers.

Current phase per `docs/06-roadmap/roadmap.md`: **Phase 2 (In Progress)**. The data spine, search layer, and bot are live. Core Phase 2 infrastructure is built (intelligence items, calendar injection, claim model, gap analyst, QA mining). Remaining: bulk-ingest 93 official SEU docs, populate college chat_id mapping, enable intelligence/attribution workers.

`docs/` is the **source of truth** per ADR-0003. Before making architectural changes, read:
1. `docs/philosophy/vocabulary.md` — precise definitions of load-bearing terms
2. `docs/philosophy/core-principles.md` — the beliefs that shape all design decisions
3. `docs/constraints/hard-boundaries.md` — rules that must not be broken
4. The relevant ADR in `docs/02-adrs/`

**Documentation governance:** `docs/philosophy/` and `docs/constraints/` are **invariant** — never modify autonomously. `docs/01-architecture/` is maintained — AI may draft, human approves. See `docs/README.md` for the full governance model.

## Runtime Topology

Deployed on Railway. `Procfile` defines **eight independent processes** — they share Supabase but never call each other:

| Process | File | Role | Status |
|---|---|---|---|
| `listener` | `app/rumman_engine.py` | Live Telethon `NewMessage` handler → `messages` + `telegram_sync_state`. Never crawls history. | Always on |
| `backfill` | `app/telegram_backfill_worker.py` | Claims pending `telegram_backfill_jobs` rows, processes with lease + heartbeat. Loops until idle. | Always on |
| `media` | `app/telegram_download_worker.py` | Unified handler: `audio_transcribe` + `telegram_media` jobs in one process (avoids session conflicts). | Always on |
| `embed` | `app/embed_worker.py` | Polls `processing_jobs` for `job_type=embed_chunk` → chunk text → OpenAI embeddings → `document_chunks`. | Always on |
| `search` | `app/search_api.py` | FastAPI search service: query understanding → pgvector → synthesis. Port `$PORT`. | Always on |
| `bot` | `app/telegram_bot.py` | Student-facing Telegram bot: long-polls Telegram API, calls `/synthesize`, returns grounded answers. | Always on |
| `intelligence` | `app/intelligence_worker.py` | Extract operational items (assignments, deadlines) from messages → `intelligence_items`. | Gated: `INTELLIGENCE_WORKER_ENABLED=true` |
| `attribution` | `app/attribution_worker.py` | AI-assisted course attribution for untagged document chunks → `machine_asserted`. | Gated: `ATTRIBUTION_WORKER_ENABLED=true` |

**Note on session architecture (three distinct StringSessions):**
- `TELEGRAM_SESSION_STRING` — personal account; used by `listener` (rumman_engine.py)
- `TELEGRAM_BACKFILL_SESSION_STRING` — personal account #2; used by `backfill` (telegram_backfill_worker.py)
- `TELEGRAM_WORKER_SESSION_STRING` — RUMMAN/غيث dedicated number; used by `media` (telegram_download_worker.py)

Never run two processes on the same StringSession simultaneously — causes `AuthKeyDuplicatedError`.

**Workers NOT in the Procfile (run on demand locally):**

| File | Role | Why off |
|---|---|---|
| `app/audio_worker.py` | Polls `processing_jobs` for `job_type=audio_transcribe` → OpenAI Whisper → `media_files`. | Superseded by unified `media` worker |
| `app/daily_brief.py` | Generate structured daily briefs from Telegram streams | Requires intelligence layer; off until Phase 2 |
| `app/pdf_worker.py` | Extract text from PDFs in Supabase Storage → `source_documents` | Run on demand when ingesting official documents |
| `app/query_handler.py` | CLI + importable module: synthesize course intelligence from all layers | Development/debug tool; not a service |
| `app/qa_mining_worker.py` | Extract Q&A pairs from 72K Telegram messages → embed → `document_chunks` | Run on demand; requires migration 026+027 |

`auth_session.py` is a one-shot local helper to generate `TELEGRAM_SESSION_STRING` — gitignored, never runs on Railway.

## The Cardinal Architectural Rule

**Live ingestion and historical backfill are permanently separated** (ADR-0002). Earlier versions merged them and produced startup delays, rate-limit hits, and uncontrolled crawls. Concretely:

- `rumman_engine.py` must not call `iter_messages` or any historical crawl. `ENABLE_BACKFILL` exists as a guard and must stay `False`.
- Backfill writes go through `telegram_backfill_jobs` lifecycle: pending → running (lease) → progress → pending again until oldest reached → completed.
- Both writers go to `messages`; duplicates caught via HTTP 409 (unique constraint on platform message id) — preserve this dedup on schema changes.

## Data Spine

All DB access is **direct PostgREST over httpx** (`{SUPABASE_URL}/rest/v1/<table>`) using the service-role key. No Supabase client library, no ORM.

Patterns:
- Insert with `Prefer: return=representation`. Treat 409 as `"duplicate"`, ≥400 as `"error"`.
- Upsert via `Prefer: resolution=merge-duplicates` + `?on_conflict=<col>`.
- Conditional patch by including expected `status` / `worker_id` as URL filters — silent fail on race prevents double-processing.

Key tables:
- `messages` — canonical messages. Keyed on (`platform_chat_id`, `platform_message_id`).
- `telegram_sync_state` — one row per chat; newest/oldest ids + total seen.
- `telegram_backfill_jobs` — controlled historical work with lease lifecycle.
- `processing_jobs` — generic async work queue (`audio_transcribe`, `telegram_media`, `embed_chunk`, `pdf_extract`).
- `source_documents` — uploaded/ingested files awaiting or post-extraction.
- `document_chunks` — vector-embedded chunks; the retrieval corpus.
- `media_files` — audio transcription results.
- `inst_colleges`, `inst_specializations`, `inst_courses` — SEU institutional layer (master data; renamed from `seu_*` for multi-tenancy).
- `tenants`, `users`, `sessions` — platform identity layer.
- `analysis_runs` — append-only analyst output log (gap_analyst, qa_miner, etc.) — migration 026.
- `gap_items` — normalised knowledge gap rows, one per cluster — migration 026.
- `active_extracted_items` — view: extracted_items filtered by temporal validity + not rejected/superseded — migration 025.
- `active_document_chunks` — view: document_chunks filtered by superseded_by — migration 025.

ADR-0004: every operational object must **eventually** carry `tenant_id`. New tables should include it from the start.

## University Knowledge Repository

The institutional knowledge repository lives outside this repo at:
```
.../0-RUMMAN/0-Universities/1- Saudi Electronic University/
```

Structure:
```
0. OpenData/          — enrollment stats, faculty data (PDF)
1. StudyPlans/        — official program study plans (PDF/DOCX), organized by college → dept → program
2. Regulations/       — exam rules, procedures, student guides (PDF)
3. AcademicCalendar/  — semester dates and windows (TXT/PDF)
4. CourseContent/     — individual course syllabi (PDF) — currently ENGT program (34 files)
5. Diplomas/          — Applied College diploma programs
_metadata/            — knowledge_manifest.json, program_index.json
```

**To ingest official documents into the platform**, use `scripts/ingest_document.py`:
```bash
python3 scripts/ingest_document.py <file_path> \
    --source-type study_plan \
    --course-code IT362 \
    [--dry-run]
```

Source types: `exam`, `study_plan`, `regulation`, `course_description`, `telegram_export`, `upload`

**To seed structured course data** (names, descriptions, prerequisites) from `scripts/data/seu_courses.json`:
```bash
python3 scripts/seed_courses.py              # seed all programs
python3 scripts/seed_courses.py --dry-run    # validate only
python3 scripts/seed_courses.py --embed      # also embed course descriptions
```

## Scripts

Operational CLI tools in `scripts/`. All require `.env` with `SUPABASE_URL`, `SUPABASE_KEY`, `OPENAI_API_KEY`.

| Script | Purpose |
|---|---|
| `ingest_document.py` | Ingest a local file into the knowledge pipeline (upload → pdf_extract job → embed_chunk job) |
| `batch_ingest_seu.py` | Bulk-ingest all 93 SEU knowledge repository documents in priority order |
| `seed_courses.py` | Seed structured course data from `scripts/data/seu_courses.json` into `inst_courses` |
| `create_backfill_jobs.py` | Create `telegram_backfill_jobs` rows for specified chat IDs |
| `generate_seed_lexicon.py` | Generate normalization dictionary candidates from corpus — outputs to `data/seed_candidates_*.json` (gitignored) |
| `review_candidates.py` | Interactive review of lexicon candidates before adding to `data/normalization_dict.json` |
| `extract_concepts.py` | Extract academic concepts from document chunks for knowledge graph seeding |
| `gap_analyst.py` | Cluster zero-result learning_events into knowledge gaps → `analysis_runs` + `gap_items` |
| `backfill_course_codes.py` | Regex + LLM inference to fill null course_code on source_documents and their chunks |
| `weekly_report.py` | Weekly ops + product health report (pipeline, query volume, corpus coverage) → Telegram ops channel |
| `eval_bot_quality.py` | Before/after synthesis quality comparison for 10 representative test queries |
| `message_signal_worker.py` | Extract typed intelligence signals from Telegram messages → `message_signals` (exam_emphasis, difficulty, professor_note, resource_rec, confusion_cluster) |

## Commands

```bash
pip install -r requirements.txt              # deps

# Run workers locally
python3 app/rumman_engine.py                 # live listener
python3 app/telegram_download_worker.py      # media + audio handler
python3 app/embed_worker.py                  # embed chunks
python3 app/telegram_backfill_worker.py      # one backfill batch (exits after)

# Ingest official university documents
python3 scripts/ingest_document.py <file> --source-type study_plan [--dry-run]

# Seed course structured data
python3 scripts/seed_courses.py [--dry-run] [--embed] [--program BSCS]

# Development tools
python3 app/query_handler.py MGT311 "exam topics"  # test query synthesis locally
uvicorn app.search_api:app --reload                 # run search API locally

python3 auth_session.py                            # generate session string (LOCAL ONLY)
```

## Required Environment

`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_STRING`, `SUPABASE_URL`, `SUPABASE_KEY`, `OPENAI_API_KEY`.

Optional: `WORKER_ID`, `BACKFILL_SLEEP_SECONDS`, `BACKFILL_LEASE_MINUTES`, `RUMMAN_USER_SALT`, `SEARCH_API_URL`.

## Repo Conventions

- `archive_old/` is gitignored — superseded code only; never import from it.
- `data/seed_candidates_*.json` is gitignored — ephemeral lexicon generation outputs.
- `.env`, `*.session`, `auth_session.py`, `downloads/`, `logs/` are gitignored.
- Empty files `force_redeploy.txt`, `rebuild.txt`, `trigger.txt` exist only to nudge Railway redeploys — they carry no information.
- Telethon `StringSession` is the only auth path; never write `.session` files in deployed code.
- Workers print structured single-line log events (`JOB_CLAIMED | id=... | chat=...`) with `flush=True` for Railway log greppability.
- All DB access via direct PostgREST — no ORM, no Supabase client library.
