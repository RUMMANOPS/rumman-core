# RUMMAN_MASTER_DOSSIER
## Complete Institutional Memory & Source of Truth

**Produced:** 2026-06-01  
**Repository:** rumman-core @ main (5e33466)  
**Purpose:** Full knowledge transfer. Sufficient to rebuild, operate, extend, and strategically evolve the platform with zero prior context.

---

# TABLE OF CONTENTS

1. Executive Summary
2. Founder Vision
3. Product Philosophy
4. Historical Timeline
5. Complete System Architecture
6. Infrastructure Architecture
7. Database Encyclopedia
8. End-to-End Data Flows
9. AI Architecture
10. Telegram Ecosystem
11. Academic Intelligence Layer
12. Search Architecture
13. Media Processing Architecture
14. Memory Architecture
15. Operational Intelligence Vision
16. Security & Privacy Model
17. Cost Model
18. Technical Debt Register
19. Hard Constraints
20. Roadmap
21. Complete Inventory
22. Rebuild Guide
23. Blind Spots & Unknowns

---

# 1. EXECUTIVE SUMMARY

## What is RUMMAN?

RUMMAN (رمّان) is an **Operational Intelligence Operating System** for Saudi universities. It continuously ingests, structures, and synthesizes knowledge from two complementary sources — official university documents and the organic intelligence flowing through student Telegram groups — and makes it instantly queryable by any student via a Telegram bot.

The name رمّان (pomegranate) is intentional: densely packed with seeds of knowledge, unified under one skin.

## Why Does It Exist?

A student at the Saudi Electronic University (SEU) preparing for an exam faces a specific and painful information problem:

- Exam topics are scattered across 50+ Telegram groups, each with thousands of messages
- Official study plans are locked in PDFs no one reads
- Past exam papers circulate informally, never indexed
- Critical announcements ("the exam covers chapters 1–5 only") disappear in chat history
- Every student wastes 30–60 minutes per study session just finding the information

RUMMAN eliminates that waste. One question to one bot, answered from everything the university community has ever said or published about that course.

## What Problem Does It Solve?

**The knowledge fragmentation problem.** Saudi university students possess collectively enormous intelligence about their courses — what appears in exams, which topics are hard, what professors emphasize, which resources work — but that intelligence is:

1. Trapped in ephemeral Telegram chat history
2. Scattered across dozens of groups per university
3. Never connected to official institutional knowledge
4. Inaccessible to students who weren't online when it was shared

RUMMAN transforms this fragmented, perishable community intelligence into a persistent, searchable, institutionally-grounded knowledge layer.

## Who Is It For?

**Primary users (v1):** Students at Saudi Electronic University (SEU) — a large distance-learning university with 200,000+ students. Distance-learning students are particularly dependent on Telegram groups because they have no physical campus for informal knowledge sharing.

**Strategic expansion:** Any Saudi university with an active Telegram community. The platform is multi-tenant from the ground up; adding a second university is an operational task, not an engineering rewrite.

**Secondary stakeholders:** University administration (gap analytics, content coverage reports), academic staff (understanding student confusion patterns).

## What Makes It Unique?

Three properties distinguish RUMMAN from existing solutions:

**1. Dual-layer grounding.** Answers are synthesized from both official university documents (authority_tier='official') and community intelligence (authority_tier='community'). Neither alone is sufficient: official docs lack the lived exam experience; community content lacks authoritative structure. The combination produces answers students actually trust.

**2. Anti-hallucination architecture.** The synthesis prompt explicitly forbids the model from using its training knowledge about SEU. Every answer must be traceable to a retrieved chunk. If the corpus doesn't contain the answer, the system says so. This is an architectural commitment, not a prompt trick.

**3. Operational memory.** The system accumulates knowledge over time — student context, exam intelligence profiles, message signals, knowledge gaps. Each query makes future queries better. This is the foundation of the intelligence OS vision.

---

# 2. FOUNDER VISION

## Origin

RUMMAN was created by Ibrahim (IbraSQ on GitHub) as a response to a concrete personal observation: SEU students were collectively answering each other's questions in Telegram groups at scale — but the answers disappeared. A student who joined a group three months after an important announcement could never find it. The same questions were asked and answered in every exam cycle, with zero institutional memory.

The initial insight: **the intelligence already exists. It just needs to be captured, structured, and made retrievable.**

## How the Vision Evolved

**Stage 1 — Simple listener.** The first version was a Telegram listener that stored messages in Postgres. Nothing more. The insight at this stage: message storage alone has no value. Messages are noise without structure.

**Stage 2 — Search over messages.** Added vector embeddings and a basic search endpoint. Students could ask questions and get relevant message excerpts. This worked but felt like searching a chat log — low quality, no context, no synthesis.

**Stage 3 — Institutional grounding.** The key pivot: adding the official SEU knowledge layer (study plans, regulations, course descriptions) alongside community content. This transformed search quality from "here's a message someone wrote" to "here's what the university officially says, plus what students say about it in practice." This pivot is documented in the shift from a pure Telegram-ingestion tool to an institutional+community dual-layer system.

**Stage 4 — Intelligence layer vision.** Recognition that search is a stepping stone, not the destination. The real product is an operational intelligence system that knows what's happening at the university right now: which exams are coming up, which topics students are confused about, which resources students are recommending, which announcements professors made. This is Phase 2 and beyond.

## Major Pivots

**Pivot 1: From chatbot to OS framing (ADR-0001).** Early architectural discussions treated RUMMAN as a smart chatbot. This was wrong. A chatbot is reactive and stateless; an OS is continuous and accumulates state. The reframing changed every subsequent architectural decision — particularly the insistence on background workers, persistent state, and a job queue architecture rather than request-response patterns.

**Pivot 2: Separation of live and historical ingestion (ADR-0002).** An early version merged the live listener and historical backfill into one process. This caused startup delays, unstable pipelines, and Telegram rate-limit cascades. The separation into two independent workers (listener for live, backfill worker for history) was a major structural improvement. This boundary is now a hard constraint enforced in code.

**Pivot 3: Three Telegram account architecture.** Running all Telegram operations from one account created session conflicts (AuthKeyDuplicatedError when two processes used the same StringSession). The solution was three purpose-specific accounts: غيث (listener), راوي (backfill), إبراهيم (media/downloads). Each account has one worker; no two workers share a session.

**Pivot 4: Direct PostgREST over Supabase client library.** The Supabase Python client library abstracted away HTTP-level control needed for: custom `Prefer` headers (return=representation, resolution=merge-duplicates), conditional PATCH for lease acquisition, 409 deduplication handling. Switching to direct httpx calls against the PostgREST API gave precise control over every HTTP interaction.

**Pivot 5: Anti-hallucination as architecture.** The synthesis system was initially built with a general "answer from context" prompt. In practice, the model would blend corpus content with its own training knowledge about Saudi universities. This was unacceptable — the system would confidently state exam details that were never in the corpus. The fix was architectural: explicitly forbid training knowledge use, require citations, and treat synthesis failure as a valid outcome preferable to hallucination.

## Long-Term Ambitions

**Near-term (12 months):** Become the authoritative exam preparation tool for SEU students. Every SEU student asking an exam question should reach RUMMAN before reaching Google.

**Medium-term (2–3 years):** Multi-university platform. Saudi Arabia has 30+ universities, each with active Telegram communities. The platform's value compounds with each university added because the AI infrastructure (embeddings, synthesis, attribution) is already built.

**Long-term vision:** The institutional intelligence layer for Arabic-medium universities. A university CTO should be able to understand their students' collective knowledge gaps, most-asked questions, and resource effectiveness from a RUMMAN dashboard — without reading a single Telegram message.

## Core Beliefs Driving Decisions

1. **Community intelligence is institutional knowledge waiting to be structured.** Students collectively know more about passing a course than any professor's syllabus says. That knowledge has value; it just needs capture and structure.

2. **Silence is better than hallucination.** A system that says "I don't know" 30% of the time is better than one that confidently says wrong things 5% of the time. Students will stop trusting the second one permanently after one bad exam experience.

3. **The pipeline must be observable and debuggable.** If something goes wrong at 3am, the on-call engineer must be able to understand the system's state from Postgres tables alone — no distributed tracing, no logs from five services, no framework knowledge required.

4. **Build for the second university from the first day.** Multi-tenancy is infinitely harder to add than to include from the start. Every table has tenant_id. Every query filters by tenant_id.

5. **The university's institutional layer is the anchor.** Community intelligence without institutional grounding drifts. Answers about exam content must be anchored to official course descriptions, study plans, and regulations. The community layer amplifies the official layer; it doesn't replace it.

---

# 3. PRODUCT PHILOSOPHY

## What RUMMAN IS

- An Operational Intelligence OS that runs continuously in the background
- A dual-layer knowledge system: institutional (official) + community (student-generated)
- A grounded synthesis engine: every answer traces to source material
- A knowledge accumulator: every interaction makes the system smarter
- Multi-tenant infrastructure built for Arabic-medium universities

## What RUMMAN IS NOT

- Not a chatbot. Does not generate conversational responses from training knowledge.
- Not a message archiver. Storage is infrastructure, not product.
- Not a recommendation engine. Does not suggest content unprompted.
- Not a search engine. Returns synthesized answers with citations, not result lists.
- Not a course management system. Does not manage enrollment, grades, or assignments.
- Not a replacement for professors. Surfaces what the community has said; doesn't create new knowledge.

## Success Criteria

**For a student interaction:**
- Correct, grounded answer in under 10 seconds
- Answer cites source (official / community) so student can verify
- If no answer exists in corpus, system says so clearly rather than fabricating

**For the platform:**
- Zero hallucination rate (architecture enforced, not prompt engineered)
- Coverage of top 50 most-queried courses reaches "moderate" or "strong"
- Zero-result rate below 20% for course-specific exam queries
- Student re-engagement: students who get a good answer come back

**For the operation:**
- All workers healthy and processing within 5 minutes of a new message
- No single daily cost event exceeds $5 in AI API calls
- New university can be onboarded in under 1 week of engineering time

## Failure Criteria

These indicate the platform has failed and requires significant intervention:

- A student gets a wrong exam topic answer that causes them to study the wrong material for an exam
- A worker runs unconstrained and generates $100+ in API costs in a single day
- Cross-tenant data is returned in a query response
- Raw Telegram session string appears in any committed code or log

## Non-Negotiable Principles

**Anti-hallucination:** The synthesis prompt must explicitly forbid using training knowledge. This is re-verified with every prompt change.

**Provenance:** Every AI-generated claim must trace to an ai_runs record. No exceptions.

**Tenant isolation:** tenant_id appears in every query. A query that returns data from multiple tenants without explicit cross-tenant authorization is a bug.

**Ingestion separation:** The live listener never crawls history. The backfill worker never handles live messages. This boundary is enforced in code, not just convention.

## Design Philosophy

**Postgres is the control plane.** Job queues, worker state, cursor positions, lease acquisition, heartbeats, event logs — everything lives in Postgres. This makes the system observable, debuggable, and recoverable from Postgres alone.

**Async Python, direct HTTP.** No ORMs, no frameworks, no abstractions that hide HTTP behavior. Direct httpx calls against PostgREST. This produces predictable, debuggable, fast code.

**Cheapest path first.** Attribution uses regex before LLM. Query understanding uses static normalization before calling gpt-4o-mini. Synthesis uses cache before calling OpenAI. Every pipeline step asks: can we get this result for free or cheaply before spending API budget?

**Fail gracefully, log loudly.** Workers catch all exceptions, log them with structured events, and continue. The synthesis endpoint falls back to returning raw chunks if GPT fails. No single component failure should degrade the user experience to zero.

## User Experience Philosophy

Students ask questions in Gulf Arabic dialect. The system:
1. Normalizes dialect to MSA without the student knowing
2. Understands what they're asking (intent classification)
3. Retrieves relevant material in Arabic and English
4. Returns an answer in the same language the student used
5. Never asks for clarification unless genuinely necessary

The UX principle: **the student should feel like they asked a knowledgeable classmate, not a search engine.**

---

# 4. HISTORICAL TIMELINE

## Phase 0: Foundation (Completed)

**What happened:** Basic Railway deployment, Telegram listener, Supabase message storage, GitHub docs framework, ADR structure established.

**Key decision:** From day one, separate live ingestion from backfill. This principle, encoded in ADR-0002, was learned from early experiments where a combined listener+backfill caused Telegram to rate-limit the account and produced startup delays.

**Key artifact:** `ENABLE_BACKFILL = False` guard in rumman_engine.py — a code-level enforcement of an architectural decision, not merely a configuration option.

## Phase 1: Data Spine + Search (Completed, Evolved)

**What happened:** The original goal was ingestion stabilization. The system grew significantly beyond that to include the full search pipeline, institutional knowledge layer, and student-facing bot.

**Significant evolution from original plan:**
- Audio transcription pipeline added (Whisper API)
- Media/OCR pipeline for PDF and images added
- Full query understanding pipeline (normalization → intent → synthesis)
- SEU curriculum database seeded (161 courses)
- Telegram bot deployed for students
- 153 official SEU documents ingested

**Key discovery:** Vector similarity for Arabic queries is significantly lower than for English queries against the same embedding model (text-embedding-3-large). The minimum similarity threshold had to be lowered from 0.45 to 0.40 for broad search and to 0.25 for course-filtered search to avoid excessive zero-result rates. This is a fundamental property of the embedding model, not a bug.

