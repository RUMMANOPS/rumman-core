# ADR-0007: Storage Architecture

## Status

Accepted

## Context

As RUMMAN expands to multimodal ingestion (audio, images, PDFs, documents, spreadsheets), the storage architecture must accommodate three distinct content types:

1. Structured metadata and operational state (relational)
2. Semantic vector representations for retrieval (vector/ANN)
3. Raw binary artifacts (immutable object storage)

Confusing these three content types into one storage system creates correctness problems (binary in Postgres columns), cost problems (Postgres row size bloat), and scaling problems (vector ANN in a relational index).

## Decision

RUMMAN uses exactly three storage systems. Each has a defined content type and usage boundary.

---

### Postgres (Supabase)

**Content:** All structured data — metadata, job state, entities, operational items, tenant management, audit trails, sync state, embeddings table.

**Why Postgres:**
- Already the operational database
- Supabase provides PostgREST, RLS, and pgvector in one managed service
- All existing workers use it for coordination
- Relational queries, joins, and transactions are native
- Handles millions of rows per table without issue at RUMMAN's current and near-term scale

**What goes here:**
- All Layer 1 tables (messages, raw_artifacts metadata, jobs, sync state, tenants, sources)
- All Layer 2 tables (knowledge_artifacts, knowledge_chunks, entities, entity_relationships)
- embeddings table (pgvector column — see below)
- All Layer 3 tables (ai_runs, tasks, decisions, deadlines, memories, insights)

**What does NOT go here:**
- Binary files (audio, images, PDFs, video)
- Large extracted text blobs (OCR output for a 200+ page document)
- Raw Telegram message JSON at scale (the raw_json column in messages will eventually need archival)

**Access pattern:** Direct PostgREST API for workers using service-role key. RLS policies for tenant-scoped read paths (future dashboard/API). Bulk operations via direct Postgres connection when PostgREST rate limits become constraining.

---

### pgvector (Supabase extension — within Postgres)

**Content:** Embedding vectors for semantic search over knowledge_chunks.

**Why pgvector, not a dedicated vector DB:**
- Supabase includes pgvector as an extension — zero operational overhead
- At RUMMAN's scale (single organization to thousands of chunks per tenant), pgvector ANN performance is adequate
- Filtered ANN queries (`WHERE tenant_id = ?` + `ORDER BY embedding <=> query_vector LIMIT k`) are supported with IVFFlat indexes
- Migration to a dedicated vector service (Pinecone, Weaviate, Qdrant) can happen when pgvector is the measured bottleneck — likely not before millions of chunks per tenant

**Invariant:** Every `k-NN` query against the embeddings table must include `WHERE tenant_id = ?`. This is not optional. Cross-tenant similarity search is a privacy violation.

**Future consideration:** If tenant count grows large and per-tenant embedding volume grows large, consider per-tenant embedding tables or schemas to avoid index scan contention. This is a scaling optimization, not a Day 1 requirement.

---

### Supabase Storage (S3-compatible object storage)

**Content:** Raw binary artifacts — audio files, images, PDFs, videos, documents, spreadsheets.

**Why object storage, not Postgres:**
- Binary blobs in Postgres degrade query performance and table compaction
- Object storage is designed for immutable large files with cheap storage and controlled egress
- Supabase Storage is S3-compatible, co-located with the database, and supports bucket-level access policies

**Path structure (enforced):**
```
{tenant_id}/{source_id}/{year}/{month}/{artifact_id}.{ext}
```

**Immutability rule:** Raw artifacts are written once and never modified. Reprocessing reads from Storage but does not overwrite — extraction outputs go to Postgres (knowledge_artifacts), not back to Storage.

**Tenant isolation in Storage:**
- Bucket-level or path-prefix-level RLS policies enforce tenant boundaries
- Worker processes must construct paths using the artifact's tenant_id — the path is the isolation mechanism
- Presigned URL generation must enforce tenant boundary: a tenant must never receive a presigned URL for another tenant's artifact path

**Large extracted text:** OCR output for a 200-page document, or a transcript of a 3-hour meeting, should also be stored in Supabase Storage rather than as a text column in knowledge_artifacts. The knowledge_artifacts row stores a storage_path pointer, not the full text inline.

---

## Storage Allocation Decision Table

| Content type | Storage system | Reason |
|---|---|---|
| Normalized message metadata | Postgres | Relational, queryable |
| Job state, leases, sync checkpoints | Postgres | Transactional, consistent |
| Entity graph | Postgres | Relational joins, foreign keys |
| Operational items (tasks/decisions) | Postgres | Structured, tenant-scoped |
| Audit trails (ai_runs) | Postgres | Immutable, relational |
| Embedding vectors | pgvector (Postgres) | ANN search, co-located |
| Knowledge chunks (text) | Postgres | Small-to-medium, relational |
| Audio files | Supabase Storage | Binary, immutable |
| Images | Supabase Storage | Binary, immutable |
| PDFs | Supabase Storage | Binary, immutable |
| Video files | Supabase Storage | Binary, large, immutable |
| Long OCR/transcript text | Supabase Storage | Large text, pointer in Postgres |
| Raw Telegram message JSON (archive) | Supabase Storage (future) | Archival, row size control |

---

## Anti-Patterns (explicitly prohibited)

- Storing binary files in Postgres bytea or text columns
- Generating cross-tenant presigned URLs
- ANN vector search without tenant_id filter
- Storing large text extraction results (>50KB) directly in Postgres text columns
- Writing to raw_artifact Storage paths after initial upload (immutability violation)
- Using a separate external vector database (Pinecone, Weaviate, Qdrant) before pgvector is the measured bottleneck

## Cost Considerations

**Supabase Storage egress:** Every time a worker downloads a file from Storage for processing, egress is charged. Files should be processed once, immediately after upload, not downloaded repeatedly. Extraction results go to Postgres, not back to repeated Storage reads.

**Embedding cost:** Embedding generation (OpenAI text-embedding-3-small or equivalent) is charged per token. Store embedding_model and embedding_version on every embeddings row. When the embedding model changes, re-embedding is a versioned offline migration job — all existing embeddings remain valid for their model version until explicitly migrated.

**raw_json archival:** The messages.raw_json column stores full Telegram message dicts. At high volume, this is the dominant Postgres storage cost. A future archival job should move old raw_json values to Supabase Storage and replace the column value with a storage_path pointer.
