# RUMMAN Engineering Documentation

This directory is the project's engineering memory. It records not only how the system works but why it evolved this way.

## How to Use This Documentation

Read `00-project-brain.md` first. It is the compass.

Read `philosophy/vocabulary.md` before reading architecture documents. The terminology here is precise and load-bearing — terms like "claim," "evidence," and "machine-asserted" have specific meanings that differ from casual usage.

Read the relevant ADR before making a significant architecture change. ADRs record not just decisions but the constraints and tradeoffs that shaped them.

---

## Governance Tiers

Documents in this repository are organized by how they change and who owns them.

### Tier 1 — Invariant

Location: `philosophy/`, `constraints/`, and accepted ADRs in `decisions/` (now `02-adrs/`).

These documents hold the load-bearing beliefs, constraints, and decisions that the architecture depends on. They are written by humans and never autonomously modified by AI systems. Changing them requires deliberate human decision — either a new ADR that supersedes an old one, or an explicit architectural review.

If an AI system autonomously edits an invariant document, treat that as a governance failure and revert.

### Tier 2 — Maintained

Location: `01-architecture/`, `04-database/`, `03-workflows/`, `06-roadmap/`, `07-knowledge-layer/`.

These documents describe the current state of the system. They are kept current as the system evolves. AI systems may draft updates and propose changes via PR; human review before merge.

A maintained document that has not been updated for 90 days after a significant system change is considered stale.

### Tier 3 — Generated

Location: `operational/` (when created), schema snapshots, process status.

These documents are derived from the running system. AI systems may update them freely. They are authoritative for the moment they were written; treat them as snapshots, not truth.

---

## Document Map

| Document | Tier | Purpose |
|---|---|---|
| `00-project-brain.md` | Maintained | Project compass — what RUMMAN is, current phase, core principle |
| `philosophy/vocabulary.md` | Invariant | Shared terminology — precise definitions for all load-bearing terms |
| `philosophy/core-principles.md` | Invariant | Load-bearing beliefs that shape architectural decisions |
| `constraints/hard-boundaries.md` | Invariant | Rules that must never be broken — single source |
| `02-adrs/ADR-0001` | Invariant (append-only) | RUMMAN as Operational Intelligence OS |
| `02-adrs/ADR-0002` | Invariant (append-only) | Live ingestion permanently separated from backfill |
| `02-adrs/ADR-0003` | Invariant (append-only) | GitHub docs as source of truth |
| `02-adrs/ADR-0004` | Invariant (append-only) | Multi-tenant platform direction |
| `02-adrs/ADR-0005` | Invariant (append-only, partially superseded) | Three-layer platform architecture — Layer 2/3 now operational; see status note in file |
| `02-adrs/ADR-0006` | Invariant (append-only, entity model superseded) | Canonical knowledge entities — production schema diverged; see RUMMAN_MASTER_DOSSIER.md §7 |
| `02-adrs/ADR-0007` | Invariant (append-only) | Storage architecture — three storage systems |
| `02-adrs/ADR-0008` | Invariant (append-only) | Telegram three-account session architecture |
| `02-adrs/ADR-0009` | Invariant (append-only) | Direct PostgREST — no ORM, no Supabase client library |
| `02-adrs/ADR-0010` | Invariant (append-only) | Anti-hallucination as architecture |
| `01-architecture/claim-model.md` | Maintained | The core primitive — what a claim is and how it flows through the system |
| `01-architecture/current-architecture.md` | Maintained | Current runtime topology — 8 processes, three-layer status |
| `01-architecture/data-spine.md` | Maintained | Layer 1 architecture and current gaps |
| `01-architecture/ingestion-architecture.md` | Maintained | Ingestion pipeline, two-speed model |
| `04-database/supabase-schema.md` | Maintained | Schema descriptions — all tables through Phase 2 (migrations 001–033) |
| `03-workflows/railway-processes.md` | Maintained | All 8 runtime processes, session architecture, deployment notes |
| `03-workflows/n8n-workflows.md` | Maintained | Planned n8n workflow inventory (not yet deployed) |
| `06-roadmap/roadmap.md` | Maintained | Phase structure and current status |
| `07-knowledge-layer/knowledge-layer-overview.md` | Maintained | Layer 2 design, per-modality pipelines, current operational status |
| `08-product-strategy/product-doctrine.md` | Maintained | Product identity, stage model, trust theory, monetization hypotheses, dependency analysis |
| `founder-doctrine.md` | Maintained | Founder-level doctrine — confirmed decisions, working hypotheses, open questions across 10 domains |
| `RUMMAN_MASTER_DOSSIER.md` (repo root) | Generated | Complete institutional memory — produced at Phase 2 completion (2026-06-01) |

---

## Rules for Adding Documentation

**Add a new ADR when:** a significant architectural decision is made, reversed, or superseded. Small implementation choices don't need ADRs. Decisions that constrain future options do.

**Add a philosophy document when:** a foundational concept needs precise definition and that definition doesn't belong in an ADR (ADRs record decisions; philosophy documents record beliefs and vocabulary).

**Add an architecture document when:** a subsystem is complex enough that a new engineer would need more than 20 minutes to understand it from code alone.

**Do not add documentation:** to record ephemeral task state (use git commits and PRs), to pad coverage metrics, or to describe things that are obvious from the code.

---

## How AI Systems Should Use This Documentation

Before making architectural recommendations: read `philosophy/vocabulary.md`, `philosophy/core-principles.md`, and the relevant ADRs.

Before editing architecture documents: verify against constraints in `constraints/hard-boundaries.md`.

Before creating new schema or tables: check alignment with `01-architecture/claim-model.md` and `02-adrs/ADR-0006-canonical-knowledge-entities.md`.

When in doubt about whether something is safe to change autonomously: if the document is in `philosophy/` or `constraints/`, it is not. Propose the change and wait for human decision.
