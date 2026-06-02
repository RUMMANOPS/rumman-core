# ADR-0009: Direct PostgREST — No ORM, No Supabase Client Library

## Status

Accepted

## Context

RUMMAN's workers interact with Supabase (PostgreSQL) as the operational data plane. Database interactions include:
- Inserting messages with deduplication (treat HTTP 409 as "already exists")
- Conditional PATCH for lease acquisition (only claim a job if status is still 'pending')
- Upsert operations (insert or merge on conflict)
- Retrieving rows with specific header requirements

Supabase provides a Python client library (`supabase-py`) that abstracts HTTP calls against the PostgREST REST API.

**The problem:** The Supabase client library abstracts in ways that prevent precise control over the exact HTTP semantics RUMMAN requires:

1. **Deduplication via 409:** The client throws an exception on 409 responses rather than returning a meaningful result. RUMMAN needs to distinguish "duplicate — skip" (409) from "real error — alert" (4xx other). The client makes this distinction difficult.

2. **Conditional PATCH for lease acquisition:** The backfill and media workers use an optimistic locking pattern: `PATCH telegram_backfill_jobs WHERE id={job_id} AND status='pending'`. If the PATCH affects 0 rows, another worker already claimed it. This requires the condition to be in the URL filter — a precise HTTP behavior the client abstracts away.

3. **Upsert with `Prefer: resolution=merge-duplicates`:** Supabase's upsert requires combining `Prefer: resolution=merge-duplicates` with `?on_conflict=column` in the URL. The client's upsert method does not expose this combination cleanly.

4. **`Prefer: return=representation`:** RUMMAN frequently needs the inserted row's ID back after insert (to create foreign key references). This requires the `Prefer: return=representation` header. The client's abstraction doesn't guarantee this.

## Decision

All database access in RUMMAN is via **direct HTTP calls against the Supabase PostgREST API** using `httpx.AsyncClient`. No ORM (SQLAlchemy, Django ORM, etc.). No Supabase client library.

**Pattern:**
```python
# Insert
resp = await client.post(
    f"{SUPABASE_URL}/rest/v1/{table}",
    headers={"Prefer": "return=representation"},
    json=payload
)
if resp.status_code == 409:
    return "duplicate"
if resp.status_code >= 400:
    return "error"
return resp.json()[0]  # inserted row

# Conditional PATCH (lease acquisition)
resp = await client.patch(
    f"{SUPABASE_URL}/rest/v1/{table}?id=eq.{job_id}&status=eq.pending",
    json={"status": "running", "worker_id": my_id}
)
if resp.status_code == 200 and resp.json():
    # claimed
else:
    # another worker got it

# Upsert
resp = await client.post(
    f"{SUPABASE_URL}/rest/v1/{table}?on_conflict=unique_column",
    headers={"Prefer": "resolution=merge-duplicates,return=representation"},
    json=payload
)
```

**HTTP client:** `httpx.AsyncClient` at the module level, not per-request. One client instance per worker process, reused across all requests.

**Auth:** `Authorization: Bearer {SUPABASE_KEY}` (service-role key) on every request. No per-request auth setup.

## Consequences

### Positive

- Complete control over HTTP semantics: headers, status code handling, URL filters
- 409 deduplication is explicit and intentional — not an exception
- Conditional PATCH lease pattern is expressible directly
- No dependency on library version for core operational behavior
- Debugging HTTP interactions is straightforward (log the request, see exactly what PostgREST received)
- No ORM schema definition required — queries use table names directly

### Negative

- More verbose than ORM calls
- No query builder — SQL-like operations must be expressed as URL parameters
- Developers unfamiliar with PostgREST API syntax need to learn it
- Type safety is at the application layer (dict payloads), not enforced by a schema

## Explicitly Avoided Approaches

**SQLAlchemy or other ORMs:** ORMs require schema reflection or model definition, add migration tooling, and abstract the HTTP behavior RUMMAN needs to control. At RUMMAN's scale (simple worker patterns, no complex joins at the application layer), the overhead is not justified.

**Supabase Python client library:** Abstracts the HTTP interactions RUMMAN needs to control precisely. Specific blockers: 409 exception behavior, conditional PATCH semantics, `Prefer` header combinations.

**Bulk operations via direct PostgreSQL connection:** Direct Postgres connection (psycopg2/asyncpg) bypasses PostgREST and RLS. This is the right tool for bulk migrations and administrative operations, but not for operational worker code where PostgREST's RLS policies provide an additional isolation layer.
