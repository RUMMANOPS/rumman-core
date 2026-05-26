# RUMMAN Roadmap

## Phase 0: Foundation

Status: In Progress

### Goals

- Establish stable Telegram live ingestion.
- Store messages in Supabase.
- Separate live ingestion from historical backfill.
- Create project documentation as source of truth.

### Completed

- Railway deployment
- Telegram user client session
- Supabase message storage
- Live listener
- telegram_sync_state checkpoints
- telegram_backfill_jobs queue
- Backfill worker process
- GitHub docs structure

---

## Phase 1: Memory/Data Spine

Status: Current Phase

### Goals

- Stabilize ingestion.
- Build resumable backfill.
- Improve observability.
- Validate message deduplication.
- Document database schema.
- Keep intelligence disabled until data spine is stable.

### Deliverables

- Controlled backfill worker
- Backfill job lifecycle
- Sync state accuracy
- Operational metrics definitions
- Database documentation

---

## Phase 2: Intelligence Pipeline v1

Status: Planned

### Goals

- Extract tasks, decisions, deadlines, entities, and insights from messages.
- Use structured outputs.
- Log AI runs.
- Keep every extracted object traceable to source messages.

### Deliverables

- Extraction schema
- AI runs table
- Message classification jobs
- Memory writer
- Task writer
- Decision writer
- Deadline writer

---

## Phase 3: Orchestration Layer

Status: Planned

### Goals

- Use n8n as an orchestration layer.
- Trigger workflows from jobs and events.
- Send notifications and summaries.
- Integrate external tools.

### Deliverables

- n8n workflow registry
- Notification flows
- Summary flows
- Manual trigger flows
- Error escalation flows

---

## Phase 4: Product Interface

Status: Future

### Goals

- Build user-facing dashboard.
- Expose operational memory.
- Search messages, decisions, tasks, and insights.
- Provide system health visibility.

### Deliverables

- Dashboard
- Search UI
- Task/decision views
- Operational health view

---

## Phase 5: Multi-Channel Expansion

Status: Future

### Goals

- Add WhatsApp, email, files, voice, and university sources.
- Normalize all channels into the same data spine.

### Deliverables

- WhatsApp ingestion
- Email ingestion
- File ingestion
- Voice pipeline
- Unified source references
