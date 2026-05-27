# Railway Runtime Processes

## Overview

RUMMAN runs on Railway. The Procfile defines three processes. Each is a long-running or semi-long-running Python async process. They share Supabase as the coordination plane but never communicate directly with each other.

---

## Process: listener

**Command:** `python3 app/rumman_engine.py`

**Role:** Live Telegram ingestion. Always-on. Receives new Telegram messages in real time and inserts them into Supabase.

**Intended lifecycle:** Run continuously. Must survive Telegram reconnections, Railway restarts, and network interruptions. Should be the most stable process in the fleet.

**Current gaps:**
- No Telethon FloodWaitError handling (crashes on Telegram rate limit)
- No heartbeat emission (silent disconnection is undetectable)
- No missed-message recovery on restart (messages received during downtime are lost)
- httpx.AsyncClient created per message instead of being reused

**Key invariant:** The listener must never perform historical crawling. `ENABLE_BACKFILL = False` in source. Historical gaps must be addressed via the backfill system, not the listener.

---

## Process: audio

**Command:** `python3 app/audio_worker.py`

**Role:** Transcription of voice/audio messages. Polls `processing_jobs` for `job_type=audio_transcribe`, downloads the media via Telethon, sends to OpenAI Whisper, writes transcript back to `media_files`.

**Intended lifecycle:** Continuous polling loop with 3-second sleep between empty cycles. Processes all pending/failed jobs before sleeping.

**Current gaps:**
- `processing_jobs` has no producer in this codebase — something external (n8n or manual SQL) must enqueue jobs
- No lease/heartbeat on `processing_jobs` — a crash mid-job causes re-download and re-transcription on next run (OpenAI cost duplication)
- Downloaded audio file is discarded after transcription — source artifact is not preserved

**Note:** This worker embeds Layer 2 (knowledge extraction) logic inside Layer 1 infrastructure. When Layer 2 is formally built, this worker will be refactored into an extraction_jobs handler within the unified job runner.

---

## Process: backfill

**Command:** `python3 app/telegram_backfill_worker.py`

**Role:** Historical Telegram message ingestion. Claims one `telegram_backfill_jobs` row, processes one batch of messages, then exits.

**Intended lifecycle:** On-demand, not continuous. Designed to be invoked manually or via Railway one-off / n8n trigger when a specific chat needs historical ingestion.

**CONFLICT:** The Procfile entry means Railway treats this as a managed process and restarts it on exit. The worker exits after one job, so Railway will restart it up to 10 times (per `railway.json: restartPolicyMaxRetries: 10`). After 10 restarts it stops. This is a partially functional workaround — not the intended design.

**Resolution needed:** Backfill should be removed from the Procfile and moved to Railway one-off commands or n8n-triggered invocations. This is a low-risk operational change that requires coordination between the Railway environment and n8n orchestration. Requires human decision before implementation.

**Current behavior when correctly invoked:**
1. Releases stale running jobs with expired leases
2. Claims the oldest pending job (by created_at)
3. Acquires a lease with heartbeat renewal
4. Processes up to `batch_size` messages (default 500) from the chat, newest to oldest
5. Updates job progress in Postgres
6. Returns job to pending if more history remains, or marks completed if fully caught up
7. Exits

---

## Process: intelligence (NOT in Procfile — deliberately disabled)

**Source file:** `app/intelligence_worker.py`

**Why disabled:** Intelligence extraction (Layer 3) must not run until the Knowledge Layer (Layer 2) is operational and the following are in place:
- `ai_runs` table (audit trail for all AI operations)
- Per-tenant cost controls
- Source traceability on all extracted items
- Dead-letter path for failed extractions
- Preview/dry-run mode for prompt iteration

**Known bugs in current implementation (before enabling):**
- References `msg.get("content")` but the field is `message_text` — every message silently skipped
- No deduplication — re-processes the same 20 messages every 30 seconds
- No ai_runs logging — outputs are untraceable
- No tenant_id — all extractions are tenant-less

---

## Deployment Notes

**Auto-deploy:** Railway deploys on push to main. No build step required (pure Python).

**Restart policy:** `ON_FAILURE` with max 10 retries (`railway.json`).

**Environment variables:** All secrets are Railway environment variables. Never committed to git. Required: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_STRING`, `SUPABASE_URL`, `SUPABASE_KEY`, `OPENAI_API_KEY`.

**Session string generation:** `auth_session.py` must be run locally, not on Railway. It generates the `TELEGRAM_SESSION_STRING` via interactive Telegram login. Output is pasted into Railway Variables. The file itself is `.gitignore`'d.

---

## Future Runtime Topology

The current three-process model reflects Phase 0/1. As Layer 2 and Layer 3 are built, the topology should evolve:

```
listener     → always-on, supervised, hardened (same as today)
job-runner   → unified async worker consuming extraction_jobs + intelligence_jobs
               (replaces audio worker and future intelligence workers with a handler registry)
backfill     → on-demand only, removed from Procfile
```

The unified job-runner model allows new job types (OCR, embedding, entity extraction, intelligence) to be added as handler classes without new Railway processes. Each handler uses the same lease/heartbeat pattern.