**Key discovery:** The normalization dictionary is essential infrastructure, not an optimization. Without converting Gulf Arabic dialect to MSA before embedding, queries like "وش يجي بالاختبار" would miss chunks about "ما الذي يظهر في الاختبار" — identical meaning, different surface form, very different embedding.

**Key failure mode discovered:** Merged audio+media worker caused AuthKeyDuplicatedError when two processes competed for the same Telegram StringSession. Solution: one account per worker type, three accounts total.

**Architectural transition:** `seu_*` tables renamed to `inst_*` (inst_colleges, inst_specializations, inst_courses) in migration 014 to support multi-tenancy. The `seu_` prefix implied SEU-only; `inst_` signals institutional-generic. This was a one-way migration — a deliberate forward investment in the multi-university direction.

## Phase 2: Institutional Intelligence (Completed 2026-06-01)

**What happened:** Transformed the system from a search tool into a genuine intelligence layer.

**Completed capabilities:**
- Academic calendar injection for exam schedule / deadline queries
- Intelligence worker for extracting operational items (exams, assignments, decisions) from messages
- Attribution worker for classifying untagged document_chunks to courses
- QA mining worker extracting implicit Q&A pairs from 72K Telegram messages
- Message signal worker extracting exam emphasis, difficulty, professor notes, resource recommendations
- Gap analyst identifying which topics have no corpus coverage
- Course intelligence profiles (per-course corpus summary injected into synthesis)
- Exam intelligence (top recurring exam topics per course, LLM-extracted)
- Student context persistence (enrolled courses, active focus, language preference)
- Synthesis cache (2-hour LRU, SHA-256 keyed on query+course+exam_type)
- Weekly health report sent to ops Telegram channel

**Key lesson learned about attribution:** Machine attribution of course codes requires two mechanisms: regex-first (if one course code appears explicitly in the chunk text, assign it for free — confidence=1.0) and LLM fallback (for ambiguous content). The LLM threshold must be HIGH (≥0.85) because false attribution (wrong course) is worse than no attribution. A chunk attributed to the wrong course contaminates search results for that course.

**Key lesson learned about Arabic course codes:** The Law school (كلية القانون) uses Arabic-script course codes like قنن427, قنن103 rather than Latin codes. This was discovered when analyzing attributing law-related content — the regex that matched only Latin codes silently skipped all law school content. Fixed by adding `|\b([ء-ي]{2,4}\d{3,4})\b` to all course code regexes.

**Key lesson learned about signal injection:** The synthesis pipeline had a hardcoded list of signal types that excluded `resource_rec` entirely. Students asking "what resources should I use for IT362?" received answers with no resource recommendations even when message signals contained exactly that information. Intent-aware signal selection was implemented to fix this.

**Open items entering Phase 3:**
- 84% of document_chunks still have null course_code (attribution_worker running at 3K/day)
- 15K+ media processing jobs stuck pending (TELEGRAM_MEDIA_IBRAHIM_SESSION not set in Railway)
- 41 public SEU groups not yet joined by راوي/إبراهيم accounts
- intelligence_worker enabled but FIN416 and MGT425 have content gaps

---

# 5. COMPLETE SYSTEM ARCHITECTURE

## Overview

RUMMAN runs as **8 independent processes** on Railway, all sharing a single Supabase instance. Processes communicate exclusively via Postgres tables — no inter-process HTTP calls, no message brokers, no shared memory.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         TELEGRAM ECOSYSTEM                              │
│  50+ SEU Telegram groups (messages, files, audio, images)               │
└────────────┬──────────────────┬──────────────────────────────────────────┘
             │ live messages    │ historical backfill
             ▼                  ▼
    ┌──────────────┐   ┌─────────────────────┐
    │   listener   │   │      backfill        │
    │ rumman_engine│   │ telegram_backfill    │
    │ (غيث account)│   │ _worker (راوي acct) │
    └──────┬───────┘   └──────────┬───────────┘
           │                      │
           └──────────┬───────────┘
                      │ INSERT → messages table
                      │ INSERT → processing_jobs (media)
                      ▼
    ┌────────────────────────────────┐
    │         media worker           │
    │  telegram_download_worker.py   │
    │  (إبراهيم account)             │
    │  audio_transcribe → Whisper    │
    │  telegram_media → PyMuPDF/OCR  │
    └───────────────┬────────────────┘
                    │ INSERT → source_documents
                    │ INSERT → processing_jobs (embed)
                    ▼
    ┌─────────────────────────────┐
    │        embed worker         │
    │     embed_worker.py         │
    │  chunk → embed → store      │
    └───────────────┬─────────────┘
                    │ INSERT → document_chunks (with vector)
                    ▼
         ┌──────────────────┐    ┌──────────────────────┐
         │  attribution     │    │   intelligence       │
         │  worker.py       │    │   worker.py          │
         │  course tagging  │    │   item extraction    │
         └──────────────────┘    └──────────────────────┘
                    │                      │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼──────────┐
                    │    search API       │
                    │  search_api.py      │
                    │  (FastAPI + pgvec.) │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │    telegram bot     │
                    │  telegram_bot.py    │
                    │  (student-facing)   │
                    └─────────────────────┘
```

## Component Specifications

### 5.1 listener — `app/rumman_engine.py`

**Purpose:** Capture every new message sent to every SEU Telegram group in real time.

**Responsibilities:**
- Connect to Telegram as a user client (not a bot) via Telethon StringSession
- Register `NewMessage` event handler for all chats
- Normalize message metadata (type, content, sender, timestamps)
- Insert into `messages` table (HTTP 409 = already exists, silently ignored)
- Update `telegram_sync_state` (newest_message_id per chat)
- If message has media → insert into `processing_jobs` (telegram_media or audio_transcribe)
- If message ID jump > 10 → create gap-fill job (self-healing mechanism)
- Discover new groups every 6 hours → auto-create backfill jobs

**Inputs:** Telegram NewMessage events (push, not poll)
**Outputs:** `messages` rows, `telegram_sync_state` updates, `processing_jobs` for media

**Dependencies:** Telethon, TELEGRAM_LISTENER_GHAYTH_SESSION, SUPABASE_URL/KEY

**Failure modes:**
- AUTH_KEY_DUPLICATED → another process using same session. Sleep 3 min, retry.
- FloodWaitError → Telegram rate limit. Sleep for specified duration.
- SUPABASE unreachable → messages buffered in memory briefly, then dropped (acceptable — backfill will recover)

**Critical invariant:** `ENABLE_BACKFILL = False` is a hard guard, not a config option. Never set to True. (ADR-0002)

---

### 5.2 backfill — `app/telegram_backfill_worker.py`

**Purpose:** Systematically ingest the full historical message history for all SEU Telegram groups.

**Responsibilities:**
- Poll `telegram_backfill_jobs` for pending work
- Acquire a lease (optimistic PATCH with `status=pending` condition — prevents race conditions)
- Download historical messages in batches of 500 via Telegram iter_messages
- Insert into `messages` (409 = already ingested by listener, silently ignored)
- Create `processing_jobs` for any media discovered in historical messages
- Renew lease heartbeat every 100 messages
- Mark job complete when oldest message reached; create next-batch job if more history exists
- Handle FloodWaitError: save progress, release lease, sleep, resume

**Inputs:** `telegram_backfill_jobs` (pending rows), Telegram API
**Outputs:** `messages` (historical), `processing_jobs` (media discovered in history), `telegram_sync_state` (oldest_message_id)

**Lease protocol detail:**
```
PATCH telegram_backfill_jobs
WHERE id = {job_id} AND status = 'pending'  ← condition prevents double-claim
SET status = 'running', worker_id = {me}, lease_expires_at = now() + 10min
```
If PATCH affects 0 rows → another worker claimed it → skip.

**Rate limiting:** 3 seconds between batches. Configurable via `BACKFILL_SLEEP_SECONDS`.

---

### 5.3 media — `app/telegram_download_worker.py`

**Purpose:** Download and process all media files (PDFs, images, audio) discovered in Telegram messages.

**Why unified:** Running separate audio and media workers on the same Telegram account caused `AuthKeyDuplicatedError`. A single worker handles both job types sequentially on the إبراهيم account.

**Responsibilities:**
- Poll `processing_jobs` for `audio_transcribe` and `telegram_media` jobs
- Download file from Telegram to `/tmp` using Telethon
- **Audio:** Convert OGA→OGG → OpenAI Whisper API → transcript stored in `media_files`
- **PDF:** PyMuPDF digital extraction + GPT-4o-vision for image-only pages
- **Images:** GPT-4o-vision OCR (max 10 pages per document to cap cost)
- Detect course codes and exam types from filename/caption (regex)
- Set `authority_tier='community'` for student uploads, `'official'` for official documents
- Create `source_documents` record + `embed_chunk` processing job
- Delete local temp file after processing

**Key design decision:** Raw files are NEVER stored in Supabase Storage from this worker. They are downloaded to `/tmp`, processed, and discarded. This is a privacy + cost decision. The extracted text is what matters; the raw binary has no long-term value for community content.

**Concurrency:** asyncio.Semaphore(10) — 10 files processed simultaneously.
**Retry:** max 5 attempts per job; exponential backoff.

**Failure modes:**
- File deleted from Telegram before download → mark job failed, no retry
- OCR quality too low → still stores extracted text with `ocr_confidence` < 0.5 flag
- OpenAI timeout → retry with same job

---

### 5.4 embed — `app/embed_worker.py`

**Purpose:** Convert extracted document text into vector-embedded chunks stored in `document_chunks`.

**Responsibilities:**
- Poll `processing_jobs` for `embed_chunk` jobs
- Load extracted text from `source_documents`
- NFKC Unicode normalization (collapses Arabic ligatures that embed differently but mean the same)
- Intelligent chunking:
  - Exam documents: question-aware splitting (splits on "Q1.", "السؤال الأول", numbered patterns)
  - Other documents: paragraph-aware with 500-token chunks and 50-token overlap
- Embed each chunk with `text-embedding-3-large` (1536 dimensions)
- Store in `document_chunks` with full metadata (course_code, authority_tier, exam_type, etc.)
- Update `source_documents.processing_status = 'chunked'`

**Why text-embedding-3-large?** Higher accuracy than ada-002 or text-embedding-3-small, especially for Arabic text. The 1536-dim limit fits pgvector's HNSW index constraints (max 2000 dims). The cost difference from small→large is minimal compared to search quality improvement.

**Why NFKC normalization?** Arabic has multiple Unicode representations for the same character due to ligature variations. Without normalization, the same word can embed to different vectors if written in different Unicode forms. NFKC collapses all variants to canonical form before embedding.

---

### 5.5 search — `app/search_api.py`

**Purpose:** The platform's primary intelligence API. Handles all search and synthesis requests from the bot and any future clients.

Full pipeline documented in Section 12 (Search Architecture).

**Key endpoints:**
- `POST /search` — retrieval only, no synthesis
- `POST /synthesize` — full pipeline with GPT synthesis
- `POST /v1/users/identify` — pseudonymous user creation
- `POST /v1/sessions` — session management
- `PATCH /v1/sessions/{id}` — update enrolled courses, active focus
- `GET /health` — operational health check

---

### 5.6 bot — `app/telegram_bot.py`

**Purpose:** Student-facing interface. Long-polls Telegram Bot API, routes messages, calls search_api, returns answers.

Full specification in Section 10 (Telegram Ecosystem) and the earlier technical report.

**Key architectural choice:** The bot does NOT perform any retrieval or synthesis itself. It is a thin routing + formatting layer that delegates all intelligence work to search_api. This means search_api can serve web clients, mobile apps, or other bots in the future without any changes.

---

### 5.7 intelligence — `app/intelligence_worker.py`

**Purpose:** Continuously extract operational items (exams, deadlines, assignments, announcements) from incoming Telegram messages.

**Current state:** Enabled (`INTELLIGENCE_WORKER_ENABLED=true` in Railway since Phase 2 completion).

**Operation:**
- Cursor-based: reads messages after `last_cursor` stored in `worker_cursors`
- Batch 50 messages, 15 concurrent API calls (semaphore-controlled)
- GPT-4o-mini extracts structured items with confidence scores
- Only stores items with confidence ≥ 0.65
- Deduplication: UNIQUE(tenant_id, source_platform, source_message_id, item_type)

**Output value:** These items feed the intelligence query path — when a student asks "when is the IT362 exam?", the system can retrieve from `intelligence_items` as well as the corpus.

---

### 5.8 attribution — `app/attribution_worker.py`

**Purpose:** Classify `document_chunks` with null `course_code` to their academic course.

**Current state:** Running in Railway, 3,000 API calls/day budget.

**Why this matters:** 84% of chunks entered the system without a course code (from general Telegram groups, multi-course PDFs, etc.). Without attribution, these chunks are invisible to course-filtered search. Attribution is the primary lever for improving search quality.

**Two-path pipeline:**
1. Regex-first: if exactly one course code appears explicitly in the chunk text → assign it immediately (confidence=1.0, zero API cost)
2. LLM: if regex yields zero or multiple codes → gpt-4o-mini with strict JSON schema → only apply if confidence ≥ 0.85

**Why 0.85 threshold (not 0.70 or 0.75)?** False attribution is worse than no attribution. A chunk incorrectly attributed to IT362 will pollute IT362 search results with irrelevant content. At 0.85, the LLM is essentially certain; below that, the guess introduces noise.

**Provenance:** Every LLM attribution creates an `ai_runs` record. If attribution quality degrades, every affected chunk can be identified and reverted by querying `WHERE attribution_ai_run_id = '<bad_run>'`.

---

## Workers NOT in Procfile (Run on Demand)

| Worker | Location | Purpose | Why Not in Procfile |
|--------|----------|---------|---------------------|
| pdf_worker | app/pdf_worker.py | PDF extraction for Storage-hosted files | Superseded by telegram_download_worker for community content; used for official docs |
| daily_brief | app/daily_brief.py | Extract operational items from last 24h messages | Run on cron; not a continuous loop |
| qa_mining_worker | app/qa_mining_worker.py | Extract Q&A pairs from Telegram history | Run on demand after new content ingested |
| audio_worker | app/audio_worker.py | Standalone audio transcription | Superseded by unified media worker |
| query_handler | app/query_handler.py | CLI test tool for synthesis pipeline | Development/debug only |

---

# 6. INFRASTRUCTURE ARCHITECTURE

## Railway

**Why Railway?** Procfile-based multi-process deployment with minimal ops overhead. Provides: automatic restart on crash, per-process environment variables, internal networking between services (SEARCH_API_URL uses Railway internal DNS), zero-config HTTPS, and build from Dockerfile or requirements.txt.

**8 processes, each independently managed by Railway:**

```
listener:     python3 app/rumman_engine.py
backfill:     python3 app/telegram_backfill_worker.py
media:        python3 app/telegram_download_worker.py
embed:        python3 app/embed_worker.py
search:       uvicorn app.search_api:app --host 0.0.0.0 --port ${PORT:-8000}
bot:          python3 app/telegram_bot.py
intelligence: python3 app/intelligence_worker.py
attribution:  python3 app/attribution_worker.py
```

**Important:** `force_redeploy.txt`, `rebuild.txt`, `trigger.txt` are empty files in the repo whose only purpose is to trigger Railway redeploys when their timestamp changes. They carry no information and should never be removed.

**Environment management:** All secrets and configuration live in Railway environment variables. `.env` is for local development only and is gitignored.

## Supabase

**Why Supabase?** Managed PostgreSQL with pgvector extension, PostgREST API (enabling direct HTTP queries without an ORM), Supabase Storage (for binary files), and built-in Auth (not used — RUMMAN uses its own pseudonymous identity model).

**What lives in Supabase:**
- All PostgreSQL tables (33 migrations, see Section 7)
- pgvector HNSW index on `document_chunks.embedding`
- Supabase Storage bucket `rumman-content` (official document PDFs before extraction)

**Why direct PostgREST, not the Supabase client library?**
The Supabase Python client abstracts HTTP in ways that prevent precise control over:
- `Prefer: return=representation` (needed to get inserted row's ID back)
- `Prefer: resolution=merge-duplicates` with `?on_conflict=column` (upsert)
- Conditional PATCH for lease acquisition (requires exact URL filter syntax)
- HTTP 409 as a meaningful dedup signal (client may throw exceptions)

Direct httpx calls give full control at minimal complexity cost.

## Telegram

**Three accounts, one purpose each:**

| Account | Identity | Session Variable | Processes |
|---------|----------|-----------------|-----------|
| غيث | +966582282200 | TELEGRAM_LISTENER_GHAYTH_SESSION | listener only |
| راوي | +966590111167 | TELEGRAM_BACKFILL_RAWI_SESSION | backfill only |
| إبراهيم | +966560064766 | TELEGRAM_MEDIA_IBRAHIM_SESSION | media only |

**Why user accounts, not bots?** Telegram bots cannot join groups without being added by an admin, cannot read message history, and cannot download all media types. User accounts have these capabilities. The tradeoff is: user accounts require StringSession management and are subject to Telegram's anti-automation rate limits.

**Bot account:** Separate from the three user accounts. Uses `TELEGRAM_BOT_TOKEN` from @BotFather. This is the student-facing interface.

## OpenAI

**Models used:**

| Model | Usage | Why |
|-------|-------|-----|
| text-embedding-3-large | All chunk embeddings + query embeddings | Best Arabic accuracy at 1536 dims |
| gpt-4o-mini | Intent classification, synthesis (default), attribution | Fast, cheap, sufficient quality |
| gpt-4o | Comparison queries, low-confidence synthesis, OCR of complex documents | Higher quality when needed |
| whisper-1 | Audio transcription | Only available Whisper API model |

**Cost model:** See Section 17.

## Storage

**Supabase Storage (`rumman-content` bucket):**
- Only stores official SEU documents (study plans, regulations, course descriptions) uploaded via `ingest_document.py`
- Community content (student PDFs, images) is processed from `/tmp` and never persisted in Storage
- Path format: `{tenant_id}/{source_type}/{filename}`

**Local filesystem (`/tmp`):**
- Temporary download location for media worker
- Purged after processing
- Never committed or persisted

## Deployment Model

```
git push origin main
  → Railway auto-deploys all 8 processes
  → Processes start independently
  → Workers immediately begin polling their job queues
  → listener reconnects to Telegram via stored StringSession
