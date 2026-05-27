# ADR-0003: Use GitHub Docs as Source of Truth

## Status

Accepted

## Context

Early development mixed design decisions between ChatGPT conversations, mental models, and ad-hoc code conventions. No canonical location existed for architectural decisions, schema designs, or workflow documentation.

Without a source of truth, decisions drift, constraints are forgotten, new engineers have no reliable orientation, and AI engineering partners (including Claude Code) operate on incomplete or stale assumptions.

## Decision

The `docs/` directory in this repository is the **authoritative source of truth** for all architectural decisions, design rationale, workflow documentation, schema intent, and operational conventions.

### What this means in practice

**Architecture decisions** must be documented in `docs/02-adrs/` as ADRs before being implemented in code. Implementation without an ADR is architectural drift.

**Schema changes** must be documented in `docs/04-database/` and (once established) reflected in `supabase/migrations/` before being applied to Supabase. Schema changes applied only through the Supabase UI are undocumented drift.

**Workflow documentation** must be in `docs/03-workflows/`. If n8n workflows exist, they must be described here. If Railway processes exist, their intended lifecycle must be described here.

**Operational conventions** — naming patterns, lease protocols, dedup behavior, error handling expectations — belong in the architecture docs, not in code comments alone.

### What thinking tools are for

ChatGPT, Claude, and other AI tools are used for reasoning, designing, and challenging assumptions. They are **not** the source of truth. Decisions made in a conversation must be committed to `docs/` before they are considered real.

### The test for whether a decision is documented

A new engineer — or a future instance of Claude Code — reading only this repository's `docs/` directory should be able to understand:
- What RUMMAN is
- Why major architectural choices were made
- What the schema is and why
- How the runtime processes work and interact
- What is deliberately disabled and why
- What is planned and what phase it belongs to

If that understanding is not achievable from `docs/` alone, the documentation is incomplete.

## Consequences

### Positive

- Decisions are persistent and reviewable via git history.
- Architectural intent survives personnel and tooling changes.
- Claude Code and other AI partners can reason from a grounded, authoritative context.
- PRs become the enforcement mechanism for undocumented architectural drift.

### Negative

- Documentation requires discipline to maintain alongside code changes.
- Stale docs are worse than no docs — they must be actively updated.
- Some fast-moving decisions may be documented post-hoc rather than pre-implementation.

## Operational Rules

1. An ADR must exist before significant architecture changes are implemented.
2. ADR status must be updated when decisions are reversed or superseded.
3. Stub docs (empty or one-line files) are technical debt — they imply a decision exists but is not documented.
4. Schema changes require a corresponding migration file and a docs update in the same PR.
5. Claude Code is expected to read `docs/` before making architecture recommendations and to update `docs/` when making architecture changes.
