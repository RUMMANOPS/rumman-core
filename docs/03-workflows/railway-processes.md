# Railway Runtime Processes

## Overview

RUMMAN runs on Railway. The Procfile defines **eight independent processes**. Each is a long-running or semi-long-running Python async process. They share Supabase as the coordination plane but never communicate directly with each other.

*Last updated: 2026-06-02 — reflects Phase 2 completion.*

---

## Process: listener

**Command:** `python3 app/rumman_engine.py`

**Role:** Live Telegram ingestion. Always-on. Receives new Telegram messages in real time and inserts them into Supabase.

**Session:** `TELEGRAM_LISTENER_GHAYTH_SESSION` (غيث, +966582282200)

**Intended lifecycle:** Run continuously. Must survive Telegram reconnections, Railway restarts, and network interruptions.

**Key invariant:** The listener must never perform historical crawling. `ENABLE_BACKFILL = False` is a hard guard in source code, not a config option. (ADR-0002)

**Responsibilities:**
- Connect to Telegram as a user client via Telethon StringSession
- Register `NewMessage` event handler for all chats
- Normalize and insert into `messages` table (HTTP 409 = already exists, silently ignored)
- Update `telegram_sync_state` (newest_message_id per chat)
- If message has media → insert into `processing_jobs` (`telegram_media` or `audio_transcribe`)
- If message ID jump > 10 → create gap-fill job
- Discover new groups every 6 hours → auto-create backfill jobs

---

## Process: backfill

**Command:** `python3 app/telegram_backfill_worker.py`

**Role:** Historical Telegram message ingestion. Claims one `telegram_backfill_jobs` row, processes one batch, then exits.

**Session:** `TELEGRAM_BACKFILL_RAWI_SESSION` (راوي, +966590111167)

**Intended lifecycle:** On-demand. Designed to be invoked via Railway one-off commands when a chat needs historical ingestion. Railway restarts it up to 10 times (per `railway.json: restartPolicyMaxRetries: 10`) after each exit — this works as a quasi-continuous loop while jobs remain.

**Behavior:**
1. Releases stale running jobs with expired leases
2. Claims the oldest pending job (by created_at)
3. Acquires a lease with heartbeat renewal
4. Processes up to `batch_size` messages (default 500) from the chat, newest to oldest
5. Updates job progress in Postgres
6. Returns job to pending if more history remains, or marks completed if fully caught up
7. Exits

---

## Process: media

**Command:** `python3 app/telegram_download_worker.py`

**Role:** Unified handler for all Telegram media: audio transcription AND document/image OCR in one process. Avoids session conflicts.

**Session:** `TELEGRAM_MEDIA_IBRAHIM_SESSION` (إبراهيم, +966560064766)

**Gate:** Requires `TELEGRAM_MEDIA_IBRAHIM_SESSION` set in Railway. If absent, all `telegram_media` and `audio_transcribe` jobs will fail.

**Job types handled:**
- `audio_transcribe` — Download OGA/OPUS audio → convert → OpenAI Whisper API → store in `media_files`
- `telegram_media` — Download PDF/image from Telegram → PyMuPDF + GPT-4o Vision OCR → store in `source_documents` → enqueue `embed_chunk` job

**Why unified:** Running separate audio and media workers on the same Telegram account caused `AuthKeyDuplicatedError`. One process, one account, no conflicts. (See ADR-0008)

---

## Process: embed

**Command:** `python3 app/embed_worker.py`

**Role:** Converts extracted document text into vector-embedded chunks stored in `document_chunks`.

**Session:** None (no Telegram access needed)

**Behavior:**
- Polls `processing_jobs` for `embed_chunk` jobs
- Loads `source_documents.extracted_text`
- NFKC Unicode normalization
- Intelligent chunking (question-aware for exams; paragraph-aware for others)
- Embeds with `text-embedding-3-large` (VECTOR(3072))
- Stores in `document_chunks` with full metadata

---

## Process: search

**Command:** `uvicorn app.search_api:app --host 0.0.0.0 --port ${PORT:-8000}`

**Role:** FastAPI search and synthesis service. The platform's primary intelligence API.

**Session:** None

**Key endpoints:**
- `POST /synthesize` — full pipeline: normalize → classify intent → vector search → synthesis
- `POST /search` — retrieval only, no synthesis
- `POST /v1/users/identify` — pseudonymous user creation
- `POST /v1/sessions` — session management
- `GET /health` — operational health check

---

## Process: bot

**Command:** `python3 app/telegram_bot.py`

