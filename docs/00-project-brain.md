# RUMMAN Project Brain

RUMMAN is an Operational Intelligence OS, not a chatbot.

## Source of Truth

GitHub Docs is the official source of truth for the project.

ChatGPT is used for thinking, architecture, troubleshooting, and decision-making, but final decisions must be documented here.

## Current Phase

Memory/Data Spine.

## Current Stack

- Telegram
- Railway
- Supabase
- n8n
- OpenAI API
- GitHub

## Current Status

- rumman-core is deployed on Railway.
- Live Telegram ingestion is working.
- Historical backfill is disabled in the live listener.
- telegram_sync_state is used for checkpoints.
- telegram_backfill_jobs is used for controlled historical backfill jobs.
- A separate Telegram backfill worker exists but should be run in a controlled way.

## Core Principle

Separate fast live ingestion from heavy historical backfill.

## Architecture Direction

RUMMAN is evolving into a three-layer Operational Intelligence Platform:

- Layer 1 (Data Spine): ingestion, synchronization, raw artifact storage, job queues, operational state
- Layer 2 (Knowledge Layer): OCR, transcription, document extraction, semantic chunking, embeddings, entity graph
- Layer 3 (Intelligence Layer): reasoning, agents, memory synthesis, operational copilots

Current code is entirely Layer 1. Layer 2 must be designed and scaffolded before Layer 3 is enabled.

See ADR-0005 for the formal layering decision.