```

**Session string generation:** One-time local operation via `auth_session.py` (gitignored). Generates a Telethon StringSession for each account. This string is then stored in Railway environment variables.

## Monitoring & Observability

**Heartbeat system:** All workers write to `worker_heartbeats` every 60 seconds. `last_seen_at > 5 minutes ago` = worker is dead.

**Job queue health:** `processing_jobs` table. `status='pending'` count = queue depth. `status='failed'` count = errors needing attention.

**Learning events:** Every student query produces a `learning_events` row. Zero-result rate, latency distribution, intent distribution — all queryable from this table.

**ai_runs table:** Every LLM API call is logged with tokens, cost, and duration. Daily spend is computable from `SUM(cost_usd) WHERE DATE(created_at) = TODAY`.

**weekly_report.py:** Generates a comprehensive health report and sends it to the ops Telegram channel every Monday at 8am AST.

---

# 7. DATABASE ENCYCLOPEDIA

*For each table: purpose, who writes, who reads, business importance, design intent.*

## Core Ingestion Tables

### `messages`
**Purpose:** Canonical store for all Telegram messages across all groups.
**Writers:** rumman_engine.py (live), telegram_backfill_worker.py (historical)
**Readers:** intelligence_worker.py, qa_mining_worker.py, message_signal_worker.py
**Unique constraint:** (platform_chat_id, platform_message_id) — this is the dedup key. A message that arrives via both live listener and backfill will produce exactly one row (409 on the second insert).
**Retention:** Permanent. Messages are the raw evidence layer. Never automated deletion.
**Growth expectation:** ~1,000–5,000 new messages/day from active SEU groups. Current: ~72,000 rows.
**Business importance:** The entire community intelligence layer depends on this table. It is the source of all non-official knowledge.

### `telegram_sync_state`
**Purpose:** Per-chat checkpoint: where we are in live and historical ingestion.
**Writers:** rumman_engine.py (newest_message_id), telegram_backfill_worker.py (oldest_message_id, backfill_completed)
**Readers:** telegram_backfill_worker.py (to resume from last position), rumman_engine.py (gap detection)
**Key columns:** newest_message_id (most recent live), oldest_message_id (how far back backfill has gone), backfill_completed (bool), last_live_seen_at (for gap detection)
**Design intent:** One row per chat. Workers use this as a bookmark; it is never the primary store for message content.

### `telegram_backfill_jobs`
**Purpose:** Work queue for historical message ingestion, with lease-based concurrency control.
**Writers:** rumman_engine.py (auto-creates on new chat discovery), create_backfill_jobs.py (manual)
**Readers/Workers:** telegram_backfill_worker.py exclusively
**Lifecycle:** pending → running (with lease) → completed | failed
**Design intent:** The lease mechanism (worker_id + lease_expires_at) prevents two workers from processing the same chat simultaneously. The condition PATCH pattern (`WHERE status='pending'`) is an optimistic lock at the database layer — no application-level locking needed.

### `processing_jobs`
**Purpose:** General async work queue for all media and embedding tasks.
**Writers:** telegram_backfill_worker.py, telegram_download_worker.py, rumman_engine.py, ingest_document.py
**Workers:** telegram_download_worker.py (audio_transcribe + telegram_media), embed_worker.py (embed_chunk), pdf_worker.py (pdf_extract)
**Key columns:** job_type, status, attempts (max 5), payload (JSONB with file metadata), lease_expires_at
**Current state:** ~15K+ media jobs stuck in pending/failed (TELEGRAM_MEDIA_IBRAHIM_SESSION not configured in Railway)
**Design intent:** Same lease pattern as telegram_backfill_jobs. A failed job increments attempts; at max_attempts it becomes permanently failed and requires manual reset_media_jobs.py intervention.

## Knowledge Layer Tables

### `source_documents`
**Purpose:** One row per ingested file. The metadata record for everything that feeds into document_chunks.
**Writers:** telegram_download_worker.py, ingest_document.py, batch_ingest_seu.py
**Readers:** embed_worker.py (to get text for chunking)
**Key columns:** content_hash (SHA256 dedup guard), authority_tier (official/verified/community), processing_status, course_code, exam_type, extraction_method
**Design intent:** The content_hash ensures a file ingested twice produces exactly one source_documents row (409 on second insert). This dedup happens at the file level, not the job level.

### `document_chunks`
**Purpose:** The retrieval corpus. Every chunk of every document, with its vector embedding.
**Writers:** embed_worker.py exclusively
**Readers:** search_api.py (via match_documents RPC), attribution_worker.py, refresh_course_profiles.py, gap_analyst.py
**Key columns:**
- `embedding vector(1536)` — the HNSW-indexed vector for similarity search
- `course_code` — the primary filter for course-specific search (NULL for 84% of rows currently)
- `authority_tier` — official/verified/community; used in synthesis prompt for source citation
- `attribution_status` — original/machine_asserted/confirmed/rejected; the claim model for course attribution
- `attribution_ai_run_id` — links to the ai_runs record that assigned this chunk's course_code
- `superseded_by` — FK to a newer version of this chunk (for re-ingestion without deletion)
**HNSW index:** m=16, ef_construction=64. pgvector's recommended defaults. Supports cosine similarity search at scale.
**Business importance:** This is the platform's primary intelligence asset. Every search result comes from this table.
**Growth expectation:** ~10K new chunks per month from active ingestion. Current: ~120K+.

## Platform Identity Tables

### `tenants`
**Purpose:** Multi-tenant anchor. Every data row in the system belongs to a tenant.
**Current state:** One row: Saudi Electronic University, id='00000000-0000-0000-0000-000000000001'
**Design intent:** The fixed UUID for SEU is deliberate — it makes debugging easier (recognizable in logs) and avoids UUID lookup overhead in the most common path.

### `rumman_users`
**Purpose:** Pseudonymous user identity. Maps platform identities to platform-agnostic hashes.
**Key design:** `platform_user_hash = SHA256(RUMMAN_USER_SALT + ":" + platform + ":" + raw_id)`. The raw Telegram chat_id is NEVER stored. This is privacy-by-design, not privacy-by-policy.
**Writers:** search_api.py `POST /v1/users/identify`
**Readers:** search_api.py (session management), student_context (personalization)

### `rumman_sessions`
**Purpose:** Per-session state. Tracks active course focus, enrolled courses, conversation context.
**TTL:** 30 minutes of inactivity. After TTL, a new session is created on the next interaction.
**Key columns:** active_course_code, active_exam_type, session_context (JSONB), turn_count
**Design intent:** Sessions are the bridge between stateless HTTP requests and stateful conversations. The bot sends session_id with every synthesize request; search_api loads session context to inject into synthesis.

### `student_context`
**Purpose:** Persistent cross-session memory for each student.
**Context types:**
- `enrolled_courses` — {"codes": ["IT362", "MGT311"]} — set via /mycourses command, expires never (explicit)
- `active_focus` — most recently queried course, expires 7 days
- `lang_pref` — Arabic or English preference, expires 30 days
- `study_pattern` — inferred study behavior, expires 30 days
**Confidence tiers:** high (explicit, never expires), medium (3+ observations), low (1-2 observations, 7 days)
**Business importance:** The enrolled_courses context makes the bot dramatically more useful. Instead of asking "which course?" on every question, the bot can inject "موادي: IT362 CS251" into the query to scope results.

## Intelligence Tables

### `intelligence_items`
**Purpose:** Structured operational items extracted from Telegram messages by intelligence_worker.py.
**Item types:** assignment, quiz, exam, deadline, meeting, decision, reminder, announcement
**Dedup constraint:** UNIQUE(tenant_id, source_platform, source_message_id, item_type) — one item type per message.
**Retention consideration:** Items with past due_dates should eventually be archived. Currently no auto-archival.
**Business importance:** This is how RUMMAN answers "when is the IT362 exam?" from live Telegram intelligence rather than relying only on official sources.

### `message_signals`
**Purpose:** Typed intelligence signals extracted from Telegram conversations (batch, not real-time).
**Signal types and their meaning:**
- `exam_emphasis` — professor or students explicitly flag something as exam-critical
- `difficulty` — repeated expressions of confusion or difficulty with a topic
- `professor_note` — direct instructor guidance shared in group
- `resource_rec` — students recommend specific study resources (videos, books, websites)
- `confusion_cluster` — multiple students asking the same question = knowledge gap
**Injection into synthesis:** The context block built for each synthesis call includes the top signals for the queried course, giving GPT awareness of what students and professors have emphasized.
**Refresh cadence:** Monthly, or after significant new message ingestion.

### `course_intelligence_profiles`
**Purpose:** Pre-computed per-course corpus summary.
**Key columns:** total_chunks, exam_chunks, official_chunks, coverage_level (none/thin/moderate/strong)
**Refresh:** `scripts/refresh_course_profiles.py` — pure SQL aggregation, no LLM, runs in minutes.
**Design intent:** Instead of computing "how much do we know about IT362?" at query time (expensive join), this is pre-computed and injected into the synthesis context block. GPT can then calibrate its confidence in the answer.

### `exam_intelligence`
**Purpose:** Top recurring exam topics per course, extracted by LLM from exam-tagged chunks.
**Refresh cadence:** One-time extraction, refresh monthly or when significant new exam content ingested.
**Injection:** Top topics for (course_code, exam_type) are injected into synthesis context to help GPT identify what's likely to appear on the exam.

## Observability Tables

### `ai_runs`
**Purpose:** Audit trail for every AI API call.
**Writers:** attribution_worker.py, intelligence_worker.py, daily_brief.py, any future AI worker
**Key columns:** worker, model, input_tokens, output_tokens, cost_usd, duration_ms, subject_type, subject_id, output_summary (NO raw content, NO PII)
**Business importance:** Cost monitoring (daily spend queries), quality debugging (find runs with unexpected output), provenance chain for every AI-generated attribution.

### `learning_events`
**Purpose:** Behavioral signal table. Every student interaction is an event.
**Event types:** query, synthesis, zero_result, feedback_positive, feedback_negative, session_start, session_end
**Key analytics queries:**
- Zero-result rate: `COUNT WHERE event_type='zero_result' / COUNT WHERE event_type='query'`
- Intent distribution: `GROUP BY intent_type`
- Latency p95: percentile on latency_ms WHERE event_type='synthesis'
- Cost: SUM of metadata.synthesis_tokens × cost_per_token WHERE event_type='synthesis'

### `worker_heartbeats`
**Purpose:** Worker liveness monitoring.
**Pattern:** Each worker upserts a row every 60 seconds with last_seen_at=now(), status.
**Alert threshold:** `last_seen_at < now() - 5 minutes` = worker is likely dead.

### `analysis_runs`
**Purpose:** Append-only log of batch analyst operations (gap_analyst, qa_miner, message_signal_miner).
**Design intent:** Never updated, never deleted. Full audit trail of when each analysis ran, what it found, and what it cost.

### `gap_items`
**Purpose:** Normalized knowledge gap records from gap_analyst runs.
**Gap types:**
- `content_gap` — avg_similarity < 0.20; nothing in corpus about this topic
- `retrieval_gap` — 0.20–0.40; content exists but isn't matching well
- `coverage_gap` — > 0.40; partial coverage, more content needed
**Resolution tracking:** `resolved_at`, `resolved_by` (ingest/extracted_item/manual). Gaps are marked resolved, not deleted.

## Academic / Institutional Tables

### `inst_colleges`, `inst_specializations`, `inst_courses`
**Purpose:** Structured SEU academic hierarchy. The "ground truth" about what the university offers.
**Writers:** seed_courses.py (one-time seed), manual updates
**Readers:** search_api.py (course name lookup), telegram_bot.py (course code validation)
**Note:** Renamed from `seu_colleges/seu_specializations/seu_courses` in migration 014 to support multi-tenancy. This is a deliberate forward investment.
**Current state:** 5 colleges, 21 specializations, 161 courses seeded with names (Arabic + English), descriptions, credit hours, prerequisites.

### `academic_calendar`
**Purpose:** SEU's official academic dates for the 1447H (2025–2026) academic year.
**Events:** semester_start, semester_end, add_drop_start/end, midterm_start/end, final_start/end, withdrawal_deadline, results_release, and more.
**Injection:** When a student asks "when is the exam?", the calendar is injected as a synthetic chunk with similarity=0.99 (effectively pinned to top of results).
**Refresh:** Manual update when each new academic year's calendar is published.

### `extracted_items` (from daily_brief)
**Purpose:** Operational items extracted from Telegram by daily_brief.py.
**Distinction from intelligence_items:** `extracted_items` is populated by daily_brief.py (sliding window, manual runs); `intelligence_items` is populated by intelligence_worker.py (real-time, continuous). Both feed the intelligence retrieval path in search_api.py.
**Validity:** `valid_until = '2026-06-25'` (current semester end). Items expire automatically via `active_extracted_items` view.
**Supersession:** New extraction of the same item sets `superseded_by` on the old row rather than deleting it (preserves audit trail).

---

# 8. END-TO-END DATA FLOWS

## Flow A: Live Telegram Message → Searchable Chunk

```
1. STUDENT SENDS MESSAGE in SEU Telegram group
   e.g., "دكتور قال الفصل الثالث كله سيجي بالاختبار مادة IT362"

