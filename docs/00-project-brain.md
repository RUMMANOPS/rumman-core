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

RUMMAN should evolve into a stateful, observable, resumable, and scalable operational intelligence platform.