**Role:** Student-facing Telegram bot. Long-polls Telegram Bot API, routes messages, calls `/synthesize`, returns grounded answers.

**Session:** Uses `TELEGRAM_BOT_TOKEN` from @BotFather (not a StringSession)

**Design:** The bot performs no retrieval or synthesis itself. It is a thin routing + formatting layer that delegates all intelligence to `search_api`. This means `search_api` can serve web clients, mobile apps, or future bots without changes.

---

## Process: intelligence

**Command:** `python3 app/intelligence_worker.py`

**Role:** Continuously extract operational items (exams, deadlines, assignments, announcements) from Telegram messages → `intelligence_items`.

**Session:** None (reads from `messages` table, not Telegram API)

**Gate:** `INTELLIGENCE_WORKER_ENABLED=true` must be set in Railway. Omitting this env var disables the worker.

**Current state (Phase 2):** Enabled in Railway.

**Operation:**
- Cursor-based: reads messages after `last_cursor` stored in `worker_cursors`
- Batch 50 messages, 15 concurrent API calls (semaphore-controlled)
- GPT-4o-mini extracts structured items with confidence scores
- Only stores items with confidence ≥ 0.65
- Deduplication: UNIQUE(tenant_id, source_platform, source_message_id, item_type)

---

## Process: attribution

**Command:** `python3 app/attribution_worker.py`

**Role:** Classify `document_chunks` with null `course_code` to their academic course.

**Session:** None

**Gate:** `ATTRIBUTION_WORKER_ENABLED=true` must be set in Railway.

**Current state (Phase 2):** Enabled in Railway at 3,000 API calls/day budget.

**Two-path pipeline:**
1. Regex-first: if exactly one course code in chunk text → assign immediately (confidence=1.0, zero API cost)
2. LLM: gpt-4o-mini with strict JSON schema → only apply if confidence ≥ 0.85

**Why 0.85 threshold:** False attribution contaminates course search results. A chunk wrongly attributed to IT362 pollutes every IT362 query. At 0.85, the model is essentially certain.

**Provenance:** Every LLM attribution creates an `ai_runs` record. If attribution quality degrades, all affected chunks can be identified via `WHERE attribution_ai_run_id = '<bad_run>'`.

---

## Workers NOT in Procfile (Run on Demand Locally)

| File | Role | Why off |
|---|---|---|
| `app/audio_worker.py` | Standalone audio transcription | Superseded by unified `media` worker |
| `app/daily_brief.py` | Generate structured daily briefs from Telegram streams | Run on cron; not a continuous loop |
| `app/pdf_worker.py` | Extract text from PDFs in Supabase Storage | Run on demand when ingesting official documents |
| `app/query_handler.py` | CLI + importable module: synthesize course intelligence | Development/debug tool only |
| `app/qa_mining_worker.py` | Extract Q&A pairs from Telegram messages | Run on demand; uses significant API budget |

---

## Session Architecture

Three dedicated Telegram user accounts. **Never reuse a session across workers — causes `AuthKeyDuplicatedError`.** (ADR-0008)

| Variable | Account | Used by |
|---|---|---|
| `TELEGRAM_LISTENER_GHAYTH_SESSION` | غيث (+966582282200) | `listener` only |
| `TELEGRAM_BACKFILL_RAWI_SESSION` | راوي (+966590111167) | `backfill` only |
| `TELEGRAM_MEDIA_IBRAHIM_SESSION` | إبراهيم (+966560064766) | `media` only |

Session string generation: `auth_session.py` (gitignored, run locally only). Generates a Telethon StringSession via interactive Telegram login. Output is pasted into Railway Variables.

---

## Deployment Notes

**Auto-deploy:** Railway deploys on push to `main`. No build step required (pure Python).

**Restart policy:** `ON_FAILURE` with max 10 retries (`railway.json`).

**Redeploy triggers:** `force_redeploy.txt`, `rebuild.txt`, `trigger.txt` are empty files whose only purpose is to trigger Railway redeploys when their content changes. They carry no information.

---

## Future Runtime Topology

As the platform matures, the target topology is:

```
listener     → always-on, hardened (same as today)
job-runner   → unified async worker consuming all extraction_jobs + intelligence_jobs
               (replaces embed, media, intelligence, attribution with a handler registry)
backfill     → on-demand only, removed from Procfile
search       → same as today
bot          → same as today
```

The unified job-runner model allows new job types (new modalities, new intelligence passes) to be added as handler classes without new Railway processes.