2. LISTENER (rumman_engine.py)
   ← Telethon NewMessage event fires
   → Normalize: extract text, sender, chat_id, message_date, media_type
   → INSERT messages {platform_chat_id, platform_message_id, message_text, ...}
      HTTP 409? → duplicate, skip silently
   → UPDATE telegram_sync_state SET newest_message_id = this_message_id
   → No media in this message → nothing further

3. (If message had a PDF attachment):
   → INSERT processing_jobs {job_type='telegram_media', payload={file_id, message_id}}

4. MEDIA WORKER (telegram_download_worker.py)
   ← polling processing_jobs WHERE job_type IN ('telegram_media', 'audio_transcribe')
   → PATCH processing_jobs SET status='running', worker_id=me
   → download_media(file_id) → /tmp/file.pdf
   → PyMuPDF extraction → text
   → detect course_code from filename/caption → "IT362"
   → detect exam_type from filename → null (not an exam paper, just a note)
   → INSERT source_documents {content_hash, file_name, extracted_text, 
                               course_code='IT362', authority_tier='community'}
   → INSERT processing_jobs {job_type='embed_chunk', source_document_id=...}
   → PATCH processing_jobs SET status='completed'
   → delete /tmp/file.pdf

5. EMBED WORKER (embed_worker.py)
   ← polling processing_jobs WHERE job_type='embed_chunk'
   → load source_documents.extracted_text
   → NFKC normalize
   → split into chunks (paragraph-aware, 500 tokens, 50 overlap)
   → for each chunk:
      → embed with text-embedding-3-large → vector[1536]
      → INSERT document_chunks {content, embedding, course_code='IT362',
                                  authority_tier='community',
                                  attribution_status='original'}
   → UPDATE source_documents SET processing_status='chunked'
   → PATCH processing_jobs SET status='completed'

6. ATTRIBUTION WORKER (running continuously, background)
   ← fetch document_chunks WHERE course_code IS NULL AND attribution_status='original'
   → This chunk already has course_code='IT362' from step 4 → skipped

7. CHUNK IS NOW SEARCHABLE
   → document_chunks row with:
      content="دكتور قال الفصل الثالث كله..."
      embedding=[0.023, -0.041, ...]  (1536 floats)
      course_code='IT362'
      authority_tier='community'
      attribution_status='original'
```

## Flow B: Student Question → Bot Answer

```
1. STUDENT sends message to @RUMMAN_bot
   "وش يجي في اختبار IT362 الميدترم؟"

2. BOT (telegram_bot.py)
   ← long-poll getUpdates
   → check _GREETING_RE → no match
   → check _COURSE_CORRECTION_RE → no match
   → check _ACADEMIC_KEYWORDS → "اختبار" matches
   → route to academic query handler
   → load or create session via POST /v1/sessions
   → build query: "وش يجي في اختبار IT362 الميدترم؟ (موادي: IT362 CS251)"
   → POST /synthesize {query, session_id, user_id}

3. SEARCH API — Step 1: Static Normalization
   normalization_dict.json
   "وش يجي في اختبار IT362 الميدترم"
   → "ما الذي يظهر في اختبار منتصف الفصل IT362"

4. SEARCH API — Step 2: Intent Hints
   "اختبار" → hint: exam_topics, source_type: exam
   → hints=[{bias_intent: "exam_topics", bias_source_type: "exam"}]

5. SEARCH API — Step 3: Intent Classification (gpt-4o-mini, ~6ms)
   Sends: normalized query + hints
   Returns: {
     intent_type: "exam_topics",
     course_codes: ["IT362"],
     exam_type: "midterm",
     english_query: "IT362 midterm exam topics",
     confidence: 0.95
   }

6. SEARCH API — Step 4: Search Parameters
   course_code detected → course-filtered search
   builds: [
     SearchParams(query="ما الذي يظهر...", course_code="IT362", limit=10),
     SearchParams(query="IT362 midterm exam topics", course_code="IT362", limit=10)
   ]

7. SEARCH API — Step 5: Parallel Retrieval (asyncio.gather)
   a) Vector search pass 1 (Arabic query, IT362 filtered):
      → embed "ما الذي يظهر في اختبار منتصف الفصل IT362"
      → match_documents(embedding, filter_course='IT362', match_count=10)
      → returns 8 chunks, similarity 0.42–0.71
   b) Vector search pass 2 (English query, IT362 filtered):
      → embed "IT362 midterm exam topics"
      → match_documents(embedding, filter_course='IT362', match_count=10)
      → returns 6 chunks, some overlap with pass 1
   c) Intelligence items (exam intent):
      → query active_extracted_items WHERE course_code='IT362'
      → returns 0 rows (no recent items)
      → query intelligence_items WHERE course_code='IT362' AND item_type='exam'
      → returns 1 row: "الاختبار يشمل الفصول 1-5"
   d) Message signals (exam_topics intent):
      → exam_emphasis + difficulty + professor_note + confusion_cluster
      → returns: ["الفصل الثالث سيجي كله (3 messages)", "OSI model صعب (7 messages)"]
   e) Course profile:
      → course_intelligence_profiles WHERE course_code='IT362'
      → {coverage_level: "strong", has_exam_archives: true, total_chunks: 847}
   f) Exam intelligence:
      → exam_intelligence WHERE course_code='IT362' AND exam_type='midterm'
      → top_topics: ["OSI Model", "TCP/IP Stack", "HTTP Protocol", "DNS", "Subnetting"]

8. SEARCH API — Step 6: Deduplication + Re-rank
   → deduplicate by chunk id (14 chunks → 10 unique)
   → sort: official first, then by similarity
   → take top 8

9. SEARCH API — Step 7: Cache Check
   key = SHA256("ما الذي يظهر في اختبار منتصف الفصل it362|IT362|midterm")[:32]
   → miss (first time this query is asked today)

10. SEARCH API — Step 8: Context Block Assembly
    [سياق الطالب]
    الطالب يبدو مسجلاً (غير مؤكد) في: IT362 CS251
    
    [معلومات المادة IT362]
    مستوى التغطية: قوي | امتحانات متاحة: نعم | رسمي: نعم
    أبرز مواضيع الميدترم: OSI Model، TCP/IP Stack، HTTP، DNS، Subnetting
    
    [إشارات المجتمع]
    تأكيدات الاختبار: الفصل الثالث سيجي كله (3 رسائل)
    مواضيع صعبة: OSI model (7 رسائل)

11. SEARCH API — Step 9: Synthesis (gpt-4o-mini)
    System: "أجب فقط من المحتوى أدناه. لا تستخدم معرفتك..."
    Context: 8 chunks + context block
    Query: "ما الذي يظهر في اختبار منتصف الفصل IT362"
    
    → Answer: "بناءً على امتحانات سابقة وإشارات من المجتمع..."
    → tokens: ~800, cost: ~$0.0005

12. SEARCH API — Step 10: Logging (fire-and-forget)
    → INSERT learning_events (synthesis, IT362, latency=3200ms, grounded=true)
    → UPDATE student_context SET active_focus = {course_code: 'IT362', exam_type: 'midterm'}
    → store in synthesis cache (TTL 2h)

13. BOT
    ← receives synthesized answer
    → format for Telegram (markdown)
    → sendMessage to student
    → append to _HISTORY_CACHE (for follow-up questions)
```

## Flow C: Official Document → Searchable Corpus

```
1. NEW SEU DOCUMENT added to knowledge repository
   e.g., IT362_Study_Plan_2025.pdf

2. ADMIN runs:
   python3 scripts/ingest_document.py IT362_Study_Plan_2025.pdf \
     --source-type study_plan --course-code IT362

3. INGEST SCRIPT
   → SHA256(file_bytes) → content_hash
   → upload to Supabase Storage at: {tenant_id}/study_plan/IT362_Study_Plan_2025.pdf
   → INSERT source_documents {
       content_hash, storage_path, file_name,
       source_type='study_plan', course_code='IT362',
       authority_tier='official',   ← KEY: official docs get highest authority
       processing_status='pending'
     }
   → INSERT processing_jobs {job_type='pdf_extract', source_document_id=...}

4. PDF WORKER or MEDIA WORKER
   → download from Storage → /tmp/
   → PyMuPDF: extract text page by page
   → if image pages found → GPT-4o Vision OCR
   → UPDATE source_documents {extracted_text, page_count, extraction_method}
   → INSERT processing_jobs {job_type='embed_chunk'}

