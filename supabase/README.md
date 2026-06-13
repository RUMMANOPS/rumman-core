# Supabase Migrations

Schema changes for RUMMAN's Supabase database. 58 migrations total (001–058).

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
| `035_exam_questions.sql` | `exam_questions` table — extracted questions from past exams | `app/question_extraction_worker.py` |
| `036_knowledge_graph_core.sql` | `kg_topics`, `kg_topic_aliases`, `kg_faculty`, `kg_syllabi`, `kg_chapters`, `kg_provenance_edges` | knowledge graph workers |
| `037_faculty_registry.sql` | `kg_faculty` extensions — teaching history, course assignments | `app/search_api.py` |
| `038_question_clusters.sql` | `question_clusters` — exam question grouping by topic similarity | `app/search_api.py` |
| `039_kg_rpcs.sql` | Knowledge graph RPCs — topic lookup, chapter attribution helpers | `app/search_api.py` |
| `040_foundation_layer.sql` | Quarantine columns on `kg_syllabi`/`kg_chapters`; `student_interactions`; `get_recurring_topics()` RPC | `app/search_api.py` |
| `041_telegram_signals.sql` | `telegram_signal_items` — typed signals extracted from Telegram channels | `app/intelligence_worker.py` |
| `042_exam_attribution_columns.sql` | `exam_questions.chapter_id`, `chapter_verified`, topic attribution columns | `app/chapter_attribution_worker.py` |
| `043_course_aliases.sql` | `course_aliases` — alternative course code / name mappings | `app/search_api.py` |
| `044_faculty_course_bridge.sql` | `kg_faculty_sections` — faculty × course × semester teaching assignments | `app/search_api.py` |
| `045_exam_bank_allowlist.sql` | `exam_bank_allowlist` — courses approved for public exam bank access | `app/search_api.py` |
| `046_operational_intelligence_layer.sql` | `pipeline_runs`, `community_qa`, `current_academic_context` VIEW; fingerprint + pipeline columns on `exam_questions`/`document_chunks` | `app/search_api.py` (Cockpit) |
| `047_student_os_foundation.sql` | `student_mastery`, `student_academic_profile`, `proactive_surface_queue` — Student OS data foundation | `student_profile_worker` (gated, not yet active) |
| `048_institutional_intelligence.sql` | `program_intelligence`, `course_sections`, `section_seat_snapshots`, `official_announcements`; ALTER `kg_faculty` add rank/cv_url/profile_verified | institutional scrapers (gated) |
| `049_behavioral_intelligence.sql` | `concept_confusion_registry`, `course_behavioral_profile`; `get_course_behavioral_intelligence()` RPC; `refresh_course_behavioral_profile()` fn | `app/search_api.py`, course_behavioral_worker (gated) |
| `050_fix_behavioral_refresh_fn.sql` | Fix `refresh_course_behavioral_profile` — learning_events uses `occurred_at` not `created_at` | — |
| `051_fix_behavioral_refresh_fn_v2.sql` | Fix `refresh_course_behavioral_profile` — message_signals uses `extracted_at` not `created_at` | — |
| `052_seu_institutional_canon.sql` | `seu_colleges_canon` (6 colleges, caic/afsc/hsc/satsc mapped), `seu_programs_canon` (21 programs), `seu_org_aliases` (44 aliases); additive FKs on `official_announcements`, `course_sections`, `kg_faculty`; `resolve_college_alias()` RPC; `canon_coverage_check` view | institutional scrapers |
| `053_seu_academic_structure_correction.sql` | Fix MCS bachelor→master; MBADM→MDM; BSBA parent program; concentration_of FK; 16 Applied College diploma programs; `seu_academic_tracks_canon` (double major/minor); website_domain on APPLIED | institutional scrapers |
| `054_canon_propagation_exam_docs.sql` | `college_canon_code` FK on `exam_questions` + `source_documents`; `seu_course_college_map` (prefix resolver); `resolve_course_to_college()` RPC; backfill 15,820/17,195 exam_questions (92%); `college_exam_coverage` view | Cockpit gap dashboard |
| `055_canon_propagation_behavioral.sql` | `college_canon_code` FK on `course_behavioral_profile` + `concept_confusion_registry`; CTE rewrite of `college_exam_coverage` (fixes 500 timeout); `concept_temporal_trajectory` table (concept time-series, compounding asset); `institutional_behavioral_clock` view; `college_knowledge_gap` view; `concept_cooccurrence_log` table | Cockpit gap dashboard, behavioral intelligence |
| `056_seed_concept_temporal_trajectory.sql` | Seed 3,287 (concept × course) rows into `concept_temporal_trajectory` from `exam_questions.topic_tags` — Year Zero historical snapshot (`academic_year='1446'`). exam_appearances = actual frequency in corpus. confusion_score = 0 pending concept_confusion_worker. | concept_temporal_trajectory |
| `057_course_health_and_concept_tags.sql` | `learning_events.concept_tags TEXT[]` (GIN + partial indexes); `course_health_score` VIEW — composite 0–100 score (exam_pts 0–40, corpus_pts 0–30, topic_pts 0–20, confusion_pts 0–10). Health tier: green ≥80, yellow ≥50, red <50. No worker — self-updates. | `app/search_api.py`, Cockpit |
| `058_fix_course_health_college_fallback.sql` | Fix `course_health_score` VIEW — `college_canon_code` fallback from `exam_questions` when `course_behavioral_profile` has no row for the course. | `course_health_score` VIEW |

## How to apply via CLI

The project is linked (`supabase projects list` shows ●). Apply pending migrations with:
```bash
supabase db push --linked
```

## Schema-as-code rule

Every schema change must go through a migration file here before being applied. Do not create or alter tables directly in the Supabase UI without a corresponding file in this directory.

This rule exists because schema changes applied only through the UI are invisible to git, unreviewable, and unreproducible. See ADR-0003.
