#!/usr/bin/env python3
"""
search_api.py — RUMMAN Platform API + semantic search.

Search pipeline (POST /search):
  1. Static normalization   (normalization_dict.json, free)
  2. Intent classification  (gpt-4o-mini, structured output, ~$0.0001)
  3. Deterministic routing  (course_code + source_type filters from intent)
  4. Corpus retrieval       (pgvector match_documents RPC)
  5. Anti-hallucination gate (similarity threshold per search pass)
  6. Deduplication + re-rank
  7. Learning event log     (fire-and-forget, never blocks response)

Platform API (v1):
  POST /v1/users/identify    — get or create pseudonymous user
  POST /v1/sessions          — create or resume session
  PATCH /v1/sessions/{id}    — update session context
  POST /v1/sessions/{id}/feedback — submit response feedback

OpenAI classifies intent and translates queries. Corpus is sole source of truth.
"""

import os
import sys
import time
import hashlib
import asyncio
import logging
import httpx

sys.path.insert(0, os.path.dirname(__file__))

from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from openai import AsyncOpenAI
from dotenv import load_dotenv

from query_understanding import (
    load_dicts,
    understand_query,
    build_search_params,
    QueryUnderstanding,
)

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

EMBED_MODEL = "text-embedding-3-large"
EMBED_DIMS  = 1536

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}

MIN_SIMILARITY        = 0.45  # broad search — no course filter
MIN_SIMILARITY_COURSE = 0.25  # course-filtered — lower ok, scope already constrained

SEU_TENANT_ID = "00000000-0000-0000-0000-000000000001"

# Session inactivity window: sessions inactive longer than this get a new one created
SESSION_TTL_SECONDS = 30 * 60


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dicts()
    yield


app = FastAPI(title="RUMMAN Platform API", version="3.0", lifespan=lifespan)
ai  = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    query:       str
    limit:       int         = Field(default=10, ge=1, le=50)
    course_code: str | None  = None
    source_type: str | None  = None
    session_id:  str | None  = None
    user_id:     str | None  = None


class UserIdentifyRequest(BaseModel):
    platform:           str
    platform_user_hash: str
    tenant_slug:        str = "seu"


class SessionCreateRequest(BaseModel):
    user_id:     str
    platform:    str
    tenant_slug: str = "seu"


class SessionUpdateRequest(BaseModel):
    active_course_code: str | None = None
    active_exam_type:   str | None = None
    session_context:    dict | None = None


class FeedbackRequest(BaseModel):
    event_id: str | None = None
    helpful:  bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tenant_id_for_slug(slug: str) -> str:
    if slug == "seu":
        return SEU_TENANT_ID
    return SEU_TENANT_ID  # default until multi-tenant expansion


def _deduplicate(results: list[dict], limit: int) -> list[dict]:
    """Keep highest-similarity result per unique content fingerprint."""
    seen: dict[str, dict] = {}
    for row in results:
        key = hashlib.md5((row.get("content") or "").encode()).hexdigest()
        if key not in seen or row.get("similarity", 0) > seen[key].get("similarity", 0):
            seen[key] = row
    deduped = sorted(seen.values(), key=lambda r: r.get("similarity", 0), reverse=True)
    return deduped[:limit]


# ---------------------------------------------------------------------------
# Corpus retrieval
# ---------------------------------------------------------------------------

async def _retrieve(
    http: httpx.AsyncClient,
    embedding: list[float],
    course_code: str | None,
    source_type: str | None,
    match_count: int,
) -> list[dict]:
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/rpc/match_documents",
        headers=HEADERS,
        json={
            "query_embedding": embedding,
            "match_count":     match_count,
            "filter_course":   course_code,
            "filter_type":     source_type,
        },
    )
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=r.text[:300])
    return r.json()


# ---------------------------------------------------------------------------
# Learning event logging (fire-and-forget)
# ---------------------------------------------------------------------------

