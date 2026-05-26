# ADR-0004: Multi-Tenant Platform Direction

## Status

Accepted

## Context

RUMMAN started as an experimental operational intelligence system using Telegram, Supabase, Railway, n8n, and OpenAI APIs.

As the architecture evolved, it became clear that RUMMAN is intended to become a commercial platform rather than a personal-only system.

The platform is expected to support multiple independent customers, organizations, teams, groups, students, and operational environments.

## Decision

RUMMAN will be designed as a multi-tenant operational intelligence platform.

Tenant isolation must become a first-class architectural concern from early stages of development.

## Tenant Examples

Potential tenants include:

- companies
- departments
- operational teams
- students
- universities
- event organizers
- startups
- family organizations
- community groups

## Student Intelligence Direction

RUMMAN should eventually support academic operational intelligence use cases.

Potential capabilities include:

- assignment tracking
- deadline intelligence
- lecture summarization
- academic memory
- study workflow analysis
- academic risk detection
- university communication ingestion

## Architectural Implications

The platform must eventually support:

- tenant isolation
- tenant-level permissions
- tenant-specific ingestion
- tenant-specific AI memory
- tenant-specific workflows
- tenant-specific dashboards
- tenant-scoped observability
- tenant-scoped billing

## Data Model Direction

Core operational tables should eventually become tenant-aware.

Examples:

- messages
- entities
- memories
- tasks
- decisions
- deadlines
- insights
- jobs

will require tenant ownership metadata.

## Long-Term Direction

RUMMAN should evolve into a scalable Operational Intelligence SaaS Platform rather than a single-user automation system.
