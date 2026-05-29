# Supabase Schema

RUMMAN's operational data spine. PostgreSQL + pgvector. Project ID: `yriavgczteuirigsvedu`.

All migrations live in `rumman-core/supabase/migrations/`. Apply via Supabase SQL Editor.

*Last updated: 2026-05-29 — reflects migrations 001–009*

---

## Schema Overview

```
Ingestion Layer          Intelligence Layer       Institutional Layer
─────────────────        ─────────────────        ─────────────────
messages                 document_chunks          seu_colleges
telegram_sync_state      source_documents         seu_specializations
telegram_backfill_jobs   media_files              seu_courses
processing_jobs          query_logs
                         feedback                 Platform Layer
                                                  tenants
                                                  users
                                                  sessions
```

---

## Ingestion Layer

### `messages`
Canonical store for all ingested Telegram messages.

**Key columns:**
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID | FK → tenants |
| `platform_chat_id` | BIGINT | Telegram chat ID |
| `platform_message_id` | BIGINT | Telegram message ID |
| `platform_user_id` | BIGINT | Sender user ID |
| `message_text` | TEXT | Extracted message text |
| `message_type` | TEXT | 'text', 'audio', 'photo', 'document', etc. |
| `message_date` | TIMESTAMPTZ | When message was sent |
| `has_media` | BOOL | Whether message has downloadable media |
| `media_job_id` | UUID | FK → processing_jobs if media queued |
| `raw_json` | JSONB | Full Telegram message object |

**Unique constraint:** `(platform_chat_id, platform_message_id)` — dedup key for backfill.

**Written by:** `rumman_engine.py` (live), `telegram_backfill_worker.py` (historical)
**Read by:** `daily_brief.py`, `intelligence_worker.py` (when enabled)

---

### `telegram_sync_state`
One row per Telegram chat. Tracks ingestion progress.

**Key columns:**
| Column | Type | Notes |
|---|---|---|
| `platform_chat_id` | BIGINT PK | |
| `chat_name` | TEXT | |
| `newest_message_id` | BIGINT | Highest seen message ID |
| `oldest_message_id` | BIGINT | Lowest seen message ID (backfill progress) |
| `total_messages_seen` | INT | Running count |
| `backfill_completed` | BOOL | True when backfill reached oldest message |

**Written by:** `rumman_engine.py`, `telegram_backfill_worker.py`

---

### `telegram_backfill_jobs`
Queue for historical backfill operations. One row per group being backfilled.

**Key columns:**
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID | |
| `chat_id` | BIGINT | Target Telegram group |
| `status` | TEXT | 'pending', 'running', 'completed', 'failed' |
| `worker_id` | TEXT | Which worker holds current lease |
| `lease_expires_at` | TIMESTAMPTZ | Stale lease detection |
| `heartbeat_at` | TIMESTAMPTZ | Last activity timestamp |
| `retry_count` | INT | Failure counter |
| `total_processed` | INT | Messages processed so far |
| `last_processed_message_id` | BIGINT | Resume checkpoint |
| `oldest_message_id` | BIGINT | Target: stop when reached |

**Written by:** `telegram_backfill_worker.py`, `scripts/create_backfill_jobs.py`

---

### `processing_jobs`
Generic async work queue. All workers poll this table for their job types.

**Key columns:**
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID | |
| `job_type` | TEXT | 'audio_transcribe', 'telegram_media', 'embed_chunk', 'pdf_extract' |
| `status` | TEXT | 'pending', 'running', 'completed', 'failed' |
| `payload` | JSONB | Job-type-specific data |
| `retry_count` | INT | Added in migration 002 |
| `created_at` | TIMESTAMPTZ | |

**Job types → workers:**
| job_type | Processed by |
|---|---|
| `audio_transcribe` | `telegram_download_worker.py` |
| `telegram_media` | `telegram_download_worker.py` |
| `embed_chunk` | `embed_worker.py` |
| `pdf_extract` | `pdf_worker.py` |

**Written by:** `rumman_engine.py`, `telegram_backfill_worker.py`, `ingest_document.py`

---

## Intelligence Layer

### `source_documents`
Uploaded/ingested official files. One row per document file.

**Key columns:**
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID | |
| `file_name` | TEXT | |
| `file_path` | TEXT | Path within Supabase Storage bucket |
| `content_hash` | TEXT | SHA256 of file content (dedup) |
| `source_type` | TEXT | 'study_plan', 'regulation', 'exam', 'course_description', etc. |
| `course_code` | TEXT | Optional — if document is course-specific |
| `language` | TEXT | 'ar', 'en' |
| `status` | TEXT | 'pending', 'extracted', 'embedded', 'failed' |
| `extracted_text` | TEXT | Output from pdf_worker |

**Written by:** `scripts/ingest_document.py`
**Read by:** `pdf_worker.py`, `embed_worker.py`

---

### `document_chunks`
The retrieval corpus. pgvector embeddings of all extracted text.

