# Supabase Schema

## Purpose

Supabase is the operational data spine of RUMMAN.

It stores operational memory, ingestion state, workflows, jobs, entities, intelligence outputs, and historical communication records.

---

# Core Tables

## messages

Stores normalized Telegram messages and future platform messages.

### Responsibilities

- canonical message storage
- ingestion target
- historical archive
- operational memory source

### Important Fields

- platform_message_id
- platform_chat_id
- platform_user_id
- message_text
- message_type
- message_date
- raw_json
- metadata

---

## telegram_sync_state

Stores synchronization checkpoints per Telegram chat.

### Responsibilities

- live ingestion checkpoint tracking
- synchronization progress
- backfill awareness
- chat-level ingestion state

### Important Fields

- platform_chat_id
- chat_type
- chat_name
- newest_message_id
- oldest_message_id
- total_messages_seen
- backfill_completed

---

## telegram_backfill_jobs

Stores historical ingestion jobs.

### Responsibilities

- controlled historical crawling
- resumable backfill
- distributed worker coordination
- lease management
- retry tracking

### Important Fields

- status
- worker_id
- retry_count
- heartbeat_at
- lease_expires_at
- total_processed
- last_processed_message_id

---

## jobs

Generic asynchronous operational jobs.

### Future Usage

- intelligence analysis
- embeddings
- summarization
- extraction
- classification
- automation triggers

---

## entities

Stores extracted entities from operational conversations.

### Examples

- people
- organizations
- projects
- locations
- systems

---

## entity_relationships

Stores relationships between entities.

### Examples

- person belongs to organization
- project owned by team
- task linked to decision

---

## memories

Long-term operational memory objects.

---

## tasks

Operational tasks extracted from conversations and workflows.

---

## deadlines

Deadline tracking and operational timing intelligence.

---

## decisions

Operational and strategic decisions extracted from discussions.

---

## insights

Derived operational intelligence outputs.

---

# Long-Term Direction

The database should evolve into a scalable operational intelligence memory system rather than a simple chatbot datastore.
