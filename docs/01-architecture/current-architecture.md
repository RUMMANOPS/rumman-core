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

## Current Rule

Do not run uncontrolled Telegram historical crawling inside the live listener.
