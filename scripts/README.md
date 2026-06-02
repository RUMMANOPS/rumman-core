# scripts/

Operational CLI tools for RUMMAN platform management. Not deployed — run locally against production or staging Supabase.

All scripts require a `.env` file with `SUPABASE_URL`, `SUPABASE_KEY`, and `OPENAI_API_KEY`.

---

## Ingestion

### `ingest_document.py` — Ingest an official document into the knowledge pipeline

Use this to push files from the university knowledge repository into the platform. The file goes through: upload to Supabase Storage → `source_documents` row → `pdf_extract` processing job → (after `pdf_worker` runs) → `embed_chunk` job → (after `embed_worker` runs) → `document_chunks`.

```bash
python3 scripts/ingest_document.py path/to/file.pdf \
    --source-type study_plan \
    --course-code IT362 \
    [--exam-type final] \
    [--academic-year 2024] \
    [--semester first] \
    [--language ar] \
    [--dry-run]
```

**Source types:** `exam`, `study_plan`, `regulation`, `course_description`, `telegram_export`, `upload`

After running, the `pdf_worker` and `embed_worker` processes must run to complete extraction and embedding.

---

### `seed_courses.py` — Seed structured course data from `data/inst_courses.json`

Pushes course records (codes, English names, descriptions, credit hours, levels, prerequisites) into the `inst_courses` table. The JSON file currently covers 2 programs: `BSBA_MGT` (37 courses) and `BSCS` (45 courses).

```bash
python3 scripts/seed_courses.py              # seed all programs
python3 scripts/seed_courses.py --dry-run    # validate JSON, no DB writes
python3 scripts/seed_courses.py --embed      # also embed course descriptions into document_chunks
python3 scripts/seed_courses.py --program BSCS  # one program only
```

---

## Backfill Management

### `create_backfill_jobs.py` — Create telegram_backfill_jobs rows

Creates backfill job entries for Telegram groups that need historical ingestion. The `telegram_backfill_worker.py` process picks these up.

```bash
python3 scripts/create_backfill_jobs.py --chat-ids 1234567 2345678 3456789
python3 scripts/create_backfill_jobs.py --dry-run
```

---

## Lexicon Management

### `generate_seed_lexicon.py` — Generate normalization dictionary candidates

Analyzes `document_chunks` to find common non-standard terms and dialect phrases that should be normalized. Outputs candidate entries to `data/seed_candidates_<timestamp>.json` (gitignored).

```bash
python3 scripts/generate_seed_lexicon.py
python3 scripts/generate_seed_lexicon.py --limit 500  # analyze top N chunks
```

### `review_candidates.py` — Interactive review of lexicon candidates

Review and approve/reject candidates before adding them to `data/normalization_dict.json`.

```bash
python3 scripts/review_candidates.py data/seed_candidates_20260528_215514.json
```

---

## Concept Extraction

### `extract_concepts.py` — Extract academic concepts for knowledge graph seeding

Analyzes document chunks and extracts entities (course topics, academic concepts) that can seed a future knowledge graph layer.

```bash
python3 scripts/extract_concepts.py --course-code IT362
python3 scripts/extract_concepts.py --all
```

---

## Data Files

### `data/inst_courses.json`

Structured course data for SEU programs: course codes, English titles, descriptions, credit hours, levels, prerequisites. Currently covers `BSBA_MGT` and `BSCS`. Used by `seed_courses.py`.

To add more programs: follow the existing JSON structure (see `programs[].courses[]` array).
