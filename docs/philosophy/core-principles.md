# RUMMAN Core Principles

<!-- governance: invariant -->

These are the load-bearing beliefs that shape architectural decisions in RUMMAN. They are not rules or requirements — they are convictions that have been reasoned through carefully and should be challenged only with equivalent care.

When a design decision conflicts with one of these principles, that conflict should be explicit and documented — not resolved by quietly ignoring the principle.

---

## 1. Claims over Messages

The core primitive of the system is the claim — a grounded, temporal, sourced assertion about organizational state. Messages are evidence for claims. The system's job is not to store messages; it is to build and maintain a model of organizational reality from the claims those messages support.

**What this means in practice:** A system optimized around message storage is a log. A system optimized around claim management is a memory. Design tables, queries, and interfaces around claims, not messages.

---

## 2. Evidence Stays Raw

Raw artifacts — messages, audio, images, documents — are immutable once ingested. The extraction pipeline derives knowledge from them; it does not transform or replace them. If extraction is wrong, the original evidence still exists and can be re-processed.

**What this means in practice:** Never delete raw artifacts through automated processes. Never overwrite the `raw_json` of an ingested message. Extraction creates new rows; it does not update source rows.

---

## 3. AI Outputs Are Hypotheses Until Confirmed

Everything produced by an AI extraction process is a machine-asserted claim with a confidence score. It is not a fact until a human confirms it or it meets a threshold sufficient for operational trust. The schema must enforce this — not application code, not documentation.

**What this means in practice:** Every derived table needs `validity_status` and `confidence` as NOT NULL columns. A machine-asserted claim sitting in the database is a hypothesis, not a fact. Systems that read from claim tables must declare what validity threshold they require.

---

## 4. Provenance Is Not Optional

Every claim must trace back to its source evidence and the AI run that produced it. Provenance cannot be added retroactively to millions of rows at acceptable cost. It must be in the schema from the moment the first claim table is created.

**What this means in practice:** `source_ingestion_event_id` and `source_ai_run_id` are NOT NULL on every claim row. If you cannot populate them, the row should not be written.

---

## 5. State Lives in Postgres, Compute Is Stateless

Workers — extraction, transcription, embedding, intelligence — hold no state between invocations. All state that needs to survive a restart lives in Postgres before the worker that created it exits. This makes every worker replaceable, re-runnable, and debuggable.

**What this means in practice:** A worker that needs to "remember" something between runs stores that thing in a Postgres row. Worker memory is not architecture.

---

## 6. Output Gates Input

The correct sequence is: validate that the output is useful before expanding the input. Add audio processing only after text extraction is proven valuable. Add document ingestion only after audio is proven valuable. More sources before validated output is noise accumulation, not intelligence building.

**What this means in practice:** The Daily Brief operational loop must prove real value before the knowledge layer is built. The knowledge layer must prove real value before the intelligence layer is enabled.

---

## 7. Extraction Quality Is Empirical, Not Assumed

The accuracy of AI extraction on real organizational data — especially Arabic colloquial chat — must be measured on actual data, not inferred from benchmark performance. False positive and false negative rates must be known before infrastructure is built around the extraction output.

**What this means in practice:** Run extraction on real historical data. Count what's right and what's wrong. Document the baseline accuracy. This happens before scaling the extraction pipeline.

---

## 8. Tenant Isolation Is Foundational, Not Incremental

Tenant isolation — ensuring that one tenant's data is never accessible to another — cannot be retrofitted cleanly into a system that was built without it. The cost of adding it after the fact grows with every table, query, and feature built without it.

**What this means in practice:** Every operational table has `tenant_id`. Every query that returns tenant-owned data filters by `tenant_id`. RLS policies enforce this at the database layer when the product becomes multi-tenant. These constraints are designed in from the start, even when there is only one tenant.

---

## 9. Operational Clarity Over Architectural Elegance

A system that is operationally clear — easy to run, observe, debug, and reason about — is more valuable than one that is architecturally elegant. Job queues over event buses. SQL over message brokers. One managed service over five specialized ones. Simple worker loops over orchestration frameworks.

**What this means in practice:** When two approaches solve the same problem, prefer the one that produces a simpler operational state. The engineer woken up at 2am to fix a production issue should be able to understand the system from its Postgres tables alone.

---

## 10. The Platform Is Only As Valuable As Its Outputs

Infrastructure without useful output is not a platform. It is a data warehouse. Before expanding ingestion, extraction, or intelligence capabilities, the question to answer is: *Is what we have now making someone meaningfully more effective?* If the answer is not "yes" or "not yet, but here's the specific thing that's missing," build toward that thing first.

**What this means in practice:** The Daily Brief loop is the current minimum test of platform value. If it doesn't produce genuine value, nothing built on top of it will fix that.
