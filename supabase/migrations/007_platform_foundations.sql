-- =============================================================================
-- 007_platform_foundations.sql
-- Platform foundations: tenants, pseudonymous users, sessions,
-- learning events, and the concept intelligence layer.
--
-- Why this migration exists:
--   Every future capability — copilot, weakness tracking, multi-tenancy,
--   institutional intelligence, concept-aware search — requires these tables.
--   Adding tenant_id, user identity, and concept relationships as afterthoughts
--   at scale is a rewrite. Adding them now at ~35k chunks and ~0 users costs
--   almost nothing and makes all future work cleaner.
--
-- Design principles:
--   - Users are pseudonymous by default. Raw platform IDs are NEVER stored.
--     Only SHA-256(salt:platform:id) hashes. Privacy-first from day one.
--   - Tenant isolation is structural, not just a column. Every user, session,
--     and event carries a tenant_id. Cross-tenant queries are explicit, not
--     accidental.
--   - The concept layer is the bridge between chunk-level retrieval and
--     concept-level intelligence. chunk_concepts + user_concept_history is
--     what makes "you've struggled with integration management" possible.
--   - learning_events replaces query_logs as the primary behavioral signal
--     table. It carries session context, concept tags, and user identity
--     that query_logs cannot.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- TENANTS
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tenants (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT        NOT NULL,
    slug        TEXT        NOT NULL UNIQUE,   -- 'seu', 'kau', 'kfupm'
    config      JSONB       NOT NULL DEFAULT '{}',
    is_active   BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed: Saudi Electronic University as default tenant
INSERT INTO tenants (id, name, slug)
VALUES ('00000000-0000-0000-0000-000000000001',
        'Saudi Electronic University', 'seu')
ON CONFLICT (slug) DO NOTHING;


-- ---------------------------------------------------------------------------
-- PSEUDONYMOUS USERS
--
-- platform_user_hash = SHA-256(RUMMAN_USER_SALT + ":" + platform + ":" + raw_id)
-- The raw platform ID (Telegram chat_id, etc.) is NEVER stored.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS rumman_users (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            UUID        REFERENCES tenants(id),
    platform             TEXT        NOT NULL,   -- 'telegram', 'web', 'api'
    platform_user_hash   TEXT        NOT NULL,   -- irreversible hash of platform identity
    opted_into_memory    BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(platform, platform_user_hash)
);

CREATE INDEX IF NOT EXISTS idx_rumman_users_tenant
    ON rumman_users(tenant_id);

CREATE INDEX IF NOT EXISTS idx_rumman_users_lookup
    ON rumman_users(platform, platform_user_hash);


-- ---------------------------------------------------------------------------
-- SESSIONS
--
-- A session is a coherent interaction window (default TTL: 30 minutes of
-- inactivity). session_context carries the minimal state needed for the
-- copilot to continue a conversation: last query, active course focus,
-- recent concept exposure.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS rumman_sessions (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID        REFERENCES rumman_users(id),
    tenant_id           UUID        REFERENCES tenants(id),
    platform            TEXT        NOT NULL,
    active_course_code  TEXT,               -- course in focus for this session
    active_exam_type    TEXT,               -- midterm / final / quiz
    session_context     JSONB       NOT NULL DEFAULT '{}',
    turn_count          INT         NOT NULL DEFAULT 0,
    is_active           BOOLEAN     NOT NULL DEFAULT TRUE,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at            TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_sessions_user
    ON rumman_sessions(user_id, last_active_at DESC);

CREATE INDEX IF NOT EXISTS idx_sessions_active
    ON rumman_sessions(user_id, is_active)
    WHERE is_active = TRUE;


-- ---------------------------------------------------------------------------
-- LEARNING EVENTS
--
-- The primary behavioral signal table. Replaces query_logs for new writes.
-- Every interaction — query, zero-result, feedback, session boundary — is
-- a timestamped, session-linked event.
--
-- concept_ids: which concepts were touched in this event (populated after
-- the concept layer is bootstrapped, NULL until then).
--
-- This table accumulates the temporal learning arc:
--   encounter → confusion → clarification → integration → recall
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS learning_events (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          UUID        REFERENCES rumman_sessions(id),
    user_id             UUID        REFERENCES rumman_users(id),
    tenant_id           UUID        REFERENCES tenants(id),
    event_type          TEXT        NOT NULL,
        -- 'query'               — standard search interaction
        -- 'zero_result'         — no grounded results returned
        -- 'feedback_positive'   — user marked response helpful
        -- 'feedback_negative'   — user marked response not helpful
        -- 'clarification_sent'  — bot asked clarifying question
        -- 'session_start'       — new session opened
        -- 'session_end'         — session closed or expired
    query_raw           TEXT,
    query_normalized    TEXT,
    intent_type         TEXT,
    intent_confidence   FLOAT,
    course_codes        TEXT[]      NOT NULL DEFAULT '{}',
    concept_ids         UUID[]      NOT NULL DEFAULT '{}',
    retrieval_count     INT,
    top_similarity      FLOAT,
    grounded            BOOLEAN,
    latency_ms          INT,
    metadata            JSONB       NOT NULL DEFAULT '{}',
    occurred_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_learning_events_session
    ON learning_events(session_id, occurred_at);

CREATE INDEX IF NOT EXISTS idx_learning_events_user
    ON learning_events(user_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_learning_events_tenant_type
    ON learning_events(tenant_id, event_type, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_learning_events_zero_results
    ON learning_events(tenant_id, occurred_at DESC)
    WHERE event_type = 'zero_result';

CREATE INDEX IF NOT EXISTS idx_learning_events_courses
    ON learning_events USING GIN(course_codes);


-- ---------------------------------------------------------------------------
-- CONCEPTS
--
-- The semantic vocabulary of RUMMAN's knowledge domain.
-- canonical_name is the normalized English key (lowercase, no punctuation).
-- tenant_id = NULL means the concept is universal across all tenants.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS concepts (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID        REFERENCES tenants(id),  -- NULL = universal
    canonical_name   TEXT        NOT NULL,   -- 'project integration management'
    display_name     TEXT        NOT NULL,   -- 'Project Integration Management'
    display_name_ar  TEXT,                   -- 'إدارة تكامل المشروع'
    description      TEXT,
    subject_area     TEXT,       -- 'it', 'management', 'finance', 'engineering', 'general'
    language         TEXT        NOT NULL DEFAULT 'en',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(canonical_name, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_concepts_tenant
    ON concepts(tenant_id);

CREATE INDEX IF NOT EXISTS idx_concepts_subject
    ON concepts(subject_area);

CREATE INDEX IF NOT EXISTS idx_concepts_name_search
    ON concepts USING GIN(to_tsvector('english', display_name));


-- ---------------------------------------------------------------------------
-- CHUNK ↔ CONCEPT ASSOCIATIONS
--
-- Maps document chunks to the concepts they instantiate.
-- relevance_weight (0–1): how central is this concept to the chunk?
-- This is what makes "find chunks about concept X" possible without
-- relying purely on embedding similarity.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chunk_concepts (
    chunk_id            UUID    NOT NULL REFERENCES document_chunks(id)
                                ON DELETE CASCADE,
    concept_id          UUID    NOT NULL REFERENCES concepts(id)
                                ON DELETE CASCADE,
    relevance_weight    FLOAT   NOT NULL DEFAULT 1.0
                                CHECK (relevance_weight BETWEEN 0 AND 1),
    extraction_method   TEXT    NOT NULL DEFAULT 'llm_batch',
    extracted_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (chunk_id, concept_id)
);

CREATE INDEX IF NOT EXISTS idx_chunk_concepts_concept
    ON chunk_concepts(concept_id, relevance_weight DESC);

CREATE INDEX IF NOT EXISTS idx_chunk_concepts_chunk
    ON chunk_concepts(chunk_id);


-- ---------------------------------------------------------------------------
-- CONCEPT RELATIONSHIPS
--
-- Structural knowledge about how concepts relate:
-- prerequisite: must understand A before B
-- related: A and B frequently appear together
-- sub_concept: B is a component of A
-- contrasts_with: A and B are often confused
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS concept_relationships (
    id                  UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    concept_id          UUID    NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    related_concept_id  UUID    NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    relationship_type   TEXT    NOT NULL,
    strength            FLOAT   NOT NULL DEFAULT 1.0
                                CHECK (strength BETWEEN 0 AND 1),
    UNIQUE(concept_id, related_concept_id),
    CHECK (concept_id != related_concept_id)
);

CREATE INDEX IF NOT EXISTS idx_concept_relationships_concept
    ON concept_relationships(concept_id);


-- ---------------------------------------------------------------------------
-- USER CONCEPT HISTORY
--
-- The longitudinal memory layer. Records which concepts a user has
-- encountered, how many times, and whether retrieval succeeded.
--
-- This is what makes "وش أكثر شيء أضعف فيه؟" answerable.
-- Without this table, every session starts from zero.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS user_concept_history (
    user_id                  UUID        NOT NULL REFERENCES rumman_users(id)
                                         ON DELETE CASCADE,
    concept_id               UUID        NOT NULL REFERENCES concepts(id)
                                         ON DELETE CASCADE,
    tenant_id                UUID        REFERENCES tenants(id),
    encounter_count          INT         NOT NULL DEFAULT 1,
    retrieval_success_count  INT         NOT NULL DEFAULT 0,
    retrieval_failure_count  INT         NOT NULL DEFAULT 0,
    first_encountered_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_encountered_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, concept_id)
);

CREATE INDEX IF NOT EXISTS idx_user_concept_history_user
    ON user_concept_history(user_id, last_encountered_at DESC);

CREATE INDEX IF NOT EXISTS idx_user_concept_history_failures
    ON user_concept_history(user_id, retrieval_failure_count DESC)
    WHERE retrieval_failure_count > 0;
