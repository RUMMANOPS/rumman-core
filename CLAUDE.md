# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What RUMMAN Is

RUMMAN is an **Operational Intelligence OS**, not a chatbot. Current phase per `docs/06-roadmap/roadmap.md` is **Phase 1: Memory/Data Spine** — stabilize ingestion before turning on intelligence. The system ingests Telegram traffic into Supabase, where it will eventually become the operational memory for a multi-tenant SaaS platform (ADR-0004).

`docs/` is the **source of truth** per ADR-0003 — read the relevant doc before changing architecture, and update docs when decisions change. ADRs in `docs/02-adrs/` are authoritative.

## Runtime Topology

Deployed on Railway. `Procfile` defines three **independent** processes — they share Supabase but never call each other:

| Process | File | Role |
|---|---|---|
| `listener` | `app/rumman_engine.py` | Live Telethon `NewMessage` handler → inserts into `messages`, updates `telegram_sync_state`. Never crawls history. |
| `audio` | `app/audio_worker.py` | Polls `processing_jobs` where `job_type=audio_transcribe`, downloads media via Telethon, transcribes through OpenAI (`gpt-4o-mini-transcribe`, `language=ar`), writes back to `media_files`. |
| `backfill` | `app/telegram_backfill_worker.py` | Claims one `telegram_backfill_jobs` row, processes a batch with lease + heartbeat, releases stale leases on startup. Designed to be run on demand, not continuously. |

`app/intelligence_worker.py` exists but is **deliberately not in the Procfile** — intelligence is kept off until the data spine is stable (roadmap Phase 1 → Phase 2 boundary). Do not add it to the Procfile without an ADR update.

`auth_session.py` is a one-shot local helper to generate `TELEGRAM_SESSION_STRING` — it is `.gitignore`'d and must never run on Railway.

## The Cardinal Architectural Rule

**Live ingestion and historical backfill are permanently separated** (ADR-0002). Earlier versions merged them and produced startup delays, rate-limit hits, and uncontrolled crawls. Concretely:

- `rumman_engine.py` must not call `iter_messages` or any historical crawl. `ENABLE_BACKFILL` exists as a guard and must stay `False`.
- Backfill writes go through `telegram_backfill_jobs` lifecycle: pending → running (lease) → progress → pending again until oldest reached → completed. Stale `running` jobs whose `lease_expires_at` is past are auto-released by the next worker.
- Both writers go to the same `messages` table; duplicates are caught via HTTP 409 from PostgREST (unique constraint on platform message id) — preserve this dedup behavior on schema changes.

## Data Spine

All DB access is **direct PostgREST over httpx** (`{SUPABASE_URL}/rest/v1/<table>`) using the service-role key in `apikey` + `Authorization` headers. There is no Supabase client library and no ORM. Patterns to follow:

- Insert with `Prefer: return=representation`. Treat 409 as `"duplicate"`, ≥400 as `"error"`.
- Upsert via `Prefer: resolution=merge-duplicates` + `?on_conflict=<col>` (see `update_sync_state` in `rumman_engine.py`).
- Conditional patch by including the expected `status` / `worker_id` as filters in the URL so the patch fails silently if another worker raced — this is how the backfill lease is held.

Tables (see `docs/04-architecture/` and `docs/04-database/`):

- `messages` — canonical normalized messages from any platform. Keyed on (`platform_chat_id`, `platform_message_id`).
- `telegram_sync_state` — one row per chat with newest/oldest message ids and `total_messages_seen`. Lightweight and updated on every live insert.
- `telegram_backfill_jobs` — controlled historical work with `status`, `worker_id`, `heartbeat_at`, `lease_expires_at`, `retry_count`.
- `processing_jobs` — generic async work queue (audio transcription today; classification/extraction later).
- `media_files`, `intelligence_items`, plus planned `entities`, `memories`, `tasks`, `deadlines`, `decisions`, `insights`.

ADR-0004 + `docs/01-architecture/tenant-isolation-strategy.md`: every operational object must **eventually** carry tenant ownership. New tables should be designed with `tenant_id` in mind even if it isn't populated yet.

## Commands

```bash
pip install -r requirements.txt              # deps (telethon, httpx, python-dotenv, openai)

python3 app/rumman_engine.py                  # run listener locally
python3 app/audio_worker.py                   # run audio worker locally
python3 app/telegram_backfill_worker.py       # run one backfill batch (worker exits after processing one job)

python3 auth_session.py                       # generate TELEGRAM_SESSION_STRING (LOCAL ONLY)
```

No test suite, lint, or build step exists in this repo. Deployment is `git push` — Railway picks up the `Procfile` and restarts on failure (`railway.json`: max 10 retries).

## Required Environment

`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_STRING`, `SUPABASE_URL`, `SUPABASE_KEY`, `OPENAI_API_KEY`. Backfill worker also reads optional `WORKER_ID`, `BACKFILL_SLEEP_SECONDS`, `BACKFILL_LEASE_MINUTES`.

## Repo Conventions

- `archive_old/` is `.gitignore`'d — it holds superseded code; do not add new code there and do not import from it.
- `.env`, `*.session`, `auth_session.py`, `downloads/`, `logs/` are git-ignored. Empty marker files like `force_redeploy.txt`, `rebuild.txt`, `trigger.txt` exist only to nudge Railway redeploys.
- Telethon `StringSession` is the only auth path; never write `.session` files in deployed code.
- Workers print structured single-line log events (`JOB_CLAIMED | id=... | chat=...`) with `flush=True` — keep this format so Railway logs stay greppable.
