# Knowledge Layer Overview

## What the Knowledge Layer Is

The Knowledge Layer (Layer 2) is the bridge between raw data (Layer 1) and intelligence (Layer 3).

It takes raw artifacts — audio files, images, PDFs, messages, documents — and transforms them into structured, queryable, semantically indexed knowledge that the Intelligence Layer can reason over.

Layer 2 does not reason. It extracts, normalizes, chunks, embeds, and graphs. The intelligence is in Layer 3. The knowledge is in Layer 2.

---

## Why a Dedicated Layer

Without a Knowledge Layer, intelligence workers read raw Telegram messages directly and try to extract meaning from noisy, unstructured text. This is what `intelligence_worker.py` currently does (when enabled). It is fragile because:

- A voice note is unusable without transcription
- A PDF contains no extractable text until it is parsed
- A scanned document is opaque without OCR
- Even a text message needs language detection, deduplication, and normalization before reliable extraction

The Knowledge Layer is the normalization pipeline. All sources — regardless of modality — produce the same `knowledge_artifact` shape that Layer 3 consumes. This makes intelligence workers modality-agnostic.

---

## Processing Pipeline Per Modality

### Audio (voice notes, audio messages)
1. raw_artifact (ogg/mp3/m4a) stored in Supabase Storage
2. transcription_worker → calls OpenAI Whisper (or equivalent) → produces knowledge_artifact with content_type=transcript
3. chunking_worker → splits transcript into knowledge_chunks (paragraph or sentence level)
4. embedding_worker → embeds each chunk → stores in embeddings table
5. entity_worker → extracts named entities from transcript → stores in entities

Current state: steps 1 and 2 are partially implemented in audio_worker.py but embedded in Layer 1 infrastructure and without Layer 2 tables.

### Images and Screenshots
1. raw_artifact (jpg/png/webp) stored in Supabase Storage
2. vision_worker → calls GPT-4o Vision or open-source OCR (pytesseract, PaddleOCR as first pass) → produces knowledge_artifact with content_type=ocr_text
3. Continue as audio above (chunking, embedding, entity extraction)

Two-pass OCR strategy: attempt open-source OCR first (free, fast, good for clean printed text). Escalate to GPT Vision only when confidence is below threshold. This controls cost significantly.

### PDF Documents
1. raw_artifact (pdf) stored in Supabase Storage
2. document_worker → text extraction layer (PyMuPDF/pdfplumber for digital PDFs) → OCR layer for scanned pages → produces knowledge_artifact with content_type=parsed_document
3. May produce multiple knowledge_chunks representing document sections, tables, and figures separately
4. Continue as above

### Spreadsheets and Structured Files
1. raw_artifact stored in Supabase Storage
2. structured_worker → parse tables, headers, formulas → produce knowledge_artifact with content_type=structured_data and structured_json field populated
3. Structured data may have a different chunking strategy (per table, per sheet)

### Text Messages
1. No binary raw_artifact
2. knowledge_artifact created directly from ingestion_event.message_text (content_type=message_text)
3. Short messages may be grouped into knowledge_chunks (conversation window chunking)
4. Continue as above

---

## Canonical Output: knowledge_artifact

Every processing pipeline produces a knowledge_artifact. The schema is consistent regardless of source modality.

Key fields:
- `content_type`: transcript / ocr_text / parsed_document / structured_data / message_text
- `extracted_text`: the normalized text content (or pointer to Supabase Storage for large texts)
- `structured_json`: optional, for tabular/structured data
- `extraction_model`: which model or tool produced this artifact
- `extraction_version`: versioned — re-extraction creates a new version, does not overwrite
- `source_raw_artifact_id`: traceability back to the original binary
- `source_ingestion_event_id`: traceability back to the original event
- `tenant_id`: mandatory, enforced at query level

---

## Replayability

All Layer 2 operations are replayable by design:
- Raw artifacts are immutable in Supabase Storage
- Extraction jobs are re-enqueueable
- knowledge_artifacts are versioned — old extractions are not deleted
- Embeddings reference extraction_version — old embeddings remain valid until explicitly migrated

If a better OCR model becomes available, the pipeline can be re-run for all raw_artifacts of type image/pdf, producing new knowledge_artifact versions and new embeddings, without touching source data.

---

## Tenant Isolation in Layer 2

Every Layer 2 entity carries `tenant_id`:
- knowledge_artifacts: tenant_id
- knowledge_chunks: tenant_id
- embeddings: tenant_id (enforced in all similarity queries)
- entities: tenant_id

Cross-tenant queries are prohibited at the application layer. RLS policies (when implemented) will enforce this at the database layer.

Storage paths are tenant-scoped: `{tenant_id}/{source_id}/{artifact_id}.{ext}`. No cross-tenant Storage access is possible through the path structure.

---

## Entity Graph

The entity graph is a Layer 2 output consumed by Layer 3.

**entities** stores named objects extracted from knowledge artifacts: people, organizations, projects, locations, events, dates, systems, documents.

**entity_relationships** stores typed edges: "person A is member of organization B", "project C has deadline D", "document E was authored by person F".

The entity graph is not a separate graph database. It is Postgres tables. For most operational knowledge graphs (hundreds to thousands of entities per tenant), Postgres with proper indexing handles graph traversal queries adequately. A dedicated graph database should be considered only when multi-hop traversal queries become a measured bottleneck.

---

## What Layer 2 Does NOT Do

- Does not produce operational items (tasks, decisions, deadlines) — that is Layer 3
- Does not reason about the content it processes — it extracts and structures
- Does not call the intelligence layer — it only enqueues intelligence_jobs when extraction is complete
- Does not access source platforms directly — it reads from raw_artifacts in Supabase Storage

---

## Current Status and Phase Gate

Layer 2 does not yet exist in code. The audio_worker.py contains a partial transcription implementation that will be the starting point for the knowledge_artifact pipeline.

Layer 2 must be operational before Layer 3 is enabled. The specific gate conditions are documented in ADR-0005.

The first Layer 2 milestone is: audio transcription produces a `knowledge_artifact` row (not just an `extracted_text` column in `media_files`) with proper traceability, versioning, and tenant_id.