5. EMBED WORKER
   → chunk text (paragraph-aware for study plans)
   → embed each chunk
   → INSERT document_chunks {
       content, embedding, course_code='IT362',
       authority_tier='official',   ← propagated from source_document
       source_type='study_plan'
     }

6. RESULT: Official study plan content now retrievable
   → will rank above community content in synthesis (official > community)
   → contributes to course_intelligence_profiles.official_chunks count
   → retrieved synthesis will cite "[رسمي]" source tag
```

---

# 9. AI ARCHITECTURE

## Model Selection Rationale

### text-embedding-3-large (1536 dims)
**Why not ada-002?** text-embedding-3-large has significantly better Arabic language performance. Ada-002 was trained primarily on English; Arabic retrieval quality degrades noticeably.
**Why not text-embedding-3-small?** The quality gap in Arabic is larger than in English. At the scale of this corpus (~120K chunks), the marginal cost of large vs. small is ~$2/month. Not a material difference.
**Why 1536 dims, not 3072?** pgvector's HNSW index maximum is 2000 dimensions. 1536 fits comfortably; 3072 does not.

### gpt-4o-mini (default synthesis model)
**Why mini, not gpt-4o?** The synthesis task is constrained: GPT is given specific chunks and instructed to summarize from them. This is a compression task, not a reasoning task. gpt-4o-mini handles it well at ~7× lower cost than gpt-4o. The Arabic quality is adequate for this use case.
**When gpt-4o is used instead:**
- `comparison` intent (comparing two concepts requires more nuanced synthesis)
- `intent.confidence < 0.65` (uncertain intent → upgrade to smarter model)
- Complex OCR documents (image-based PDFs with Arabic handwriting)

### gpt-4o-mini (intent classification)
**Why not a smaller/faster classifier?** The intent classification prompt returns a full JSON structure with normalized text, English translation, course codes, exam type, etc. This is complex structured output. gpt-4o-mini handles Arabic intent reliably; smaller models produce more classification errors, especially for ambiguous Gulf Arabic queries.
**Timeout:** 6 seconds. On timeout, falls back to keyword-based routing (using the intent_hints that were already computed).

### whisper-1 (audio transcription)
**Only available Whisper API model.** No alternatives.

## Prompt Architecture

### Intent Classification Prompt (query_understanding.py)
**Function:** Transforms a raw Arabic student query into structured intent.
**Key design decisions:**
- Explicitly forbids the model from answering the question (it can only classify)
- Requires `english_query` always — even for "unknown" intents — because Arabic queries embed poorly against English corpus content
- `clarification_question` must be in Gulf Arabic, not MSA — students are more comfortable with dialect
- Hint injection: static keyword hints are prepended as "signals" with explicit instruction to not override if query contradicts them

### Attribution Classification Prompt (attribution_worker.py)
**Function:** Determine which SEU course a document chunk belongs to.
**Key constraint in prompt:** "Confidence ≥ 0.85 ONLY when code is explicitly in text OR content is unambiguously specific to exactly one course. Never guess based on topic alone."
**Why this matters:** Algorithms could be CS101, CS201, IT201, or a dozen other courses. Topic-based guessing produces mass attribution errors.

### Synthesis System Prompt (search_api.py)
**Function:** Generate a grounded answer from retrieved chunks.
**Anti-hallucination instructions:**
1. Answer ONLY from the content below
2. Do NOT use training knowledge about SEU, courses, or professors
3. If the answer is not in the content, say: "لا أجد معلومة عن هذا في المحتوى المتاح"
4. Cite source tier: [رسمي] for official, [مجتمع] for community
**Bilingual instruction:** Arabic for Arabic content, English for English content.

### Intelligence Extraction Prompt (intelligence_worker.py)
**Function:** Extract structured operational items from Telegram messages.
**Conservative bias:** "When in doubt, omit." An empty `{"items": []}` is explicitly stated as valid and often correct. This prevents over-extraction of false positives.
**Confidence calibration:** 0.9+ = explicitly stated; 0.7–0.89 = clearly implied; below 0.65 = do not store.

## Routing Logic

```
query arrives at /synthesize
  ↓
normalized? → compare_intent? → gpt-4o
                             → default → gpt-4o-mini
low confidence (<0.65)?   → gpt-4o
clarification needed?     → no synthesis → return question
cached?                   → return cached response
everything else           → gpt-4o-mini synthesis
```

## Attribution Mechanism (Claim Model)

Every chunk's course_code has a tracked origin:

```
attribution_status = 'original'
  ← set at ingest time from source metadata (filename, explicit parameter)
  ← most reliable: human explicitly specified

attribution_status = 'machine_asserted'
  ← set by attribution_worker.py
  ← requires confidence ≥ 0.85
  ← linked to specific ai_runs row
  ← can be bulk-reverted if quality degrades

attribution_status = 'confirmed'
  ← human-validated or downstream logic confirmed
  ← graduates to operational truth

attribution_status = 'rejected'
  ← AI was wrong; course_code cleared; chunk re-queued
```

## Confidence System

Multiple confidence scores coexist:
- `intent.confidence` — how certain the intent classifier is about intent type
- `attribution_confidence` — how certain attribution_worker is about course assignment
- `extracted_items.confidence` — how certain intelligence_worker is about an item
- `message_signals.confidence` — how certain signal extraction is about a signal

Each threshold is set independently based on the cost of false positives in that context:
- Attribution: 0.85 (false attribution contaminates search)
- Intelligence items: 0.65 (false item is just noise, not harmful)
- Signals: 0.65 (signal noise is acceptable, signal miss is costly)

## Failure Handling

**Intent classifier timeout (6s):** Fall back to keyword-based routing from intent_hints. The query still gets a useful (if less precise) search.

**OpenAI synthesis timeout (25s):** Fall back to returning raw retrieved chunks without synthesis. Students get the source material directly.

**Zero results:** Return explicit "لا أجد معلومة" rather than synthesizing an empty context. This is the correct UX — silence is better than fabrication.

**Attribution LLM failure:** Log error, skip chunk, continue. The chunk remains unattributed; attribution_worker will retry it next cycle.

## Cost Optimization Strategies

**Cache:** Synthesis cache (2h TTL, 1000 entries LRU) eliminates repeated API calls for popular queries. During exam season, cache hit rate may reach 60-80%.

**Regex-first attribution:** Every chunk that has an explicit course code in its text is attributed for free. At current corpus size, this handles ~30-40% of attributable chunks.

**Intent hints:** Static keyword hints reduce intent classification load — if the hints give high-confidence signal, the classification prompt is still called (for structured output) but the model has a "head start" reducing thinking tokens.

**Daily caps:** `ATTRIBUTION_MAX_DAILY_CALLS=3000` prevents attribution_worker from consuming the shared gpt-4o-mini RPD budget in one day.

**Model routing:** Only comparison and low-confidence queries use gpt-4o. Everything else uses gpt-4o-mini.

---

# 10. TELEGRAM ECOSYSTEM

## Why Telegram?

SEU students are native Telegram users. The academic communication ecosystem at Saudi universities exists almost entirely on Telegram:
- Official course announcements from professors
- Peer study groups per course
- Past exam paper sharing
- Last-minute exam tip sharing
- General university news channels

WhatsApp exists but is fragmented and private. Discord exists but is less used. Telegram is where the institutional intelligence lives.

## Group Taxonomy

**SEU Telegram groups fall into categories:**
1. **Official college channels** — managed by the college; official announcements
2. **Course-specific groups** — students + sometimes professors; per-course discussion
3. **General SEU groups** — university-wide news, general help
4. **Year cohort groups** — students from the same graduation year
5. **Program-specific groups** — all students in a given major

**Current coverage:** 79+ active groups being monitored.

## Content Types in Telegram

| Content Type | Format | Processing Path | Value |
|-------------|--------|-----------------|-------|
| Text messages | UTF-8 text | Direct to messages table | High — contains exam tips, announcements |
| PDF documents | Binary | media worker → PyMuPDF | Very high — often exam papers, summaries |
| Images (screenshots of slides) | Binary | media worker → GPT-4o Vision OCR | High — lecture content |
| Voice notes | OGG/OPUS audio | media worker → Whisper | Medium — sometimes exam guidance |
| Photos of handwritten notes | Binary | media worker → GPT-4o Vision | Medium |
| Forwarded messages | Text (re-ingested) | Same as text messages | Variable |
| Links | Text only | Not followed | Low (not processed) |

## Collection Methods

**Live collection:** rumman_engine.py (غيث account) receives all messages in real time via Telethon user client. The bot is a member of each monitored group.

**Historical collection:** telegram_backfill_worker.py (راوي account) downloads message history for each group via iter_messages. This is the mechanism for getting pre-RUMMAN history.

**Gap filling:** If the live listener misses messages during downtime, rumman_engine.py detects message ID gaps (> 10 jump) and automatically creates gap-fill jobs.

## Expansion Strategy

**Adding new groups:**
1. Join the group with راوي or إبراهيم account (manually, 20-30/day to avoid rate limits)
2. Run `python3 scripts/create_backfill_jobs.py --chat-id {chat_id}` to queue historical ingestion
3. Backfill worker picks it up automatically

**Blocked groups (18 currently):** Some groups require admin approval. Need a connection to the group admin or an SEU student who is a member.

**Public group discovery:** `scripts/export_group_links.py` generates invite links for all known groups. Used to share with team members who can then join and expand coverage.

## Operational Challenges

**Rate limiting:** Telegram rate-limits user accounts aggressively during bulk backfill. The 3-second delay between backfill batches and FloodWaitError handling were added to stay within limits.

**Session conflicts:** The fundamental constraint is one Telegram StringSession per concurrent connection. Running two processes with the same session produces `AuthKeyDuplicatedError` immediately. The three-account architecture permanently solves this.

**Content filtering:** Not all Telegram content is academically relevant. A message saying "هلا شباب" has no value. The system doesn't filter at ingestion (everything is stored), but:
- QA mining worker requires meaningful Q&A pairs (confidence ≥ 0.70 to store)
- Signal extraction requires confidence ≥ 0.65
- Intelligence worker requires confidence ≥ 0.65
Low-value content is stored but not extracted or surfaced.

---

# 11. ACADEMIC INTELLIGENCE LAYER

## University Structure

**Saudi Electronic University (SEU):**
- Distance-learning university
- ~200,000+ students enrolled
- 5 colleges: Computing & Informatics, Administrative & Financial Sciences, Law, Health Sciences, General Studies
- 21 specializations
- 161 courses seeded in inst_courses

**Academic calendar (1447H):**
- Two main semesters + summer
- Semester structure: ~15 weeks
- Mid-term exams: around week 7-8
- Final exams: last 2 weeks of semester

## Course Intelligence Profiles

Each course in the corpus has a pre-computed profile (`course_intelligence_profiles`):
- `total_chunks`: how many document chunks are associated with this course
- `exam_chunks`: how many are tagged as exam-type content
- `official_chunks`: how many come from official SEU documents
- `coverage_level`: none/thin/moderate/strong

**Coverage thresholds:**
- strong: 30+ exam chunks OR 60+ total chunks
- moderate: 10+ exam chunks OR 30+ total chunks
- thin: any content but below moderate
- none: no content tagged to this course

**Current distribution (approximate):**
- strong: ~40-50 courses (major courses with lots of community content)
- moderate: ~60-80 courses
- thin: ~100-120 courses
- none: remaining courses (minor, elective, or Law school courses)

## Exam Intelligence

`exam_intelligence` table stores the top 5-8 recurring topics per (course, exam_type) pair. These are extracted by `scripts/extract_exam_signals.py`:
- Reads all exam-tagged chunks for a course
- Calls gpt-4o-mini to identify recurring themes
- Stores as JSON array: `["OSI Model", "TCP/IP", "HTTP Protocol", ...]`

This is injected directly into the synthesis context, giving the model awareness of what historically appears on exams.

## Knowledge Repository

The official SEU knowledge repository lives outside the git repo at:
```
.../0-RUMMAN/0-Universities/1- Saudi Electronic University/
  0. OpenData/          — enrollment statistics, faculty data
  1. StudyPlans/        — degree plans by college → dept → program
  2. Regulations/       — exam rules, student guides, procedures
  3. AcademicCalendar/  — semester dates (TXT/PDF)
  4. CourseContent/     — ENGT program syllabi (34 files)
  5. Diplomas/          — Applied College diploma programs
  _metadata/            — knowledge_manifest.json, program_index.json
