# ADR-0001: RUMMAN as an Operational Intelligence OS

## Status

Accepted

## Context

RUMMAN was conceived as a personal operational intelligence system to ingest and reason over organizational communications and documents — primarily Telegram messages.

Early decisions treated it as a message-logging or chatbot-adjacent system. As the architecture matured, it became clear that this framing was too narrow and would constrain long-term design choices.

The question of what RUMMAN fundamentally *is* needed to be answered before downstream architecture decisions could be made correctly.

## Decision

RUMMAN is an **Operational Intelligence Operating System**, not a chatbot and not a message logger.

### What this means

**Input:** RUMMAN ingests heterogeneous organizational knowledge — messages, documents, voice, images, PDFs, spreadsheets, schedules, contracts, reports, and any other artifact that carries operational information.

**Processing:** RUMMAN extracts, normalizes, and structures that knowledge into a queryable, relational, and semantic operational memory.

**Output:** RUMMAN produces actionable operational intelligence — tasks, deadlines, decisions, entities, insights, and recommendations — derived from the organizational knowledge it holds.

**Operating model:** RUMMAN operates continuously and autonomously in the background, not conversationally on demand. It is infrastructure, not a UI feature.

### What RUMMAN is not

- Not a chatbot. Does not respond to user queries directly.
- Not a message archiver. Storage is a means to intelligence, not an end.
- Not a notification system. Notifications are a downstream output of intelligence, not the core function.
- Not a workflow automation tool. Automation may be an output; it is not the architecture.

### The OS analogy

An operating system manages resources (CPU, memory, I/O) and provides a stable platform for applications to run on top of. RUMMAN manages organizational knowledge (ingestion, storage, extraction, indexing) and provides a stable platform for intelligence applications (agents, copilots, dashboards, workflows) to run on top of.

Just as an OS has a kernel (data spine), drivers (source connectors), and userland (intelligence applications), RUMMAN has Layer 1 (data spine), Layer 2 (knowledge extraction), and Layer 3 (intelligence layer).

## Consequences

### Positive

- Architecture decisions are evaluated against the platform's role as an intelligence OS, not as a chatbot or logger.
- Feature scope is defined by operational intelligence value, not conversational utility.
- The three-layer architecture (ADR-0005) follows naturally from this framing.
- Multi-tenant direction (ADR-0004) aligns with building an OS for organizations, not an app for one user.

### Negative

- The platform is more complex than a chatbot or logger would be.
- Time-to-first-value is longer because the foundation must be correct.
- Premature activation of intelligence features risks undermining platform integrity.

## Long-Term Direction

RUMMAN should evolve such that any organization can connect their communication and document sources and receive a continuously updated, tenant-isolated operational intelligence layer — without writing any code themselves.
