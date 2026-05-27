# Current Architecture

## Overview

RUMMAN currently uses Telegram as an input source, Railway as the runtime environment, Supabase as the operational database, GitHub as the code and documentation source of truth, and n8n as an orchestration layer.

## Current Runtime Processes

Defined in Procfile:

- listener: runs live Telegram ingestion.
- audio: handles audio/media pipeline.
- backfill: runs historical Telegram backfill worker on demand.

## Current Design

Live ingestion and historical backfill are separated.

The live listener only handles new messages and writes them to Supabase.

The backfill worker processes old messages through controlled jobs stored in Supabase.

## What n8n Is For

n8n is not the core brain.

n8n is the orchestration layer for workflows, notifications, integrations, and triggering processes.

## What Supabase Is For

Supabase is the operational data spine.

It stores messages, sync state, jobs, media records, memories, tasks, decisions, deadlines, and insights.

## Three-Layer Platform Model

RUMMAN is evolving across three distinct architectural layers. Current code covers Layer 1.

### Layer 1 — Data Spine (current)
Ingestion, synchronization, raw artifact storage, job queues, lease coordination, operational state, tenant management. Everything in the current Procfile lives here.

### Layer 2 — Knowledge Layer (next)
Extraction pipelines (OCR, transcription, document parsing), semantic chunking, embedding generation, entity extraction, knowledge graph population. This layer transforms raw artifacts into queryable knowledge objects.

### Layer 3 — Intelligence Layer (gated)
Reasoning systems, operational memory synthesis, agents, copilots, recommendations. This layer is gated on Layer 2 being stable. `intelligence_worker.py` is a Layer 3 sketch; it must not be enabled until Layer 2 exists.

## Current Rule

Do not run uncontrolled Telegram historical crawling inside the live listener.

## Layer Boundary Rule

Do not implement Layer 3 behavior in Layer 1 workers. Do not skip Layer 2. Every piece of organizational knowledge must pass through extraction and normalization (Layer 2) before reaching intelligence (Layer 3). This is what makes the platform replayable, auditable, and multi-tenant safe.
