# RUMMAN Core

RUMMAN is an Academic Intelligence Platform for Saudi Electronic University (SEU). It crystallizes the collective intelligence of student Telegram communities and makes it accessible through a grounded synthesis layer. The AI is the lens; the corpus is the product.

## Architecture

Eight independent processes deployed on Railway. They share Supabase but never call each other.

| Process | File | Role |
|---|---|---|
| `listener` | `app/rumman_engine.py` | Live Telethon NewMessage handler → `messages` + `telegram_sync_state` |
| `backfill` | `app/telegram_backfill_worker.py` | Claims `telegram_backfill_jobs` rows, processes with lease + heartbeat |
| `media` | `app/telegram_download_worker.py` | Unified handler: `audio_transcribe` + `telegram_media` jobs |
| `embed` | `app/embed_worker.py` | Polls `processing_jobs` for `embed_chunk` → OpenAI embeddings → `document_chunks` |
| `search` | `app/search_api.py` | FastAPI search: query understanding → pgvector → synthesis |
| `bot` | `app/telegram_bot.py` | Student-facing Telegram bot, calls `/synthesize`, returns grounded answers |
| `intelligence` | `app/intelligence_worker.py` | Extract assignments/deadlines/exams from messages → `intelligence_items` |
| `attribution` | `app/attribution_worker.py` | AI-assisted course attribution for untagged chunks → `machine_asserted` |

## Three-Layer Platform

- **Layer 1 — Data Spine:** Ingestion, storage, embeddings, search
- **Layer 2 — Knowledge Layer:** Signal extraction, attribution, gap analysis
- **Layer 3 — Intelligence Layer:** Student-facing synthesis, daily briefs, intelligence workers

All three layers are operational as of Phase 2 (complete 2026-06-01).

## Telegram Session Architecture

Three dedicated user accounts — one per concurrent worker type:

| Account | Session Variable | Used by |
|---|---|---|
| غيث (+966582282200) | `TELEGRAM_LISTENER_GHAYTH_SESSION` | `listener` only |
| راوي (+966590111167) | `TELEGRAM_BACKFILL_RAWI_SESSION` | `backfill` only |
| إبراهيم (+966560064766) | `TELEGRAM_MEDIA_IBRAHIM_SESSION` | `media` only |

Never run two processes on the same session — causes `AuthKeyDuplicatedError`.

## Data Storage

All DB access is direct PostgREST over httpx. No ORM, no Supabase client library. See ADR-0009.

Key tables: `messages`, `telegram_sync_state`, `telegram_backfill_jobs`, `processing_jobs`, `source_documents`, `document_chunks` (VECTOR 1536), `media_files`, `intelligence_items`, `learning_events`, `inst_colleges`, `inst_specializations`, `inst_courses`, `tenants`, `rumman_users`, `rumman_sessions`.

Full schema: `docs/04-database/supabase-schema.md`

## Commands

```bash
pip install -r requirements.txt

# Run workers locally
python3 app/rumman_engine.py
python3 app/telegram_download_worker.py
python3 app/embed_worker.py
python3 app/telegram_backfill_worker.py

# Ingest official university documents
python3 scripts/ingest_document.py <file> --source-type study_plan [--dry-run]

# Seed course structured data
python3 scripts/seed_courses.py [--dry-run] [--embed]

# Test query synthesis locally
python3 app/query_handler.py MGT311 "exam topics"
uvicorn app.search_api:app --reload
```

## Environment Variables

Required: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_LISTENER_GHAYTH_SESSION`, `TELEGRAM_BACKFILL_RAWI_SESSION`, `TELEGRAM_MEDIA_IBRAHIM_SESSION`, `SUPABASE_URL`, `SUPABASE_KEY`, `OPENAI_API_KEY`

## Do Not Commit

- `.env`
- `*.session`
- `auth_session.py`
- `logs/`
- `downloads/`

## Documentation

Engineering memory lives in `docs/`. Start with `docs/00-project-brain.md`.

Architecture decisions: `docs/02-adrs/`
Product and founder doctrine: `docs/08-product-strategy/product-doctrine.md`, `docs/founder-doctrine.md`