```

**153 documents ingested** as of 2026-06-01. All 153 are embedded and searchable. Future additions (new academic year documents) follow the same `ingest_document.py` process.

## Gap Analysis

`scripts/gap_analyst.py` identifies knowledge gaps from `learning_events` zero-result queries:
- Clusters queries by course code and topic
- Classifies as content_gap / retrieval_gap / coverage_gap
- Stores in `gap_items` with example queries and severity scores
- Output helps prioritize which content to add next

---

# 12. SEARCH ARCHITECTURE

## Architecture Overview

RUMMAN uses **semantic vector search** as its primary retrieval mechanism. There is no keyword search. Every query is embedded and compared against document_chunk embeddings via cosine similarity.

**Why no keyword search?** Arabic has extensive morphological variation. "يذاكر" (studying) and "دراسة" (study) are semantically related but lexically different. A keyword search would miss many relevant results. Semantic embedding captures meaning regardless of surface form.

## Similarity Thresholds

Two thresholds based on search scope:

| Search Type | Threshold | Rationale |
|-------------|-----------|-----------|
| Course-filtered (course_code specified) | 0.25 | Scope is already tightly constrained to one course. Lower threshold to avoid missing relevant content. |
| Broad (no course_code) | 0.40 | No scope constraint. Higher threshold prevents irrelevant results from other courses. |

**Why these specific values?** Arabic queries against an embedding model trained primarily on English produce lower similarity scores. Testing showed that course-specific queries typically hit 0.28–0.45; a threshold of 0.25 captures all relevant results while filtering noise. Broad search at 0.40 prevents multi-course contamination.

## Multi-Pass Strategy

For course-specific queries, two search passes run in parallel:
1. Arabic normalized query
2. English translation of the query

Arabic-medium course content (IT362, CS241, etc.) contains significant English technical terminology. A query about "بروتوكول HTTP" should match chunks containing "HTTP Protocol", "HTTP/1.1", "Hypertext Transfer Protocol". The English pass catches these.

Results from both passes are deduplicated and merged.

## Re-ranking

After retrieval, chunks are sorted by:
1. Authority tier (official > verified > community)
2. Similarity score (descending)

This ensures official university documents appear first when both official and community content covers the same topic.

## Source Diversification

The synthesis receives:
- Vector search results (chunks from document_chunks)
- Academic calendar entries (synthetic chunks, similarity=0.99)
- Intelligence items (from extracted_items / intelligence_items, similarity=confidence value)

Each source type informs different parts of the answer. Calendar provides temporal grounding; intelligence items provide recent community announcements; vector search provides deep course content.

## Synthesis Cache

**Key:** SHA-256(normalized_query.lower() | course_code | exam_type)[:32]
**TTL:** 2 hours
**Max entries:** 1000 (LRU eviction)

During exam season, many students ask semantically identical questions. The cache collapses these to one API call per 2-hour window. Cache hits return in < 200ms vs. 5-8s for a full synthesis.

---

# 13. MEDIA PROCESSING ARCHITECTURE

## PDF Processing

**Path 1: Digital PDF (text-based)**
```
PyMuPDF → extract text per page → concatenate → chunk → embed
```
- Fastest path, highest quality
- Detection: if page.get_text() returns meaningful content

**Path 2: Mixed PDF (some digital, some image pages)**
```
PyMuPDF → digital pages extracted
GPT-4o Vision → image pages OCR'd
Combined → chunk → embed
```

**Path 3: Image-only PDF (scanned document)**
```
PyMuPDF detects no extractable text
GPT-4o Vision → OCR each page (max 10 pages per document to cap cost)
Combined → chunk → embed
```

**Cost cap:** Max 10 pages per document for Vision OCR. If a document has 50 image pages, only the first 10 are OCR'd. This prevents a single large scanned document from consuming $5+ in API costs.

## Audio Processing

```
Download audio from Telegram → /tmp/
OGA/OPUS format → convert to OGG (for Whisper compatibility)
OpenAI Whisper API → transcript (Arabic or mixed)
Store in media_files {transcript, duration_ms, language}
```

Audio files are typically voice notes from professors or students. They often contain valuable exam guidance ("الاختبار سيشمل كذا وكذا"). Transcription captures this.

## Image Processing (Screenshots)

Many students share screenshots of:
- PowerPoint slides (most common)
- Textbook pages
- Whiteboard photos
- Exam papers

These go through GPT-4o Vision OCR. Arabic text recognition is generally high quality for digital screenshots; handwritten Arabic is less reliable.

## Current Limitations

**No DOCX/PPTX processing:** Microsoft Office formats are not currently extracted. Most SEU content circulates as PDF, so this gap is minor but documented.

**No video processing:** Videos shared in Telegram are not processed. Audio-only extraction of videos is technically possible but not implemented.

**Max 10 pages OCR:** Large scanned documents are truncated. For official documents, ingest_document.py should be used (which has a different path through pdf_worker.py with full extraction).

**Handwritten Arabic:** GPT-4o Vision quality varies significantly. Confidence scoring helps — low-confidence OCR is stored but marked accordingly.

---

# 14. MEMORY ARCHITECTURE

## Types of Memory

RUMMAN has four distinct memory systems at different temporal scopes:

### 1. Corpus Memory (Permanent)
**Table:** `document_chunks`
**Scope:** All knowledge ever ingested into the platform
**Duration:** Permanent (superseded_by for versioning, never deleted)
**Content:** Chunked text from all sources with embeddings
**Mechanism:** Vector similarity search

This is what makes RUMMAN answer questions. Every PDF, every Telegram message, every course description lives here permanently.

### 2. Intelligence Memory (Temporal)
**Tables:** `extracted_items`, `intelligence_items`
**Scope:** Recent operational events at the university
**Duration:** Until `valid_until` date (current semester end) or manually closed
**Content:** Exams, deadlines, assignments, announcements
**Mechanism:** Direct DB query filtered by date and course_code

This is what makes RUMMAN answer "when is the exam?" or "is there an assignment due this week?".

### 3. Student Context Memory (Per-User)
**Table:** `student_context`
**Scope:** Individual student preferences and history
**Duration:** Explicit (never), medium (30 days), low (7 days)
**Content:** Enrolled courses, language preference, active focus course, study patterns
**Mechanism:** Loaded at query time, injected into synthesis context

This is what makes RUMMAN feel personalized. A student who has told the system they're in IT362 doesn't need to repeat it every time.

### 4. Signals Memory (Aggregated)
**Tables:** `message_signals`, `exam_intelligence`, `course_intelligence_profiles`
**Scope:** Aggregated intelligence about courses (not individual messages)
**Duration:** Refreshed monthly, or after significant new ingestion
**Content:** What students struggle with, what appears on exams, what resources help
**Mechanism:** Pre-aggregated, injected into synthesis context block

This is the "wisdom of the crowd" layer — not individual messages, but patterns extracted from hundreds of messages about a course.

## Memory Evolution

The memory system is designed to grow in fidelity over time:

**Month 1:** Corpus memory mostly empty, student context cold, no signals
- Answers are generic, based on official documents + whatever backfill found

**Month 3:** Backfill complete for all groups, exam archives indexed, first signals extracted
- Exam-topic answers become specific and accurate
- Student context begins accumulating

**Month 6:** Signal extraction running monthly, intelligence worker catching real-time events
- Answers include "students say this topic is hard", "professor mentioned X this week"
- Student context at medium/high confidence for active users

**Year 1+:** Full corpus, mature signals, historical pattern recognition
- "This course tends to have Subnetting on the midterm" (pattern from 3+ years of archives)
- Genuine operational intelligence, not just retrieval

---

# 15. OPERATIONAL INTELLIGENCE VISION

## The Transformation Path

RUMMAN is currently a **knowledge retrieval system** (a very good one). The strategic vision is to evolve it into an **operational intelligence system** — one that not only answers questions but proactively surfaces what matters without being asked.

**Current state (Phase 2):** Student asks a question → system retrieves and synthesizes an answer.

**Target state (Phase 4+):** System proactively tells students: "Your IT362 midterm is in 3 days. Based on past exams and what the professor emphasized this week, focus on OSI Model and Subnetting. Three students have already asked about TCP/IP — here's the best explanation in the corpus."

## Intelligence Capabilities on the Roadmap

### Proactive Exam Alerts
When intelligence_worker detects a message like "الميدترم الأسبوع القادم يوم الاثنين", it creates an intelligence_item. Future capability: push a notification to all enrolled students 72 hours before detected exam dates.

### Knowledge Gap Notifications
`gap_analyst.py` already identifies topics that students repeatedly ask about with zero results. Future capability: automatically notify the admin when a high-frequency gap is detected ("Students are asking about IT362 Chapter 5 — no content in corpus").

### Coverage Dashboard
`course_intelligence_profiles` already has coverage_level per course. Future capability: admin dashboard showing which courses are well-covered, which need more content, and which have high query volume but poor results.

### Confusion Pattern Detection
`message_signals` with signal_type='confusion_cluster' already captures topics where multiple students express confusion. Future capability: automatic notification to professor groups when a confusion cluster reaches a threshold.

### Personalized Study Plans
`user_concept_history` + `student_context` can eventually support: "Based on your query history, you've struggled with networking concepts. Here are the 5 most important chunks from the corpus on that topic."

## The Multi-University Vision

RUMMAN's architecture was built for multi-tenancy from day one:
- All data rows have `tenant_id`
- The institutional layer (inst_courses, inst_colleges) is university-generic
- The knowledge repository contract (folder structure) is repeatable

Adding a second university:
1. Create tenant row in `tenants`
2. Seed inst_colleges/inst_specializations/inst_courses for new university
3. Join new university's Telegram groups with a dedicated account
4. Run backfill jobs
5. The entire search/synthesis pipeline works without modification

## The B2B Intelligence Vision

Long-term strategic direction: sell the analytical layer to universities themselves.

**University admin dashboard:**
- "What are students most confused about in COMP 201 this semester?"
- "Which courses have the best student-community knowledge coverage?"
- "What topics come up in exam prep queries that our official content doesn't address?"

This creates a data flywheel: more students → more queries → better gap detection → better content → better answers → more students.

---

# 16. SECURITY & PRIVACY MODEL

## Privacy-by-Design Principles

**No raw identifiers.** Telegram chat_id is a personally-identifying platform identifier. RUMMAN never stores it. The moment a chat_id arrives, it is hashed:
```
platform_user_hash = SHA256(RUMMAN_USER_SALT + ":telegram:" + str(chat_id))
```
Without the salt, the hash is not reversible. With the salt, it is (one-way) deterministic — the same student always gets the same hash, enabling persistent context without storing their identity.

**Opt-out memory.** `rumman_users.opted_into_memory = false` causes the system to skip all reads and writes to `student_context`. A student who doesn't want personalization gets none.

**No raw content in AI logs.** `ai_runs.output_summary` is a short human-readable summary. The full prompt and response are never stored. This prevents a data breach of `ai_runs` from revealing student query content at scale.

## Tenant Isolation

**Structural, not just policy.** Every operational table has `tenant_id`. Every PostgREST query must include `WHERE tenant_id = {tenant_id}`. This is enforced by application code, not database RLS (Row Level Security), because the service role key bypasses RLS. Tenant isolation correctness is verified by code review.

**Storage isolation.** Supabase Storage paths are namespaced by tenant_id: `{tenant_id}/{source_type}/{filename}`. A presigned URL for one tenant's file cannot be used to access another tenant's file.

## Secret Management

**Secrets live in Railway environment variables only.** Never in code, never in comments, never in git history.

**Gitignored files:**
- `.env` — local development secrets
- `*.session` — Telethon session files (never used in production; StringSession stored in Railway env vars)
- `auth_session.py` — one-time session string generator

**Rotation:** If any secret is compromised, rotation requires:
1. Generate new credential at the source (Telegram, Supabase, OpenAI)
2. Update Railway environment variable
3. Redeploy affected services

## Auditability

**ai_runs:** Every LLM API call is logged with worker, model, tokens, cost, subject. Auditable question: "How much did attribution cost last month?" → `SELECT SUM(cost_usd) FROM ai_runs WHERE worker='attribution_worker' AND DATE_TRUNC('month', created_at) = '2026-05-01'`

**Provenance chain:** For any document_chunk with `attribution_status = 'machine_asserted'`, the full audit trail is: chunk → ai_runs (which run assigned this code?) → worker (which version of attribution_worker ran?) → model (which GPT model?)

**Learning events:** Every student query is logged (without PII, using user_hash). Auditable: "How many queries came from this user hash last week?" This supports abuse detection without storing PII.

---

# 17. COST MODEL

## Current Monthly Costs (Approximate)

### OpenAI API

| Operation | Volume | Cost/Unit | Monthly Est. |
|-----------|--------|-----------|--------------|
| text-embedding-3-large (ingestion) | ~50K tokens/day | $0.13/1M | ~$0.20 |
| gpt-4o-mini (intent classification) | ~100 queries/day × 400 tokens | $0.30/1M in | ~$0.40 |
| gpt-4o-mini (synthesis) | ~100 queries/day × 1000 tokens | $0.30/1M + $1.20/1M out | ~$1.50 |
| gpt-4o-mini (attribution) | 3000 calls/day × ~300 tokens | $0.30/1M | ~$2.70 |
| gpt-4o-mini (intelligence) | ~200 messages/day × ~500 tokens | $0.30/1M | ~$0.90 |
| Whisper (audio) | ~50 minutes/month | $0.006/min | ~$0.30 |
| **OpenAI Total** | | | **~$6/month** |

### Railway
- 8 processes running continuously
- Estimated: ~$15–25/month depending on compute allocation

### Supabase
- Free tier covers current usage
- Upgrade needed if: storage > 1GB, DB rows > 50K (not yet), bandwidth > 5GB
- Estimated: $0–25/month

**Current total: ~$20–55/month**

## Cost Scaling

| Growth Event | Cost Impact |
|-------------|-------------|
| 10× query volume | +$15/month (synthesis dominates) |
| Second university onboarded | +$10/month (ingestion + attribution for new corpus) |
| Attribution worker completing full corpus | Attribution cost decreases to near-zero after corpus is tagged |
| Synthesis cache hit rate 70% | Synthesis cost drops by 70% |
| intelligence_worker running full speed | +$5/month |

## Financial Risks

**Runaway attribution:** If attribution_worker's daily cap (`ATTRIBUTION_MAX_DAILY_CALLS=3000`) is accidentally removed or corrupted, it could process the entire 21K unattributed chunk backlog in one day at ~$5+ cost.

**No per-tenant cost ceiling:** Currently there is one tenant (SEU). As multi-tenancy scales, a tenant with 100× the query volume could dominate costs. Per-tenant daily cost caps must be implemented before multi-tenant launch.

**Synthesis cache cold start:** After a Railway restart, the synthesis cache is empty. First hour after restart may have 10× normal API costs until cache warms up.

## Optimization Opportunities

1. **Synthesis cache persistence:** Currently in-memory (lost on restart). Persisting to Postgres/Redis would eliminate cold-start cost spikes.
2. **Embedding deduplication:** The same text chunk shouldn't be embedded twice. The content_hash + dedup on source_documents provides this for files; similar dedup for individual messages would reduce embedding costs.
3. **Batch embedding:** OpenAI supports batch embedding API calls (cheaper than individual calls). embed_worker currently calls one chunk at a time.

---

# 18. TECHNICAL DEBT REGISTER

*Ordered by severity: Critical → High → Medium → Low*

## Critical

### C1: No Per-Tenant Cost Ceiling
**Problem:** No mechanism prevents runaway AI costs per tenant. A loop bug or malicious input could generate thousands of API calls.
**Risk:** Financial. A bug in attribution_worker processing logic could cost $100+ in one night.
**Fix:** Add `tenant_settings` table with `daily_ai_budget_usd`. Workers check before each API call.
**Estimated effort:** 1 day

### C2: 15K+ Media Jobs Stuck Pending
**Problem:** TELEGRAM_MEDIA_IBRAHIM_SESSION not configured in Railway. All media jobs that arrived since session var rename are stuck.
**Risk:** 15K+ student files (PDFs, images) not processed. Significant corpus gap.
**Fix:** Ibrahim must set TELEGRAM_MEDIA_IBRAHIM_SESSION in Railway, then run reset_media_jobs.py.
**Estimated effort:** 10 minutes (for Ibrahim)

## High

### H1: 84% of Chunks Unattributed
**Problem:** 84% of document_chunks have null course_code. These are invisible to course-filtered search.
**Impact:** Search quality for specific course queries severely limited.
**Status:** attribution_worker running at 3K/day. At this rate, full attribution takes 4-6 weeks.
**Optimization opportunity:** Increase ATTRIBUTION_MAX_DAILY_CALLS now that RPD budget is better understood.

### H2: No Synthesis Cache Persistence
**Problem:** Synthesis cache is in-memory and lost on every Railway restart.
**Impact:** Post-restart cost spike; degraded response time for 30-60 min after restarts.
**Fix:** Persist cache to Postgres (simple table with key, payload, ts columns).
**Estimated effort:** 2 hours

### H3: 41 Unjoined Public SEU Groups
**Problem:** 41 public SEU Telegram groups not yet joined by راوي/إبراهيم.
**Impact:** Missing community intelligence from those groups. Unknown content value.
**Fix:** Manual join (20-30/day limit). Operational task, not engineering.
**Estimated effort:** 3-5 days of manual joining

### H4: Layer 2 Not Formally Built
**Problem:** The three-layer architecture (ADR-0005) calls for a formal Layer 2 (Knowledge Layer). Current code has Layer 2 logic embedded in Layer 1 workers (embed_worker.py, telegram_download_worker.py).
**Impact:** Extraction is not replayable as designed. Improving OCR model requires re-running all worker code, not just Layer 2.
**Fix:** Formalize Layer 2 as a separate worker set with extraction_jobs queue interface.
**Estimated effort:** 2-3 weeks of refactoring

## Medium

### M1: No RLS (Row Level Security)
**Problem:** Tenant isolation is enforced by application code (WHERE tenant_id = ?). Supabase RLS policies would enforce it at the DB layer, preventing any accidental query returning cross-tenant data.
**Impact:** Single point of failure — a bug in application code could leak data.
**Fix:** Enable RLS on all operational tables; add policies for service_role key.
**Estimated effort:** 1-2 days

### M2: Query Logs vs Learning Events Duplication
**Problem:** Both `query_logs` (migration 006) and `learning_events` (migration 007) exist. search_api.py writes only to learning_events. query_logs is populated by an older code path.
**Impact:** Operational confusion about which table is the source of truth.
**Fix:** Deprecate query_logs; migrate any analytics queries to learning_events.
**Estimated effort:** 4 hours

### M3: No DOCX/PPTX Support
**Problem:** Many SEU official documents and student notes are in DOCX/PPTX format. Currently unprocessed.
**Impact:** Missing content from non-PDF files.
**Fix:** Add python-docx and python-pptx extraction paths to telegram_download_worker.py.
**Estimated effort:** 1 day

### M4: Synthesis Cache Not Invalidated on New Content
**Problem:** When new document_chunks are added for a course (from fresh ingestion), the synthesis cache still serves the old answer for 2 hours.
**Impact:** Students may receive slightly stale answers immediately after new content is ingested.
**Fix:** Add cache invalidation on relevant course_code when new chunks are indexed.
**Estimated effort:** 4 hours

### M5: FIN416 Content Gap
**Problem:** Islamic Finance (FIN416 — التمويل الإسلامي) has no official PDF materials in the knowledge repository. Murabaha, Ijara, Sukuk queries return zero official results.
**Fix:** Ibrahim to add official FIN416 materials to knowledge repository and run ingest.
**Estimated effort:** 1 hour (operational)

## Low

### L1: improvement_candidates Table Unpopulated
**Problem:** The `improvement_candidates` table (migration 006) was designed to capture normalization and intent hint improvement candidates. It is never written to by current code.
**Impact:** The normalization improvement loop is manual (generate_seed_lexicon.py → review_candidates.py → add to JSON).
**Fix:** Connect gap_analyst.py and zero-result event processing to write improvement candidates automatically.

### L2: concepts/chunk_concepts/concept_relationships Tables Empty
**Problem:** Three tables from the concept layer (migration 007) were designed for concept-level retrieval and user concept history. Never populated.
**Impact:** The "what am I struggling with?" use case is not implemented.
**Fix:** Run extract_concepts.py to populate concepts; link to chunks.
**Estimated effort:** 1-2 weeks (concepts need to be designed, not just extracted)

### L3: Missing PPTX Processing
**Problem:** PowerPoint presentations are a primary format for SEU lecture slides. Not processed.
**Fix:** Add python-pptx extraction to media worker.

---

# 19. HARD CONSTRAINTS

*These cannot be violated. The reason for each constraint is as important as the constraint itself.*

## C1: Live listener never crawls history
**Rule:** `rumman_engine.py` must not call `iter_messages`, must not crawl history, must not perform backfill inline. `ENABLE_BACKFILL = False` is a hard guard, not a config option.

**Why:** Merging live and historical ingestion caused startup delays, Telegram rate-limit cascades, and unstable pipelines. This failure mode was experienced before ADR-0002 was written. Recreating it would require the same weeks of debugging.

## C2: One Telegram StringSession per running process
**Rule:** No two processes may use the same StringSession simultaneously.

**Why:** Telegram invalidates both sessions when it detects concurrent use of the same credentials. This manifests as `AuthKeyDuplicatedError` and effectively locks out both processes.

## C3: Raw artifacts are never deleted by automation
**Rule:** Messages, audio files, and documents are ingested once and retained permanently. No automated process deletes them.

**Why:** Extraction quality improves over time. If a document is deleted after extraction, re-processing with a better model is impossible. The raw artifact is the audit record.

## C4: AI outputs require provenance
**Rule:** Every row in extracted_items, intelligence_items, and any future claim table must have a reference to the ai_runs record that produced it.

**Why:** Without provenance, wrong extractions cannot be identified, bulk-reverted, or audited. Provenance cannot be reconstructed retroactively. The schema enforces this.

## C5: Machine-asserted claims are not treated as facts
**Rule:** attribution_status='machine_asserted' means a hypothesis, not confirmed truth. Code that reads attribution must acknowledge this status.

**Why:** Attribution at 85% confidence means 15% of machine-asserted attributions are wrong. Treating them as facts would corrupt 15% of course-specific search results.

## C6: No secrets in git
**Rule:** `.env`, `*.session`, `auth_session.py`, and any file containing API keys are gitignored and must never be committed.

**Why:** Committed secrets are in git history permanently. Rotation requires history rewrite. The only safe path is never committing them.

## C7: No cross-tenant data access
**Rule:** Every query against an operational table must include `WHERE tenant_id = {specific_tenant_id}`. No query may return data from multiple tenants without explicit authorization.

**Why:** This is both a privacy and legal requirement. Cross-tenant data access is a data breach regardless of intent.

## C8: No binary blobs in Postgres
**Rule:** Audio files, images, PDFs, videos — stored in Supabase Storage, not as Postgres columns.

**Why:** Binary blobs in Postgres degrade table compaction, increase backup size, slow queries, and don't benefit from relational capabilities. Supabase Storage handles this correctly.

## C9: Default model is Sonnet for CLAUDE.md-governed AI work
**Rule:** When using Claude Code (this tool) for RUMMAN development, the default model is Sonnet. Opus requires explicit per-task approval.

**Why:** Opus is ~5× the cost of Sonnet. For code editing, documentation, and analysis, Sonnet is sufficient. This keeps inference costs predictable.

## C10: Philosophy and constraints docs are invariant
**Rule:** `docs/philosophy/` and `docs/constraints/` must not be modified autonomously by AI systems.

**Why:** These documents encode foundational beliefs and constraints. A subtly modified constraint document can corrupt reasoning across the project for months.

---

# 20. ROADMAP

## 30 Days

**Unblock the pipeline (operational):**
1. Ibrahim sets `TELEGRAM_MEDIA_IBRAHIM_SESSION` in Railway → unblocks 15K+ media jobs
2. راوي/إبراهيم join remaining 41 public SEU groups (20-30/day)
3. Monitor attribution_worker progress — consider increasing daily cap from 3K to 5K

**Content gaps:**
4. Add FIN416 official materials to knowledge repository and ingest
5. Add any new MGT425 materials available

**Quality improvements:**
6. Run `refresh_course_profiles.py` after attribution worker makes significant progress
7. Run `gap_analyst.py` to identify most impactful knowledge gaps after attribution improves

## 90 Days

**Intelligence layer:**
8. Confirm intelligence_worker stable in production; review false positive rate on extracted items
9. Enable proactive exam alerts: when intelligence_item with item_type='exam' is created for a course, push notification to students with that course in enrolled_courses
10. Monthly message signal refresh (message_signal_worker.py for new messages since last run)

**Multi-tenant foundation:**
11. Implement per-tenant daily AI cost ceiling (critical technical debt C1)
12. Enable Supabase RLS on all operational tables (medium debt M1)
13. Evaluate second university candidate — operational assessment only

**Search quality:**
14. Persist synthesis cache to Postgres (eliminate cold-start cost spike)
15. DOCX/PPTX extraction support (adds lecture slides)

## 6 Months

**Second university onboarding:**
16. Join second Saudi university (King Abdulaziz, KFUPM, or KSU most likely)
17. Onboard their knowledge repository (same folder contract as SEU)
18. Deploy tenant-routed bot (one bot or two — UX decision)

**Intelligence layer maturity:**
19. Weekly automated gap reports sent to ops channel
20. Confusion cluster notifications (when 5+ students ask about same topic with zero results, notify admin)
21. Exam date extraction from intelligence_items → personalized reminder system

**Concept layer:**
22. Run extract_concepts.py on corpus; populate concepts + chunk_concepts tables
23. Enable concept-aware retrieval (find chunks about "OSI Model" by concept, not just by similarity)

## 1 Year

**Platform layer:**
24. Admin dashboard (web): corpus coverage, query volume, gap analysis visualization
25. Student web interface (not just Telegram)
26. API for third-party integrations (LMS, university portal)

**Data quality:**
27. Human validation loop for machine-asserted attributions (promote most-queried chunks to confirmed)
28. Exam paper archive: systematic scraping of past SEU exams through official channels

**Multi-university:**
29. 5+ universities on platform
30. Shared institutional layer (common courses like English, Islamic Studies exist at multiple universities)

## 3 Years

**B2B intelligence layer:**
31. University admin product: "what are your students struggling with this semester?" dashboard
32. Predictive exam coverage gaps: before midterm season, alert on courses with thin coverage
33. Cross-university benchmarking: compare community knowledge depth across institutions

**Arabic language model fine-tuning:**
34. Collect enough human-validated Q&A pairs to fine-tune a small Arabic academic model
35. Reduce OpenAI API dependency for classification and attribution tasks

**Regional expansion:**
36. Gulf universities (UAE, Kuwait, Bahrain)
37. Partnership with official university bodies for direct content feeds (no Telegram scraping needed)

---

# 21. COMPLETE INVENTORY

## Repositories
| Repo | Purpose |
|------|---------|
| rumman-core (this repo) | Platform code, workers, API, bot |
| Knowledge repository (local only) | Official SEU documents, NOT in git |

## Railway Services (Processes)
| Process | File | Port | Session Var |
|---------|------|------|-------------|
| listener | app/rumman_engine.py | — | TELEGRAM_LISTENER_GHAYTH_SESSION |
| backfill | app/telegram_backfill_worker.py | — | TELEGRAM_BACKFILL_RAWI_SESSION |
| media | app/telegram_download_worker.py | — | TELEGRAM_MEDIA_IBRAHIM_SESSION |
| embed | app/embed_worker.py | — | — |
| search | app/search_api.py | $PORT | — |
| bot | app/telegram_bot.py | — | TELEGRAM_BOT_TOKEN |
| intelligence | app/intelligence_worker.py | — | — |
| attribution | app/attribution_worker.py | — | — |

## Database Tables (33 Migrations)
| Table | Migration | Purpose |
|-------|-----------|---------|
| messages | 001 | All Telegram messages |
| telegram_sync_state | 001 | Per-chat sync checkpoints |
| telegram_backfill_jobs | 001 | Backfill work queue |
| source_documents | 003 | Ingested file metadata |
| document_chunks | 003 | Vector-embedded text corpus |
| courses (legacy) | 003 | Replaced by inst_courses |
| processing_jobs | 004 | Media/embed work queue |
| query_logs | 006 | Legacy query log (superseded) |
| improvement_candidates | 006 | Normalization improvement pipeline |
| tenants | 007 | Multi-tenant anchor |
| rumman_users | 007 | Pseudonymous user identities |
| rumman_sessions | 007 | Session state |
| learning_events | 007 | All behavioral signals |
| concepts | 007 | Academic concept graph (empty) |
| chunk_concepts | 007 | Chunk↔concept links (empty) |
| concept_relationships | 007 | Concept graph edges (empty) |
| user_concept_history | 007 | Per-user concept encounters (empty) |
| inst_colleges | 008 (renamed 014) | College hierarchy |
| inst_specializations | 008 (renamed 014) | Specialization hierarchy |
| inst_courses | 008 (renamed 014) | Course master data |
| worker_cursors | 011 | Cursor state for stateful workers |
| intelligence_items | 011 | Extracted operational items |
| ai_runs | 015 | LLM call audit trail |
| academic_calendar | 016 | SEU academic dates |
| worker_heartbeats | 016 | Worker liveness monitoring |
| analysis_runs | 026 | Analyst worker audit log |
| gap_items | 026 | Knowledge gap records |
| message_signals | 032 | Community intelligence signals |
| course_intelligence_profiles | 031 | Per-course corpus summary |
| exam_intelligence | 031 | Top exam topics per course |
| student_context | 030 | Per-user persistent context |

## Views
| View | Purpose |
|------|---------|
| active_extracted_items | extracted_items filtered: not expired, not rejected, not superseded |
| active_document_chunks | document_chunks filtered: not superseded, not rejected |
| course_intelligence | courses enriched with prerequisites and downstream count |
| course_content_coverage | chunk count per course (legacy, use course_intelligence_profiles) |

## Postgres RPC Functions
| Function | Purpose |
|----------|---------|
| match_documents | Main semantic search (all document_chunks) |
| match_course_chunks | Course-scoped semantic search (legacy) |
| match_chunks_general | Source-type-scoped semantic search |

## Application Files
| File | Lines | Purpose |
|------|-------|---------|
| app/rumman_engine.py | 541 | Live Telegram listener |
| app/telegram_backfill_worker.py | 854 | Historical backfill |
| app/telegram_download_worker.py | 534 | Media download + processing |
| app/embed_worker.py | 388 | Text chunking + embedding |
| app/search_api.py | 1639 | Search API + synthesis |
| app/telegram_bot.py | 1131 | Student-facing Telegram bot |
| app/intelligence_worker.py | 297 | Operational item extraction |
| app/attribution_worker.py | 360 | Course code attribution |
| app/heartbeat.py | ~80 | Worker liveness utility |
| app/query_handler.py | 379 | CLI synthesis tool |
| app/daily_brief.py | 467 | Daily intelligence brief |
| app/pdf_worker.py | 308 | PDF extraction worker |
| app/qa_mining_worker.py | 579 | Q&A pair extraction |
| app/query_understanding.py | 400 | Query normalization + intent |

## Scripts
| Script | Purpose |
|--------|---------|
| scripts/ingest_document.py | Single document ingestion |
| scripts/batch_ingest_seu.py | Bulk SEU repository ingestion |
| scripts/seed_courses.py | Course master data seeding |
| scripts/refresh_course_profiles.py | Update course intelligence profiles |
| scripts/extract_exam_signals.py | Extract top exam topics |
| scripts/backfill_course_codes.py | Manual attribution (regex+LLM) |
| scripts/qa_mining_worker.py | Q&A pair mining |
| scripts/message_signal_worker.py | Signal extraction |
| scripts/gap_analyst.py | Knowledge gap analysis |
| scripts/weekly_report.py | Weekly ops health report |
| scripts/eval_bot_quality.py | Bot quality evaluation |
| scripts/create_backfill_jobs.py | Create backfill jobs manually |
| scripts/reset_media_jobs.py | Reset stuck media jobs |
| scripts/generate_seed_lexicon.py | Generate normalization candidates |
| scripts/review_candidates.py | Review normalization candidates |
| scripts/extract_concepts.py | Extract academic concepts |
| scripts/export_group_links.py | Export Telegram group invite links |

## Data Files
| File | Purpose |
|------|---------|
| data/normalization_dict.json | Arabic dialect normalization (98 phrases, 153 words) |
| data/intent_hints.json | Static keyword→intent mappings (7 patterns) |
| scripts/data/seu_courses.json | Structured course data for seed_courses.py |

## Environment Variables
```
# Telegram user clients
TELEGRAM_API_ID
TELEGRAM_API_HASH
TELEGRAM_LISTENER_GHAYTH_SESSION    # غيث account
TELEGRAM_BACKFILL_RAWI_SESSION      # راوي account
TELEGRAM_MEDIA_IBRAHIM_SESSION      # إبراهيم account
# Legacy fallbacks (listener only):
TELEGRAM_GHAYTH_SESSION
TELEGRAM_SESSION_STRING

