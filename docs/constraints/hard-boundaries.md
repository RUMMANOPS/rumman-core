# Hard Boundaries

<!-- governance: invariant -->

These are rules that must not be broken. They are not preferences or conventions — they are constraints that exist because the cost of violating them is high and often irreversible.

Each boundary includes the reason it exists. Knowing the reason helps judge edge cases; rules without reasons get silently ignored when they're inconvenient.

---

## Ingestion Boundaries

### Never run uncontrolled Telegram crawling inside the live listener

The listener handles new messages only. It never calls `iter_messages`, never crawls history, and never performs backfill operations inline.

**Why:** Early versions merged live ingestion with historical crawling. The result was startup delays, unstable pipelines, Telegram rate-limit cascades, and inability to restart cleanly. Historical ingestion now lives exclusively in the backfill worker with controlled batches, leases, and rate limiting. Mixing them again would recreate those failure modes. (ADR-0002)

**The rule in code:** `ENABLE_BACKFILL = False` in `rumman_engine.py`. This is a guard, not a feature flag — it should never be set to True.

---

### Never delete raw artifacts through automated processes

Raw artifacts (messages, audio, images, documents) are the permanent evidence record. Once ingested, they are not deleted by any worker, scheduled job, or automated process.

**Why:** Extraction quality improves over time. If you delete the original artifact after extracting from it, you lose the ability to re-process with a better model. The raw artifact is the audit record that makes every derived claim verifiable. Deletion destroys that.

**The exception:** Explicit human-initiated deletion for legal/privacy reasons (right to erasure requests) is permitted and must be documented.

---

### Never share or cross-tenant-access data

A tenant must never receive data — messages, claims, artifacts, embeddings, AI outputs — that belongs to another tenant.

**Why:** This is a legal and trust boundary, not just a product feature. Cross-tenant data leakage is a privacy violation regardless of intent. The schema enforces `tenant_id` on all operational tables; queries must always filter by `tenant_id`. pgvector similarity searches must always include `WHERE tenant_id = ?`.

---

## AI and Extraction Boundaries

### Never write AI outputs without provenance

Every row in `extracted_items`, `tasks`, `decisions`, `deadlines`, `memories`, `insights`, or any other claim table must have `source_ai_run_id` and `source_ingestion_event_id` populated. These columns are NOT NULL in the schema.

**Why:** Without provenance, claims cannot be audited, wrong extractions cannot be identified and corrected, and re-extraction cannot be targeted. Provenance cannot be reconstructed retroactively across millions of rows. The schema enforces this.

---

### Never treat machine-asserted claims as confirmed facts

Claims produced by the extraction pipeline have `validity_status = 'machine_asserted'`. They are hypotheses. Systems that query claim tables to produce user-facing outputs must declare the validity threshold they require and filter accordingly.

**Why:** An extraction pipeline that is 80% accurate produces outputs that are 20% wrong. If machine-asserted claims are treated as facts, 20% of what the system presents to users is incorrect. Users will notice, stop trusting the system, and stop using it. The validity state machine exists to let the system be honest about what it knows vs. what it believes.

---

### Never enable the intelligence layer before stable extraction is validated

The intelligence layer (Layer 3) must not run in production until: the extraction pipeline (Layer 2) is operational, `ai_runs` logging is in place, per-tenant cost controls are configured, and extraction quality has been measured on real data.

**Why:** Intelligence built on poor extraction amplifies errors. Enabling it prematurely produces confident-sounding wrong answers, which is worse than no answers. The `intelligence_worker.py` file exists as a sketch but is intentionally excluded from the Procfile.

---

### Never run AI extraction without a cost ceiling per tenant

Each tenant must have a configurable daily AI cost limit. Extraction workers must check this limit before making API calls.

**Why:** A loop bug or malicious input can produce unbounded API calls. At $0.002 per message processed, 50,000 messages/day = $100/day per tenant in extraction costs alone. Without a ceiling, a single bug becomes a significant billing incident.

**Current status:** Not yet implemented. Must be implemented before the intelligence layer is enabled for any tenant.

---

## Storage Boundaries

### Never store binary files in Postgres

Audio files, images, PDFs, videos, documents — none of these belong in Postgres columns (as bytea, text, or base64).

**Why:** Binary blobs in Postgres degrade table compaction, increase backup size, slow down queries on unrelated columns, and don't benefit from Postgres's relational capabilities. Supabase Storage exists for this purpose. (ADR-0007)

---

### Never generate cross-tenant presigned URLs

Presigned URLs for Supabase Storage artifacts must be generated with the artifact's `tenant_id` in the path. A presigned URL must only be issued to the tenant who owns the artifact.

**Why:** Presigned URLs bypass RLS. A URL pointing to another tenant's artifact path is a data breach, regardless of whether the URL expires.

---

## Operational Boundaries

### Never skip the lease protocol in job workers

Workers that process jobs from any queue (`telegram_backfill_jobs`, `processing_jobs`, future `extraction_jobs`) must: acquire a lease before starting work, renew the lease via heartbeat during long operations, and release the lease cleanly on completion or failure. They must never assume they hold a lease without verifying it.

**Why:** Without leases, two workers can process the same job simultaneously, producing duplicate outputs, double API costs, and corrupted state. The lease protocol is what makes the queue safe for concurrent workers.

---

### Never modify invariant documentation autonomously

Documents in `docs/philosophy/` and `docs/constraints/` are invariant. AI systems may read them freely but must not write to them autonomously.

**Why:** These documents encode the foundational beliefs and constraints that the architecture depends on. A subtly wrong invariant document — one that looks correct but has been silently modified — can poison reasoning across the entire project for months before anyone notices. Human authorship of these documents is a safety mechanism, not a bureaucratic rule.

---

### Never commit secrets or session strings

`.env`, `*.session`, and files containing API keys, database credentials, or Telegram session strings must never be committed to the repository.

The `.gitignore` enforces this for known file types, but new credentials paths (e.g., a new service's config file) must be added to `.gitignore` before they are created.

**Why:** Committed secrets are permanently in git history. Rotating them requires both the credential rotation and a git history rewrite. The `auth_session.py` file is `.gitignore`'d and must stay that way.
