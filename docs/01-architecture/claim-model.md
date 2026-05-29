# The Claim Model

<!-- governance: maintained -->

The claim is the core primitive of RUMMAN. Understanding the claim model is required before working on the extraction pipeline, the intelligence layer, or any schema that stores derived knowledge.

See `docs/philosophy/vocabulary.md` for precise definitions of: claim, evidence, machine-asserted, human-confirmed, provenance.

---

## What a Claim Is

A claim is a bounded, sourced assertion about organizational state. It has:

- **Content** — what is being asserted ("Assignment 3 is due Thursday")
- **Type** — the category of assertion (task / deadline / decision / fact / risk / commitment)
- **Confidence** — how certain the system is (0.0–1.0)
- **Temporal scope** — when the claim is valid (valid_from, valid_until)
- **Validity state** — its epistemic status in the system
- **Provenance** — what evidence supports it, what AI run produced it

A message is not a claim. A message is evidence from which claims may be extracted. The claim is the unit of organizational memory; the message is the unit of organizational communication.

---

## The Validity State Machine

Every claim moves through a defined set of states. State transitions are explicit — no implicit promotion.

```
                    ┌─────────────────┐
                    │ machine_asserted │  ← Created by extraction worker
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
     ┌─────────────┐  ┌──────────┐  ┌──────────────────┐
     │   confirmed  │  │ rejected │  │ machine_rejected  │
     │  (by human) │  │(by human)│  │  (by system)      │
     └──────┬──────┘  └──────────┘  └──────────────────┘
            │
     ┌──────┴──────┐
     │             │
     ▼             ▼
┌─────────┐  ┌────────────┐
│ expired │  │ superseded │
└─────────┘  └────────────┘
```

**machine_asserted:** The claim was produced by an extraction worker. It is a hypothesis. It is not part of operational reality.

**confirmed:** A human reviewed the claim and confirmed it is accurate. It becomes part of operational reality.

**rejected (human):** A human reviewed the claim and marked it as incorrect. Preserved for audit; not surfaced to users.

**machine_rejected:** The system determined the claim is likely wrong — confidence below threshold, or contradicted by a higher-confidence claim about the same subject. Not surfaced to users; retained for analysis.

**expired:** The claim's temporal scope has passed. A deadline that has passed is expired. An expired claim is preserved for historical context but is no longer part of current operational reality.

**superseded:** A newer, higher-confidence claim about the same subject has replaced this one. Common when a deadline is updated in a later message.

---

## The Provenance Requirement

Every claim row must carry:

```
source_ingestion_event_id    — the message or event the claim was extracted from
source_ai_run_id             — the AI operation that produced the claim
```

These are NOT NULL. A claim without provenance is architecturally invalid.

The provenance chain is what makes the system auditable: "RUMMAN told me the deadline was Thursday — show me why it believes that" should produce: the specific message where the deadline was mentioned, the AI run that extracted it, the confidence score, and the extraction model used.

---

## The Initial Schema: extracted_items

The first implementation of the claim model. Intentionally minimal for the Daily Brief validation phase.

```sql
extracted_items (
  id                        uuid primary key default gen_random_uuid(),
  tenant_id                 text not null default 'default',

  -- what the claim is
  item_type                 text not null,   -- task | deadline | decision
  content                   text not null,   -- the claim in natural language
  structured                jsonb,           -- optional structured form

  -- temporal scope
  valid_from                timestamptz default now(),
  valid_until               timestamptz,     -- null = no known expiry

  -- epistemic state
  validity_status           text not null default 'machine_asserted',
  confidence                float not null,

  -- provenance (both required)
  source_chat_name          text not null,
  source_message_ids        text[] not null, -- messages that support this claim
  source_ai_run_id          uuid not null references ai_runs(id),

  -- lifecycle
  created_at                timestamptz default now(),
  updated_at                timestamptz default now()
)
```

The `validity_status` column has a CHECK constraint: `validity_status IN ('machine_asserted', 'confirmed', 'rejected', 'machine_rejected', 'expired', 'superseded')`.

`confidence` has a CHECK constraint: `confidence >= 0.0 AND confidence <= 1.0`.

---

## The ai_runs Table (Required Before extracted_items)

`extracted_items.source_ai_run_id` is a foreign key. The `ai_runs` table must exist before the first extraction runs.

```sql
ai_runs (
  id                uuid primary key default gen_random_uuid(),
  tenant_id         text not null default 'default',

  -- what ran
  worker            text not null,          -- e.g., 'daily_brief_extractor'
  prompt_name       text not null,
  prompt_version    text not null,

  -- what model
  model             text not null,
  input_tokens      int,
  output_tokens     int,
  cost_usd          float,

  -- what was processed
  source_table      text not null,          -- e.g., 'messages'
  source_ids        text[] not null,        -- the input rows

  -- input fingerprint (for dedup detection)
  input_hash        text,

  -- output
  raw_output        jsonb,

  -- status
  status            text not null,          -- completed | failed
  error             text,

  started_at        timestamptz default now(),
  completed_at      timestamptz
)
```

**Rule:** Write the `ai_runs` row first (with `status = 'running'`), then write the extracted claims with the `ai_run_id` as foreign key, then update `ai_runs.status = 'completed'`. If the extraction fails, update `status = 'failed'` and write no claims. This ensures claims always have traceable parentage.

---

## How the Daily Brief Produces Claims

The Daily Brief extraction loop:

```
1. Query recent messages from a chat (last 24h)
2. Write an ai_runs row (status = running)
3. Call LLM with messages + extraction prompt
4. Parse JSON output into individual claims
5. For each claim:
   - Set validity_status = 'machine_asserted'
   - Set source_ai_run_id = the ai_runs row
   - Set source_message_ids = the IDs of messages that mentioned it
   - Set confidence from the model's output
6. Insert all claims into extracted_items
7. Update ai_runs.status = 'completed'
8. If any step fails: update ai_runs.status = 'failed', write no claims
```

Step 8 is critical. A partial write that produces claims without a completed ai_runs parent violates the provenance requirement.

---

## How Claims Become Operational Reality

In the Daily Brief v1, claims become operational reality through a simple human feedback loop:

- User receives the brief (list of machine_asserted claims)
- User replies `/done [id]` → claim status set to `confirmed`, then `expired`
- User replies `/wrong [id]` → claim status set to `human_rejected`
- No reply → claim remains `machine_asserted`, re-surfaces tomorrow if still relevant

The Daily Brief surfaces only `machine_asserted` and `confirmed` claims with `valid_until > now()` (or `valid_until IS NULL`).

---

## Evolution of the Claim Model

This initial schema is intentionally minimal. It captures the core validity/provenance structure without the full ontology from ADR-0006.

As the system matures, `extracted_items` will be refactored into specialized tables (tasks, deadlines, decisions) with this model as the foundation. The migration will be mechanical because the claim structure is consistent — `validity_status`, `confidence`, `source_ai_run_id` will carry forward unchanged.

What must not change between v1 and future versions: the provenance requirement, the validity state machine, and the constraint that AI outputs are machine-asserted (never directly confirmed) until human validation.
