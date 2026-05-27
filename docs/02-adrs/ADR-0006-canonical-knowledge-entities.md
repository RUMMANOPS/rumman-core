# ADR-0006: Canonical Knowledge Entities and Ontology

## Status

Accepted

## Context

As RUMMAN processes messages, documents, audio, images, and structured files across multiple tenants, a consistent ontology is required. Without a defined entity model, each worker invents its own schema, extraction results are not joinable, and intelligence queries become impossible.

The entity model also determines what the Knowledge Layer (Layer 2) produces and what the Intelligence Layer (Layer 3) consumes.

## Decision

RUMMAN uses a four-group canonical entity model organized by architectural layer:

---

### Group 1: Source Entities (Layer 1)

These represent the origin of data, not the data itself.

**sources**
A registered connection to a source platform. One row per (tenant, platform, identity).
- tenant_id, platform (telegram/email/whatsapp/file), platform_identity_id
- identity_type (user_client/bot/email_address/etc.)
- status, created_at

**ingestion_events**
A normalized inbound signal from any source. Platform-specific details preserved in raw_json; key fields normalized.
- tenant_id, source_id, platform, platform_event_id, event_type (message/file/edit/delete)
- occurred_at, raw_json, processing_status

**raw_artifacts**
An immutable binary file associated with an ingestion event. The source record; never modified after creation.
- tenant_id, ingestion_event_id, artifact_type (audio/image/pdf/document/video/spreadsheet)
- storage_path, size_bytes, mime_type, checksum
- upload_status, created_at

---

### Group 2: Knowledge Entities (Layer 2)

These represent extracted and structured knowledge derived from source data.

**knowledge_artifacts**
The processed representation of one raw artifact or ingestion event. Primary Layer 2 output.
- tenant_id, source_ingestion_event_id, source_raw_artifact_id
- content_type (transcript/ocr_text/parsed_document/structured_data)
- extracted_text, structured_json (optional, for tables/spreadsheets)
- extraction_model, extraction_version, extracted_at
- word_count, language

One raw_artifact produces one knowledge_artifact. If extraction is re-run with a new model, a new version of the knowledge_artifact is created (versioned, not replaced) to preserve extraction history.

**knowledge_chunks**
Semantic segments of a knowledge_artifact, sized for embedding and retrieval.
- tenant_id, knowledge_artifact_id, chunk_index, chunk_text
- token_count, chunk_strategy (paragraph/sentence/fixed_token)

**embeddings**
Vector representations of knowledge_chunks.
- tenant_id, knowledge_chunk_id, embedding_model, embedding_version
- embedding (vector), created_at

Filtering rule: every semantic search must include `WHERE tenant_id = ?`. Cross-tenant similarity search is a privacy violation.

**entities**
Named objects extracted from knowledge artifacts.
- tenant_id, entity_type (person/organization/project/location/event/date/document/system)
- canonical_name, aliases (array), first_seen_at, last_seen_at
- extraction_confidence

**entity_relationships**
Typed edges between entities.
- tenant_id, from_entity_id, to_entity_id, relationship_type
- source_knowledge_artifact_id, confidence, valid_from, valid_until

---

### Group 3: AI Operation Entities (cross-layer)

These provide auditability and traceability for all AI-driven operations.

**ai_runs**
An immutable record of every AI model invocation. Required before any AI output is written.
- tenant_id, worker, prompt_name, prompt_version
- model, input_tokens, output_tokens, cost_usd
- input_hash (for dedup detection), output_json
- source_table, source_id (what was processed)
- status (completed/failed), error, started_at, completed_at

Rule: every row in tasks/decisions/deadlines/memories/insights must have a source_ai_run_id foreign key. AI outputs without lineage are not valid.

---

### Group 4: Intelligence / Operational Memory Entities (Layer 3)

These are the outputs of reasoning — structured operational knowledge extracted from conversations and documents.

All entities in this group carry: tenant_id, source_ai_run_id, source_ingestion_event_id, confidence, created_at.

**tasks**
An actionable item identified in organizational communications.
- title, description, assignee_entity_id, due_date, priority, status

**deadlines**
A time-bound operational constraint.
- title, deadline_at, source_context, related_task_id

**decisions**
A resolved choice captured from discussions.
- title, decision_text, decided_by_entity_id, decided_at, rationale

**memories**
Long-term operational facts with broader scope than a single task or decision.
- memory_type (fact/relationship/pattern/preference), content, valid_from, valid_until

**insights**
Derived analytical observations from patterns across operational data.
- insight_type, content, supporting_evidence_ids, confidence

---

## Ontology Evolution Rules

1. New entity types may be added. Existing entity types may be extended with new fields. Entity types are never deleted — they are deprecated with a status flag.

2. entity.entity_type is a controlled vocabulary. New types require a documented decision (not necessarily a full ADR, but at minimum a commit message explaining the type and its scope).

3. knowledge_artifacts are versioned, not replaced. Reprocessing creates a new version with a new extraction_model and extraction_version. Downstream embeddings and entities must reference the specific artifact version they were derived from.

4. ai_runs are immutable. An AI run that produced wrong output is marked status=failed; a new run is created. The wrong output row is preserved for audit, not deleted.

## Consequences

### Positive

- Intelligence outputs are always traceable to source data through ai_runs
- Re-extraction is safe because artifact versioning prevents silent replacement
- Entity graph is queryable across all knowledge types
- Multi-tenant privacy is enforced by tenant_id on every entity
- Layer boundaries are clearly expressed in the entity model

### Negative

- Schema is more complex than a simple messages + intelligence_items table
- Entity versioning adds write complexity for extraction workers
- Building the full entity model requires Layer 2 to be implemented first

## Relationship to Current Schema

The current schema (messages, telegram_sync_state, telegram_backfill_jobs, processing_jobs, media_files, intelligence_items) maps approximately as follows:

- messages → ingestion_events (future canonical) + messages (Telegram-specific)
- media_files → raw_artifacts (to be superseded)
- processing_jobs → extraction_jobs (to be unified with lease pattern)
- intelligence_items → intelligence output tables (tasks/decisions/etc.) once ai_runs is added

No existing data needs to be deleted for this migration — the canonical tables are additive.
