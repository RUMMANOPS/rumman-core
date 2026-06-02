# Supabase Migrations

Schema changes for RUMMAN's Supabase database. 34 migrations total (001–034).

## How to apply a migration

1. Open the Supabase dashboard → SQL Editor → New query
2. Paste the contents of the migration file
3. Run it
4. Verify the tables exist before running the affected worker

## Migrations

| File | Tables / columns | Required by |
|---|---|---|
| `001_daily_brief_tables.sql` | `brief_runs`, `extracted_items`, `ai_runs` | `app/daily_brief.py` |
| `002_processing_jobs_retry_count.sql` | `processing_jobs.retry_count` | `app/telegram_download_worker.py` |
| `003_knowledge_layer.sql` | `source_documents`, `document_chunks` (VECTOR 1536), pgvector extension | `app/embed_worker.py` |
| `004_media_lifecycle.sql` | `media_files` | `app/telegram_download_worker.py` |
| `005_match_documents_rpc.sql` | `match_documents()` pgvector RPC | `app/search_api.py` |
| `006_query_intelligence.sql` | `query_logs`, `feedback` (both DROPPED in 016 — do not reference) | superseded |
| `007_platform_foundations.sql` | `tenants`, `users`, `sessions`, `events` | all workers |
| `008_curriculum_foundations.sql` | `seu_colleges`, `seu_specializations`, `seu_courses` (renamed inst_* in 014) | `scripts/seed_courses.py` |
| `009_curriculum_graduate_and_remapping.sql` | 8 graduate specializations, course remapping | `scripts/seed_courses.py` |
| `010_source_authority.sql` | `document_chunks.source_authority` tier column | `app/embed_worker.py`, `app/search_api.py` |
| `011_intelligence_layer.sql` | `intelligence_items` | `app/intelligence_worker.py` |
| `012_messages_tenant_id.sql` | `messages.tenant_id` (backfill) | `app/rumman_engine.py` |
| `013_embedding_model.sql` | `document_chunks.embedding_model` | `app/embed_worker.py` |
| `014_rename_seu_to_inst.sql` | `seu_*` → `inst_*` table renames | all institutional queries |
| `015_claim_model_and_authority.sql` | `machine_asserted`, `confidence_tier` columns | `app/attribution_worker.py` |
| `016_temporal_and_ops.sql` | `learning_events` (new); `query_logs` + `feedback` DROPPED | `app/search_api.py` |
| `017_academic_calendar_1447h.sql` | `academic_calendar` table, 1447H dates seeded | `app/search_api.py` |
| `018_drop_legacy_courses_table.sql` | Drop legacy courses table | — |
| `019_fix_intelligence_items.sql` | Fix dedup constraint on `intelligence_items` | `app/intelligence_worker.py` |
| `020_drop_seu_compat_views.sql` | Drop backward-compat `seu_*` views | — |
| `021_ai_runs_defaults.sql` | Defaults/constraints on `ai_runs` | all AI workers |
| `022_match_documents_authority_tier.sql` | Update `match_documents()` with authority tier filter | `app/search_api.py` |
| `023_worker_heartbeats.sql` | `worker_heartbeats` liveness table | all workers |
| `024_course_names_bulk.sql` | Bulk Arabic course names in `inst_courses` | — |
| `025_claim_model_temporal_and_contradiction.sql` | `active_extracted_items` view, `active_document_chunks` view, supersession columns | `app/search_api.py` |
| `026_analysis_runs.sql` | `analysis_runs`, `gap_items` | `scripts/gap_analyst.py` |
| `027_document_chunks_metadata.sql` | `document_chunks.metadata` JSONB column | `app/embed_worker.py`, `app/search_api.py` |
| `028_self_healing_ingestion.sql` | Self-healing backfill improvements | `app/telegram_backfill_worker.py` |
| `029_fix_intelligence_items_dedup.sql` | Fix dedup logic on intelligence_items | `app/intelligence_worker.py` |
| `030_student_context.sql` | `student_context` persistent cross-session memory | `app/search_api.py` |
| `031_course_intelligence_profiles.sql` | `course_intelligence_profiles`, `exam_intelligence` | `app/search_api.py` |
| `032_message_signals.sql` | `message_signals` typed signals | `scripts/message_signal_worker.py` |
| `033_backfill_tenant_id.sql` | Backfill missing `tenant_id` values | — |
| `034_match_documents_fix.sql` | Fix `match_documents()` — `filter_tenant` UUID param, `metadata` JSONB return | `app/search_api.py` |

## Why not Supabase CLI

The service-role key is not in the repository `.env`. Migrations are applied manually via the SQL editor.

Once the service-role key is added to `.env`, switch to:
```bash
supabase db push
```

## Schema-as-code rule

Every schema change must go through a migration file here before being applied. Do not create or alter tables directly in the Supabase UI without a corresponding file in this directory.

This rule exists because schema changes applied only through the UI are invisible to git, unreviewable, and unreproducible. See ADR-0003.