# Supabase
SUPABASE_URL
SUPABASE_KEY                        # service_role key

# OpenAI
OPENAI_API_KEY

# Telegram bot
TELEGRAM_BOT_TOKEN
SEARCH_API_URL                      # Railway internal URL
RUMMAN_USER_SALT                    # Secret for user hash derivation

# Worker tuning (optional, have defaults)
WORKER_ID
BACKFILL_SLEEP_SECONDS              # default 30
BACKFILL_LEASE_MINUTES              # default 10
ATTRIBUTION_WORKER_ENABLED          # true/false
ATTRIBUTION_BATCH_SIZE              # default 20
ATTRIBUTION_SLEEP_SECONDS           # default 120
ATTRIBUTION_MAX_DAILY_CALLS         # default 3000
ATTRIBUTION_MAX_TOKENS_PER_RUN      # default 500,000
INTELLIGENCE_WORKER_ENABLED         # true/false
INTELLIGENCE_BATCH_SIZE             # default 50
INTELLIGENCE_SLEEP_SECONDS          # default 60
INTELLIGENCE_MAX_TOKENS_PER_RUN     # default 200,000
INTELLIGENCE_CONCURRENCY            # default 15
SYNTHESIS_CACHE_TTL                 # default 7200 (2h)
SYNTHESIS_CACHE_MAX                 # default 1000

