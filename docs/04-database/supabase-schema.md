# Supabase Schema

RUMMAN's operational data spine. PostgreSQL + pgvector. Project ID: `yriavgczteuirigsvedu`.

All migrations live in `rumman-core/supabase/migrations/`. Apply via Supabase SQL Editor.

*Last updated: 2026-06-02 — reflects all 34 migrations 001–034 (Phase 2 complete).*

---

## Schema Overview

```
Ingestion Layer          Knowledge Layer          Institutional Layer
─────────────────        ─────────────────        ─────────────────
messages                 document_chunks          inst_colleges
telegram_sync_state      source_documents         inst_specializations
telegram_backfill_jobs   media_files              inst_courses
processing_jobs          intelligence_items
                         message_signals          Platform Layer
                         course_intelligence_     tenants
                           profiles               rumman_users
                         exam_intelligence        rumman_sessions

Observability Layer      Student Layer
─────────────────        ─────────────────
learning_events          student_context
ai_runs                  academic_calendar
worker_heartbeats
analysis_runs
gap_items
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
| `embedding` | VECTOR(1536) | text-embedding-3-large output (1536 dimensions, set in migration 003) |
| `course_code` | TEXT | Detected/assigned course code |
| `source_type` | TEXT | Inherited from source_document |
| `language` | TEXT | 'ar', 'en' |

**pgvector index:** `ivfflat` on `embedding` column.
**RPC function:** `match_documents(query_embedding, match_threshold, match_count, filter_tenant)` — originally defined in migration 005, updated with authority tier filtering in migration 022, fixed with metadata JSONB return in migration 034. Current signature returns `metadata` JSONB column alongside chunk data.

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

### ~~`query_logs`~~ — DROPPED in migration 016
Originally tracked search and synthesis queries. Replaced by `learning_events` (see Observability Layer) which provides the same analytics with a more general event model. Any query referencing `query_logs` will fail — use `learning_events` with `event_type = 'query'` or `event_type = 'synthesis'`.

---

## Institutional Layer (SEU)

⚠️ **TABLE NAME CHANGE:** `seu_colleges`, `seu_specializations`, `seu_courses` were renamed to `inst_colleges`, `inst_specializations`, `inst_courses` in migration 014 to support multi-tenancy. Use the `inst_*` names in all new queries.

### `inst_colleges` *(formerly `seu_colleges`)*
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

**Current state:** 5 colleges seeded.

---

### `inst_specializations` *(formerly `seu_specializations`)*
SEU program/major data. One row per specialization.

**Key columns:**
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID | |
| `college_id` | UUID | FK → inst_colleges |
| `code` | TEXT | 'BSCS', 'MGT', 'MBA', 'MCS', etc. |
| `name_ar` | TEXT | |
| `name_en` | TEXT | |
| `total_credits` | INT | Required credits for graduation |
| `num_levels` | INT | Number of study levels (semesters) |

**Current state:** 21 specializations (13 bachelor's + 8 graduate).

---

### `inst_courses` *(formerly `seu_courses`)*
SEU course catalog. One row per course.

**Key columns:**
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID | |
| `specialization_id` | UUID | FK → inst_specializations |
| `code` | TEXT | e.g. 'IT362', 'MGT311' |
| `name_ar` | TEXT | Arabic name |
| `name_en` | TEXT | English name |
| `credit_hours` | INT | |
| `level` | INT | Study level (1-8) |
| `prerequisites` | TEXT[] | Course codes required before enrollment |
| `description` | TEXT | Course description (seeded for ~161 courses) |

**Current state:** 161 courses seeded with names and descriptions. Run `scripts/seed_courses.py` to refresh.

---

## Platform Layer

### `tenants`
Multi-tenant isolation. One row per university.

**Current state:** 1 tenant — SEU (`00000000-0000-0000-0000-000000000001`).

---

### `rumman_users` *(formerly `users`)*
Platform users. Pseudonymized via `RUMMAN_USER_SALT`.

**Key design:** `platform_user_hash = SHA256(RUMMAN_USER_SALT + ":" + platform + ":" + raw_id)`. Raw Telegram chat_id is NEVER stored. Privacy-by-design.

**Written by:** `search_api.py` (`POST /v1/users/identify`)

---

### `rumman_sessions` *(formerly `sessions`)*
Per-session state. Tracks active course focus, enrolled courses, conversation context.

**TTL:** 30 minutes of inactivity. After TTL, next interaction creates a new session.

**Key columns:** `active_course_code`, `active_exam_type`, `session_context` (JSONB), `turn_count`

---

### `student_context`
Persistent cross-session memory for each student.

**Context types:** `enrolled_courses` (explicit, never expires), `active_focus` (7 days), `lang_pref` (30 days), `study_pattern` (30 days).

**Confidence tiers:** high (explicit), medium (3+ observations), low (1-2 observations).

---

## Intelligence Layer (Phase 2 Tables)

### `intelligence_items`
Structured operational items extracted from Telegram messages by `intelligence_worker.py`.

**Item types:** assignment, quiz, exam, deadline, meeting, decision, reminder, announcement

**Dedup constraint:** UNIQUE(tenant_id, source_platform, source_message_id, item_type)

**Written by:** `app/intelligence_worker.py`

---

### `extracted_items`
Operational items extracted by `daily_brief.py` (sliding window, on-demand runs). Distinct from `intelligence_items` which is populated by the continuous worker.

**Validity:** Items have `valid_until` date. Expired items are preserved but not surfaced.

**Supersession:** `superseded_by` FK rather than deletion — preserves audit trail.

---

### `message_signals`
Typed intelligence signals extracted from Telegram conversations (batch, not real-time).

**Signal types:** `exam_emphasis`, `difficulty`, `professor_note`, `resource_rec`, `confusion_cluster`

**Injection:** Top signals for a course are included in the synthesis context block.

**Written by:** `scripts/message_signal_worker.py`

---

### `course_intelligence_profiles`
Pre-computed per-course corpus summary.

**Key columns:** `total_chunks`, `exam_chunks`, `official_chunks`, `coverage_level` (none/thin/moderate/strong)

**Refresh:** `scripts/refresh_course_profiles.py` — pure SQL aggregation, no LLM.

---

### `exam_intelligence`
Top recurring exam topics per (course_code, exam_type), LLM-extracted from exam-tagged chunks.

**Refresh:** Monthly or after significant new exam content ingested.

**Injection:** Top topics injected into synthesis context to help calibrate exam preparation answers.

---

### `academic_calendar`
SEU's official academic dates (1447H / 2025–2026).

**Events:** semester_start/end, add_drop_start/end, midterm_start/end, final_start/end, withdrawal_deadline, results_release.

**Injection:** When student asks "when is the exam?", calendar is injected as a synthetic chunk with similarity=0.99 (effectively pinned to top of results).

---

## Observability Layer

### `ai_runs`
Audit trail for every AI API call.

**Key columns:** `worker`, `model`, `input_tokens`, `output_tokens`, `cost_usd`, `duration_ms`, `subject_type`, `subject_id`

**NO raw content, NO PII stored.**

**Business value:** Daily spend = `SUM(cost_usd) WHERE DATE(created_at) = TODAY`. Provenance chain for every attribution.

**Written by:** `attribution_worker.py`, `intelligence_worker.py`, `daily_brief.py`, any AI worker.

---

### `learning_events`
Every student interaction is a learning event.

**Event types:** query, synthesis, zero_result, feedback_positive, feedback_negative, session_start, session_end

**Key analytics:**
- Zero-result rate: `COUNT WHERE event_type='zero_result' / COUNT WHERE event_type='query'`
- Latency p95: percentile on `latency_ms WHERE event_type='synthesis'`

---

### `worker_heartbeats`
Worker liveness monitoring. Each worker upserts a row every 60 seconds.

**Alert threshold:** `last_seen_at < now() - 5 minutes` = worker likely dead.

---

### `analysis_runs`
Append-only log of batch analyst operations (gap_analyst, qa_miner, message_signal_miner).

**Never updated, never deleted.** Full audit trail of when each analysis ran and what it cost.

---

### `gap_items`
Normalized knowledge gap records from `gap_analyst.py` runs.

**Gap types:** `content_gap` (similarity < 0.20), `retrieval_gap` (0.20–0.40), `coverage_gap` (> 0.40)

**Resolution tracking:** `resolved_at`, `resolved_by`. Gaps are marked resolved, not deleted.

---

## Migration History

| # | File | Key Purpose |
|---|---|---|
| 001 | `001_daily_brief_tables.sql` | Daily brief infrastructure, ai_runs, extracted_items |
| 002 | `002_processing_jobs_retry_count.sql` | Retry count on processing_jobs |
| 003 | `003_knowledge_layer.sql` | source_documents, document_chunks, pgvector |
| 004 | `004_media_lifecycle.sql` | media_files table |
| 005 | `005_match_documents_rpc.sql` | match_documents() pgvector RPC |
| 006 | `006_query_intelligence.sql` | query_logs, feedback |
| 007 | `007_platform_foundations.sql` | tenants, users, sessions, events |
| 008 | `008_curriculum_foundations.sql` | seu_colleges, seu_specializations, seu_courses |
| 009 | `009_curriculum_graduate_and_remapping.sql` | 8 graduate specializations + course re-mapping |
| 010 | `010_source_authority.sql` | `source_authority` tier column on document_chunks (official/verified/community) |
| 011 | `011_intelligence_layer.sql` | `intelligence_items` table — structured operational items from messages |
| 012 | `012_messages_tenant_id.sql` | `tenant_id` backfill on `messages` |
| 013 | `013_embedding_model.sql` | `embedding_model` column on document_chunks |
| 014 | `014_rename_seu_to_inst.sql` | Rename `seu_*` tables to `inst_*` for multi-tenancy |
| 015 | `015_claim_model_and_authority.sql` | Claim model columns (`machine_asserted`, `confidence_tier`) |
| 016 | `016_temporal_and_ops.sql` | `learning_events` table; `query_logs` and `feedback` DROPPED |
| 017 | `017_academic_calendar_1447h.sql` | `academic_calendar` table, SEU 1447H dates seeded |
| 018 | `018_drop_legacy_courses_table.sql` | Drop legacy courses table superseded by `inst_courses` |
| 019 | `019_fix_intelligence_items.sql` | Fix dedup constraint on `intelligence_items` |
| 020 | `020_drop_seu_compat_views.sql` | Drop backward-compat `seu_*` views |
| 021 | `021_ai_runs_defaults.sql` | Defaults and constraints on `ai_runs` |
| 022 | `022_match_documents_authority_tier.sql` | Update `match_documents()` with authority tier filtering |
| 023 | `023_worker_heartbeats.sql` | `worker_heartbeats` table for liveness monitoring |
| 024 | `024_course_names_bulk.sql` | Bulk-seed Arabic course names into `inst_courses` |
| 025 | `025_claim_model_temporal_and_contradiction.sql` | `active_extracted_items` and `active_document_chunks` views; supersession columns |
| 026 | `026_analysis_runs.sql` | `analysis_runs` + `gap_items` tables for gap analyst |
| 027 | `027_document_chunks_metadata.sql` | `metadata` JSONB column on document_chunks |
| 028 | `028_self_healing_ingestion.sql` | Self-healing backfill and retry improvements |
| 029 | `029_fix_intelligence_items_dedup.sql` | Fix dedup logic on intelligence_items |
| 030 | `030_student_context.sql` | `student_context` table — persistent cross-session student memory |
| 031 | `031_course_intelligence_profiles.sql` | `course_intelligence_profiles` + `exam_intelligence` tables |
| 032 | `032_message_signals.sql` | `message_signals` table — typed signals from Telegram messages |
| 033 | `033_backfill_tenant_id.sql` | Backfill missing `tenant_id` values across tables |
| 034 | `034_match_documents_fix.sql` | Fix `match_documents()` — add `filter_tenant` UUID parameter, return `metadata` JSONB |