async def _log_event(
    event_type: str,
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    tenant_id: str = SEU_TENANT_ID,
    understanding: QueryUnderstanding | None = None,
    result_count: int | None = None,
    top_similarity: float | None = None,
    grounded: bool | None = None,
    latency_ms: int | None = None,
    metadata: dict | None = None,
) -> None:
    """Write one learning_events row. Never raises — must not block responses."""
    try:
        intent = understanding.intent if understanding else None
        row: dict = {
            "event_type":         event_type,
            "session_id":         session_id,
            "user_id":            user_id,
            "tenant_id":          tenant_id,
            "query_raw":          understanding.query_raw if understanding else None,
            "query_normalized":   (understanding.query_normalized
                                   if understanding and understanding.query_normalized != understanding.query_raw
                                   else None),
            "intent_type":        intent.intent_type if intent else None,
            "intent_confidence":  intent.confidence  if intent else None,
            "course_codes":       intent.course_codes if intent else [],
            "concept_ids":        [],  # populated after concept layer is built
            "retrieval_count":    result_count,
            "top_similarity":     top_similarity,
            "grounded":           grounded,
            "latency_ms":         latency_ms,
            "metadata":           metadata or {},
        }
        async with httpx.AsyncClient(timeout=5) as http:
            await http.post(
                f"{SUPABASE_URL}/rest/v1/learning_events",
                headers=HEADERS,
                json=row,
            )
    except Exception as exc:
        log.warning("learning_event write failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Platform API: /v1/users
# ---------------------------------------------------------------------------

@app.post("/v1/users/identify")
async def identify_user(req: UserIdentifyRequest):
    """
    Get or create a pseudonymous user.
    The platform_user_hash must be computed by the caller as:
      SHA-256(RUMMAN_USER_SALT + ":" + platform + ":" + raw_user_id)
    Raw user IDs are never sent to or stored by the platform.
    """
    tenant_id = _tenant_id_for_slug(req.tenant_slug)
    async with httpx.AsyncClient(timeout=10) as http:
        # Try to find existing user
        r = await http.get(
            f"{SUPABASE_URL}/rest/v1/rumman_users",
            headers=HEADERS,
            params={
                "platform":           f"eq.{req.platform}",
                "platform_user_hash": f"eq.{req.platform_user_hash}",
                "select":             "id,tenant_id,opted_into_memory",
                "limit":              "1",
            },
        )
        existing = r.json()
        if existing:
            user = existing[0]
            # Update last_active_at
            await http.patch(
                f"{SUPABASE_URL}/rest/v1/rumman_users?id=eq.{user['id']}",
                headers=HEADERS,
                json={"last_active_at": "now()"},
            )
            return {"user_id": user["id"], "created": False}

        # Create new user
        r = await http.post(
            f"{SUPABASE_URL}/rest/v1/rumman_users",
            headers={**HEADERS, "Prefer": "return=representation"},
            json={
                "platform":           req.platform,
                "platform_user_hash": req.platform_user_hash,
                "tenant_id":          tenant_id,
            },
        )
        if r.status_code not in (200, 201):
            raise HTTPException(status_code=502, detail="user creation failed")
        user = r.json()[0]
        return {"user_id": user["id"], "created": True}


# ---------------------------------------------------------------------------
# Platform API: /v1/sessions
# ---------------------------------------------------------------------------

@app.post("/v1/sessions")
async def create_or_resume_session(req: SessionCreateRequest):
    """
    Resume the most recent active session for this user if within TTL,
    otherwise create a new one.
    """
    tenant_id = _tenant_id_for_slug(req.tenant_slug)
    async with httpx.AsyncClient(timeout=10) as http:
        # Look for an active session within TTL
        r = await http.get(
            f"{SUPABASE_URL}/rest/v1/rumman_sessions",
            headers=HEADERS,
            params={
                "user_id":  f"eq.{req.user_id}",
                "is_active": "eq.true",
                "platform":  f"eq.{req.platform}",
                "order":     "last_active_at.desc",
                "limit":     "1",
                "select":    "id,last_active_at,active_course_code,active_exam_type,turn_count",
            },
        )
        sessions = r.json()
        if sessions:
            s = sessions[0]
            # Check TTL
            last_active = s.get("last_active_at", "")
            # Parse and check if within SESSION_TTL_SECONDS
            try:
                from datetime import datetime, timezone
                last_dt = datetime.fromisoformat(last_active.replace("Z", "+00:00"))
                age_seconds = (datetime.now(timezone.utc) - last_dt).total_seconds()
                if age_seconds <= SESSION_TTL_SECONDS:
                    return {
                        "session_id": s["id"],
                        "created": False,
                        "active_course_code": s.get("active_course_code"),
                        "turn_count": s.get("turn_count", 0),
                    }
            except Exception:
                pass  # fall through to create new

        # Create new session
        r = await http.post(
            f"{SUPABASE_URL}/rest/v1/rumman_sessions",
            headers={**HEADERS, "Prefer": "return=representation"},
            json={
                "user_id":   req.user_id,
                "tenant_id": tenant_id,
                "platform":  req.platform,
            },
        )
        if r.status_code not in (200, 201):
            raise HTTPException(status_code=502, detail="session creation failed")
        session = r.json()[0]
        return {"session_id": session["id"], "created": True, "turn_count": 0}


@app.patch("/v1/sessions/{session_id}")
async def update_session(session_id: str, req: SessionUpdateRequest):
    """Update session context, active course, or exam type."""
    patch: dict = {"last_active_at": "now()"}
    if req.active_course_code is not None:
        patch["active_course_code"] = req.active_course_code
    if req.active_exam_type is not None:
        patch["active_exam_type"] = req.active_exam_type
    if req.session_context is not None:
        patch["session_context"] = req.session_context

    async with httpx.AsyncClient(timeout=10) as http:
        await http.patch(
            f"{SUPABASE_URL}/rest/v1/rumman_sessions?id=eq.{session_id}",
            headers=HEADERS,
            json=patch,
        )
    return {"ok": True}


@app.post("/v1/sessions/{session_id}/feedback")
async def submit_feedback(session_id: str, req: FeedbackRequest):
    """
    Record a user's feedback on the most recent response in a session.
    Generates a learning_event of type feedback_positive or feedback_negative.
    """
    event_type = "feedback_positive" if req.helpful else "feedback_negative"
    asyncio.create_task(_log_event(
        event_type,
        session_id=session_id,
        metadata={"event_id": req.event_id},
    ))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "version": "3.0"}