# Reporting
RUMMAN_OPS_CHAT_ID                  # Telegram ops channel chat_id
```

---

# 22. REBUILD GUIDE

*Assume the entire project is lost. This is the rebuild sequence.*

## Prerequisites
- Python 3.11+
- Supabase project with pgvector extension enabled
- Railway account
- Three Telegram user accounts (any three phone numbers)
- One Telegram bot (from @BotFather)
- OpenAI API account

## Step 1: Infrastructure Setup (Day 1)

```bash
# 1. Create Supabase project
# Enable pgvector: Dashboard → Database → Extensions → vector → Enable

# 2. Run migrations in order
# In Supabase SQL editor, run each migration file:
supabase/migrations/001_daily_brief_tables.sql
supabase/migrations/003_knowledge_layer.sql
supabase/migrations/004_media_lifecycle.sql
supabase/migrations/005_match_documents_rpc.sql
supabase/migrations/006_query_intelligence.sql
supabase/migrations/007_platform_foundations.sql
supabase/migrations/008_curriculum_foundations.sql
# ... continue through 033

# 3. Generate Telegram session strings (local only)
# Run auth_session.py for each of the three accounts
# Store resulting strings in Railway env vars
```

## Step 2: Core Ingestion (Days 2-3)

```bash
# Deploy listener + backfill + media + embed workers to Railway
# Set environment variables in Railway

# Seed SEU institutional data
python3 scripts/seed_courses.py

# Ingest official SEU documents
python3 scripts/batch_ingest_seu.py

# Create backfill jobs for all known SEU groups
python3 scripts/create_backfill_jobs.py --chats [chat_id_list]
```

**Critical dependency:** The listener must be running and joined to groups BEFORE starting the search layer. The corpus must have some content for search to be meaningful.

## Step 3: Search Layer (Day 4)

```bash
# Deploy search API and bot to Railway
# Verify: GET https://{search-service-url}/health returns 200
# Test: POST /synthesize with a simple query
```

## Step 4: Intelligence Layer (Week 2)

```bash
# Enable after corpus has reasonable coverage
# Set ATTRIBUTION_WORKER_ENABLED=true in Railway
# Set INTELLIGENCE_WORKER_ENABLED=true in Railway

# Run initial signal extraction
python3 scripts/message_signal_worker.py

# Run course profile refresh
python3 scripts/refresh_course_profiles.py

# Run exam signal extraction
python3 scripts/extract_exam_signals.py
```

## Critical Rebuild Mistakes to Avoid

1. **Don't enable intelligence_worker before corpus exists.** It reads messages and tries to extract items from empty context. Waste of API budget.

2. **Don't run two processes on the same StringSession.** Always verify each process has its own unique session string. Test by running one at a time first.

3. **Don't skip the Arabic regex for course codes.** Law school uses Arabic-script codes (قنن427). The regex must handle both Latin and Arabic patterns.

4. **Don't set MIN_SIMILARITY too high.** Arabic embeddings are lower similarity than English. Start with 0.40 broad / 0.25 course-filtered. Adjust after seeing actual query results.

5. **Don't use the Supabase client library.** Use direct httpx against PostgREST. You need control over `Prefer` headers, conditional PATCHes, and 409 handling.

6. **Don't process media before the StringSession is confirmed.** The media worker needs the Ibrahim account session. Test the connection before queuing 15K jobs.

7. **Don't remove the synthesis cache TTL check.** It's easy to accidentally cache indefinitely, serving stale answers even after new content is ingested.

8. **Don't skip normalization testing.** The normalization_dict.json is essential for Arabic dialect handling. Test that Gulf Arabic phrases normalize correctly before deploying.

---

# 23. BLIND SPOTS & UNKNOWNS

## Verified Facts (High Confidence)
- Current corpus: ~120K document_chunks, ~72K messages, 153 official documents
- 338 course intelligence profiles computed
- 263 exam intelligence records
- attribution_worker running at 3K calls/day
- Phase 2 complete as of 2026-06-01

## Likely True (Medium Confidence)
- Arabic embedding quality is adequate for course-specific queries but degrades for general cultural/contextual queries
- The 0.25/0.40 similarity thresholds are appropriate for current query volume; may need tuning as corpus grows
- gpt-4o-mini attribution at ≥0.85 confidence has low false positive rate (not empirically verified)
- Cache hit rate during exam season is high enough to justify current implementation (not measured)

## Unverified Assumptions (Require Investigation)
- **Attribution quality:** The 0.85 threshold for machine-asserted attribution has not been empirically validated on real data. Assumption: it produces < 5% false positives. Needs a sample audit.
- **Intelligence item quality:** intelligence_worker's 0.65 confidence threshold has not been spot-checked against real Telegram messages. May produce significant false positives or miss important items.
- **SEU student query volume:** The cost model assumes ~100 queries/day. Actual volume once the bot is promoted to more groups may be 10-50× higher. Cost projections would change significantly.
- **Backfill completeness:** Not all SEU Telegram groups are known. There may be 50-100 additional groups not yet joined.
- **FIN416 gap severity:** The gap in Islamic Finance content is documented but its impact on student experience hasn't been measured (how often do students ask about FIN416?).

## Missing Information (Unknown)
- Complete list of all active SEU Telegram groups (only 79 are known)
- Actual engagement metrics for the bot (how many students use it? how often?)
- Qualitative feedback from students (is the answer quality satisfactory?)
- Actual false positive rate of intelligence_worker extracted items
- Whether the Law school (Arabic-script course codes) has active Telegram groups that are currently unmonitored

## Areas Requiring Human Decision
- Whether to join non-SEU groups that discuss SEU courses (e.g., general Saudi student groups)
- Whether to make the bot public or keep it invite-only
- Retention policy for message data (GDPR / Saudi data protection compliance)
- Whether to store audio transcripts of student voice notes (privacy consideration)

## Future Investigation Needed
- Can pgvector HNSW performance sustain 1M+ chunks without infrastructure upgrade?
- What is the actual embedding quality gap between text-embedding-3-large vs. a fine-tuned Arabic model?
- Is the current synthesis quality sufficient for exam preparation, or are students still verifying answers elsewhere?

---

*End of RUMMAN_MASTER_DOSSIER*  
*Generated: 2026-06-01 | Repository: rumman-core @ 5e33466*  
*This document should be reviewed and updated quarterly, or whenever a major architectural decision is made.*