**Key columns:**
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID | |
| `source_document_id` | UUID | FK → source_documents (NULL for Telegram-origin chunks) |
| `chunk_text` | TEXT | Chunk content |
| `chunk_index` | INT | Position within document |
| `embedding` | VECTOR(3072) | text-embedding-3-large output |
| `course_code` | TEXT | Detected/assigned course code |
| `source_type` | TEXT | Inherited from source_document |
| `language` | TEXT | 'ar', 'en' |

**pgvector index:** `ivfflat` on `embedding` column.
**RPC function:** `match_documents(query_embedding, match_threshold, match_count, tenant_id)` — defined in migration 005.

⚠️ **Known gap:** No `source_authority` field to distinguish official docs from community uploads. Migration 010 will add this.

**Written by:** `embed_worker.py`, `scripts/seed_courses.py` (course descriptions)
**Read by:** `search_api.py` via `match_documents` RPC

---

### `media_files`
Audio transcription results and media metadata.

**Key columns:**
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID | |
| `source_message_id` | UUID | FK → messages |
| `file_type` | TEXT | 'audio', 'image', 'document' |
| `transcription` | TEXT | Audio transcript or OCR output |
| `file_path` | TEXT | Storage path |
| `duration_seconds` | INT | Audio files |

**Written by:** `telegram_download_worker.py`

---

### `query_logs`
All search and synthesis queries. Used for observability and future analytics.

**Key columns:**
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID | |
| `session_id` | UUID | FK → sessions |
| `query_text` | TEXT | Raw student query |
| `normalized_query` | TEXT | After normalization pipeline |
| `detected_intent` | TEXT | Output of gpt-4o-mini classifier |
| `detected_course_code` | TEXT | |
| `result_count` | INT | Chunks returned |
| `synthesis_tokens` | INT | Tokens used for synthesis |
| `response_time_ms` | INT | End-to-end latency |
| `model_used` | TEXT | e.g. 'gpt-4o-mini' |

---

### `feedback`
Student feedback on bot responses.

**Key columns:**
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `query_log_id` | UUID | FK → query_logs |
| `rating` | INT | 1-5 or thumbs up/down |
| `comment` | TEXT | Optional free text |

---

## Institutional Layer (SEU)

### `seu_colleges`
SEU college master data. One row per college.

**Key columns:**
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID | Always SEU tenant ID |
| `code` | TEXT | 'COMP', 'ADMIN', 'HEALTH', 'THEO', 'GENERAL', 'APPLIED' |
| `name_ar` | TEXT | Arabic name |
| `name_en` | TEXT | English name |
| `telegram_chat_ids` | BIGINT[] | Group IDs being ingested for this college |

**Current state:** 5 colleges seeded (migration 008).

---

### `seu_specializations`
SEU program/major data. One row per specialization.

**Key columns:**
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID | |
| `college_id` | UUID | FK → seu_colleges |
| `code` | TEXT | 'BSCS', 'MGT', 'MBA', 'MCS', etc. |
| `name_ar` | TEXT | |
| `name_en` | TEXT | |
| `total_credits` | INT | Required credits for graduation |
| `num_levels` | INT | Number of study levels (semesters) |

**Current state:** 21 specializations (13 bachelor's + 8 graduate). Migration 008 + 009.

---

### `seu_courses`
SEU course catalog. One row per course.

**Key columns:**
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID | |
| `specialization_id` | UUID | FK → seu_specializations |
| `code` | TEXT | e.g. 'IT362', 'MGT311' |
| `name_ar` | TEXT | ⚠️ NULL for all 157 courses — not yet seeded |
| `name_en` | TEXT | ⚠️ NULL for all 157 courses — not yet seeded |
| `credit_hours` | INT | |
| `level` | INT | Study level (1-8) |
| `prerequisites` | TEXT[] | Course codes required before enrollment |
| `description` | TEXT | ⚠️ NULL for most courses |

**Current state:** 157 courses, all mapped to specializations. Run `scripts/seed_courses.py` to populate names.

---

## Platform Layer

### `tenants`
Multi-tenant isolation. One row per university.

**Current state:** 1 tenant — SEU (`00000000-0000-0000-0000-000000000001`).

---

### `users`
Platform users. Pseudonymized via `RUMMAN_USER_SALT`.

---

### `sessions`
Bot session tracking. Session = one student interaction window.

---

## Migration History

| # | File | Purpose |
|---|---|---|
| 001 | `001_daily_brief_tables.sql` | Daily brief infrastructure |
| 002 | `002_processing_jobs_retry_count.sql` | Retry count on processing_jobs |
| 003 | `003_knowledge_layer.sql` | source_documents, document_chunks, embeddings |
| 004 | `004_media_lifecycle.sql` | media_files table |
| 005 | `005_match_documents_rpc.sql` | match_documents() pgvector RPC |
| 006 | `006_query_intelligence.sql` | query_logs, feedback |
| 007 | `007_platform_foundations.sql` | tenants, users, sessions, events |
| 008 | `008_curriculum_foundations.sql` | seu_colleges, seu_specializations, seu_courses |
| 009 | `009_curriculum_graduate_and_remapping.sql` | 8 graduate specializations + course re-mapping |

**Pending:**
- Migration 010: Add `source_authority` to `document_chunks`