# ---------------------------------------------------------------------------
# Search (backward-compatible + session-aware)
# ---------------------------------------------------------------------------

@app.post("/search")
async def search(req: SearchRequest):
    t_start = time.monotonic()

    # Step 1+2: normalize + classify intent
    understanding = await understand_query(req.query, ai=ai, run_classifier=True)
    intent = understanding.intent

    # Step 3: build search param list
    if req.course_code or req.source_type:
        from query_understanding import SearchParams
        param_list = [SearchParams(
            query=intent.normalized_text if intent else understanding.query_normalized,
            course_code=req.course_code,
            source_type=req.source_type,
            limit=req.limit,
        )]
    else:
        param_list = build_search_params(understanding, req.limit)

    # Step 4: embed + retrieve per unique query
    seen_queries: set[str] = set()
    all_raw: list[dict] = []
    params_log: list[dict] = []

    async with httpx.AsyncClient(timeout=30) as http:
        for params in param_list:
            q = params.query
            if q in seen_queries:
                continue
            seen_queries.add(q)

            resp = await ai.embeddings.create(
                model=EMBED_MODEL, input=q, dimensions=EMBED_DIMS
            )
            embedding = resp.data[0].embedding
            fetch_count = min(params.limit * 3, 150)

            threshold = MIN_SIMILARITY_COURSE if params.course_code else MIN_SIMILARITY
            raw = await _retrieve(http, embedding, params.course_code, params.source_type, fetch_count)
            for row in raw:
                if (row.get("similarity") or 0) >= threshold:
                    all_raw.append(row)
            params_log.append({
                "query":       q,
                "course_code": params.course_code,
                "source_type": params.source_type,
            })

    # Step 5: dedup
    results  = _deduplicate(all_raw, req.limit)
    top_sim  = results[0].get("similarity") if results else None
    grounded = len(results) > 0
    latency  = int((time.monotonic() - t_start) * 1000)

    # Step 6: update session focus if course detected
    if req.session_id and intent and intent.course_codes:
        asyncio.create_task(_patch_session_focus(
            req.session_id,
            intent.course_codes[0],
            intent.exam_type,
        ))

    # Step 7: log learning event (replaces query_logs for new writes)
    event_type = "zero_result" if not grounded else "query"
    asyncio.create_task(_log_event(
        event_type,
        session_id=req.session_id,
        user_id=req.user_id,
        understanding=understanding,
        result_count=len(results),
        top_similarity=top_sim,
        grounded=grounded,
        latency_ms=latency,
        metadata={"search_params": params_log},
    ))

    # Build debug info
    debug: dict = {}
    if understanding.classifier_used and intent:
        debug = {
            "intent_type":       intent.intent_type,
            "intent_confidence": intent.confidence,
            "course_codes":      intent.course_codes,
            "exam_type":         intent.exam_type,
            "source_type":       intent.source_type_filter,
            "clarification":     intent.clarification_question if intent.clarification_needed else None,
        }

    return {
        "query":            req.query,
        "normalized_query": understanding.query_normalized
                            if understanding.query_normalized != req.query else None,
        "count":            len(results),
        "raw_fetched":      len(all_raw),
        "grounded":         grounded,
        "latency_ms":       latency,
        "debug":            debug or None,
        "results":          results,
    }


async def _patch_session_focus(session_id: str, course_code: str, exam_type: str | None) -> None:
    """Background: update session's active course/exam focus. Non-fatal."""
    try:
        async with httpx.AsyncClient(timeout=5) as http:
            await http.patch(
                f"{SUPABASE_URL}/rest/v1/rumman_sessions?id=eq.{session_id}",
                headers=HEADERS,
                json={
                    "active_course_code": course_code,
                    "active_exam_type":   exam_type,
                    "last_active_at":     "now()",
                },
            )
    except Exception as exc:
        log.warning("session focus update failed (non-fatal): %s", exc)
