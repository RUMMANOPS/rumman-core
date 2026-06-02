# RUMMAN Project Brain

RUMMAN is an Operational Intelligence OS for Saudi universities — not a chatbot.

*Last updated: 2026-06-02 — Phase 2 complete.*

## Source of Truth

`docs/` is the authoritative source of truth for all architectural decisions, schema intent, and operational conventions. (ADR-0003)

`RUMMAN_MASTER_DOSSIER.md` (repo root) is the complete institutional memory document produced at Phase 2 completion. Read it for full system context.

## Current Phase

**Phase 2 — Institutional Intelligence: Complete** (as of 2026-06-01)

Completed Phase 2 capabilities:
- Academic calendar injection (exam schedule / deadline queries)
- Intelligence worker: extracts operational items from messages → `intelligence_items`
- Attribution worker: classifies untagged chunks to courses
- QA mining: extracted Q&A pairs from Telegram history
- Message signal worker: exam_emphasis, difficulty, professor_note, resource_rec, confusion_cluster
- Gap analyst: knowledge coverage gaps → `gap_items`
- Course intelligence profiles (per-course corpus summary)
- Exam intelligence (top recurring exam topics per course)
- Student context persistence (enrolled courses, active focus, language preference)
- Synthesis cache (2-hour LRU)
- Weekly ops health report

Phase 3 (Multi-University Expansion) has not begun. See `docs/06-roadmap/roadmap.md`.

## Current Stack

- **Telegram** — ingestion source (user accounts via Telethon; student-facing bot via Bot API)
- **Railway** — runtime: 8 independent processes (see `docs/03-workflows/railway-processes.md`)
- **Supabase** — PostgreSQL + pgvector + Storage; all data, all coordination
- **OpenAI** — text-embedding-3-large (embeddings), gpt-4o-mini (synthesis, attribution, intelligence), gpt-4o (OCR, complex synthesis), Whisper (audio)
- **GitHub** — code and documentation source of truth
- ~~n8n~~ — planned for Phase 3; not yet deployed

## Current Status

- 8 processes deployed on Railway: listener, backfill, media, embed, search, bot, intelligence (gated), attribution (gated)
- Live Telegram ingestion: active (غيث account)
- Historical backfill: active (راوي account)
- Media/OCR pipeline: active pending TELEGRAM_MEDIA_IBRAHIM_SESSION being set in Railway
- Vector search + synthesis: active
- Intelligence extraction: active (INTELLIGENCE_WORKER_ENABLED=true)
- Attribution: active (ATTRIBUTION_WORKER_ENABLED=true, 3K calls/day)
- Three dedicated Telegram accounts: one per concurrent worker type (ADR-0008)

## Core Principle

Separate fast live ingestion from heavy historical backfill. (ADR-0002)

## Architecture Direction

RUMMAN is a three-layer Operational Intelligence Platform. All three layers are functionally operational as of Phase 2:

- **Layer 1 (Data Spine):** ingestion, synchronization, raw artifact storage, job queues — `listener`, `backfill`, `media`
- **Layer 2 (Knowledge Layer):** extraction, chunking, embeddings, attribution — `media`, `embed`, `attribution`
- **Layer 3 (Intelligence Layer):** synthesis, intelligence extraction, student context — `intelligence`, `search`, `bot`

See ADR-0005 for the formal layering decision. Note: ADR-0005's status claims on intelligence_worker and Layer 2 are superseded by Phase 2 completion.

## Product Identity

RUMMAN is an Academic Intelligence Platform. The AI is a lens through which accumulated community knowledge becomes legible — not the product itself.

See `docs/08-product-strategy/product-doctrine.md` for the full product doctrine.
