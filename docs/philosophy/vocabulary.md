# RUMMAN Vocabulary

<!-- governance: invariant -->

This document defines the precise meaning of terms used throughout the RUMMAN codebase, documentation, and architectural reasoning. These definitions are load-bearing — using them loosely produces architectural confusion.

When a new term is adopted, add it here before using it in ADRs or architecture documents.

---

## The Core Triad

These three terms describe the fundamental epistemic layers of the system.

### Evidence

Raw, unprocessed source material. Messages, audio files, images, PDFs, documents. Evidence is immutable once ingested — it records what happened, not what it means.

Evidence is never the system of record. It is the foundation that claims rest on.

**Examples:** A Telegram message. An audio voice note. A scanned lecture slide. A forwarded PDF.

**In the schema:** `messages`, `raw_artifacts` (once implemented), the `raw_json` column.

---

### Claim

A bounded, sourced assertion about organizational state. Claims are extracted from evidence by the system (or asserted by humans). Every claim has:
- A **truth value** (believed true / believed false / unknown)
- A **temporal scope** (when it became true, when it stops being true)
- A **source** (what evidence supports it)
- A **confidence** (how certain the assertion is)
- A **validity state** (see below)

Claims are the core primitive of the system. Everything the system reasons about is a claim.

**Examples:** "Assignment 3 is due Thursday." "The team decided to delay the launch." "Person A committed to delivering B." "Quiz 4 is Sunday at 9pm."

**In the schema:** `extracted_items` (initial implementation), future: `tasks`, `deadlines`, `decisions`, `memories`.

---

### Operational Reality

The current validated claim state of an organization — what is actually true, right now, as far as the system can determine. Operational reality is not a table; it's the answer to the query: *what are the confirmed, non-expired claims about this organization's working world?*

A system that knows operational reality can answer questions like "what commitments were made last week that aren't resolved?" without reading through raw message history.

**In the schema:** The intersection of all claims with `validity_status = 'confirmed'` and `valid_until > now()`.

---

## Claim Vocabulary

### Machine-Asserted Claim

A claim produced by an AI extraction process. Not yet validated. It's a hypothesis — the system believes it is true based on evidence, but no human has confirmed it.

Machine-asserted claims are **not** part of operational reality. They are candidates.

---

### Human-Confirmed Claim

A machine-asserted claim that a human has reviewed and confirmed as accurate. Graduates to operational reality.

---

### Validity State

Every claim has an explicit validity state. The valid states are:

| State | Meaning |
|---|---|
| `machine_asserted` | Extracted by AI; not yet validated |
| `human_confirmed` | Reviewed and confirmed by a human |
| `machine_rejected` | System determined the claim is likely wrong (low confidence, contradicted) |
| `human_rejected` | Human marked the claim as incorrect |
| `expired` | The claim's temporal scope has passed (deadline passed, decision superseded) |
| `superseded` | A newer claim replaces this one |

---

### Provenance

The chain from a claim back to the evidence that supports it. A claim without provenance cannot be trusted, verified, or re-evaluated.

Minimum provenance for any claim: `source_ingestion_event_id` (what message or event triggered it) and `source_ai_run_id` (what AI operation produced it).

**Rule:** A claim without provenance is architecturally invalid. The schema must enforce this with NOT NULL constraints, not application-level conventions.

---

### Confidence

A value between 0 and 1 representing how certain the system is about a claim. This is set by the extraction process and should reflect genuine uncertainty — not defaulted to 1.0.

Claims with confidence below a threshold (typically 0.6) should not be surfaced to users without explicit uncertainty marking.

---

## Pipeline Vocabulary

### Raw Artifact

An immutable binary file ingested from a source platform (audio, image, PDF, video, document). Stored once in Supabase Storage and never modified. The permanent evidence record.

Distinct from a *message* (which is text-based) and from a *knowledge artifact* (which is extracted/processed).

---

### Knowledge Artifact

The processed representation of a raw artifact or message. What the system knows about the content after extraction: a transcript, OCR text, parsed document structure. Knowledge artifacts are versioned — reprocessing creates a new version, not an overwrite.

**Note:** The knowledge artifact concept belongs to Layer 2, which does not yet exist in code.

---

### Extraction

The process of producing claims from evidence. Extraction is probabilistic — it produces machine-asserted claims with confidence scores, not facts. Extraction quality must be measured empirically on real data before extraction infrastructure is scaled.

---

### Ingestion

The process of receiving source platform signals and storing them as evidence (messages, raw artifacts). Ingestion is deterministic — it stores what happened, not what it means. Ingestion does not perform extraction.

---

## Architectural Layer Terms

### Data Spine (Layer 1)

The ingestion, storage, synchronization, and coordination infrastructure. Everything that gets data in and keeps it safe. Currently implemented.

### Knowledge Layer (Layer 2)

The extraction pipeline. Transforms evidence (raw artifacts, messages) into structured knowledge (knowledge artifacts, entities). Not yet implemented.

### Intelligence Layer (Layer 3)

The reasoning layer. Produces validated claims and operational insights from structured knowledge. Gated on Layer 2 being stable. Currently disabled.

---

## System Philosophy Terms

### Operational Intelligence

Structured, actionable knowledge derived from an organization's operational communications and documents. Distinguished from conversational AI (reactive, query-response) and automation (rule-based, trigger-action) by its ability to reason over a model of organizational state.

### Organizational Memory

The accumulated, validated claim state of an organization over time. Not a list of messages. Not a search index. A queryable model of what has been true, what is true now, and what the organization has committed to.

### Provenance Chain

The complete lineage from a claim back through the AI run that produced it, through the knowledge artifact it was derived from, through the raw artifact or message that was the original evidence. The provenance chain makes every system output auditable and replayable.

---

## Terms Deliberately Avoided

**"Memory" (as a technical feature):** Too vague. Use "operational memory" (the organizational concept) or "knowledge artifact" / "validated claim" (the technical concept) instead.

**"AI Output"** (as a noun for stored data): Use "machine-asserted claim" instead. "AI output" suggests finality; "machine-asserted claim" communicates its epistemic status.

**"Intelligence" (as a description of the extraction pipeline):** The extraction pipeline produces claims. Intelligence is what reasons over them. Use "extraction" for the pipeline and "intelligence" only for Layer 3 reasoning.
