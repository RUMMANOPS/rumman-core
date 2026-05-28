#!/usr/bin/env python3
"""
search_api.py — Semantic search over RUMMAN's knowledge base.

POST /search   { query, limit?, course_code?, source_type? }
GET  /health

Pipeline per request:
  1. Static normalization   (normalization_dict.json, free)
  2. Intent classification  (gpt-4o-mini, structured output, ~$0.0001)
  3. Deterministic routing  (course_code + source_type filters from intent)
  4. Corpus retrieval       (pgvector match_documents RPC)
  5. Anti-hallucination gate (similarity threshold, returns no-data if empty)
  6. Deduplication + re-rank
  7. Query logging          (fire-and-forget, never blocks response)

OpenAI classifies intent only. Corpus is the sole source of truth.
"""

import os
import hashlib
import asyncio
import logging
import httpx

from contextlib import asynccontextmanager
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

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

EMBED_MODEL = "text-embedding-3-large"
EMBED_DIMS  = 1536

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}

MIN_SIMILARITY = 0.45


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dicts()
    yield


app = FastAPI(title="RUMMAN Search API", version="2.0", lifespan=lifespan)
ai  = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    query:       str
    limit:       int  = Field(default=10, ge=1, le=50)
    course_code: str  | None = None
    source_type: str  | None = None  # "exam" | "study_plan" | "upload"


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

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
# Corpus retrieval (single search call)
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
# Query logging (fire-and-forget)
# ---------------------------------------------------------------------------

async def _log_query(
    understanding: QueryUnderstanding,
    result_count: int,
    top_similarity: float | None,
    grounded: bool,
    search_params_used: list[dict],
) -> None:
    """Write one row to query_logs. Never raises — logging must not block responses."""
    try:
        intent = understanding.intent
        row = {
            "query_raw":          understanding.query_raw,
            "query_normalized":   understanding.query_normalized
                                  if understanding.query_normalized != understanding.query_raw
                                  else None,
            "intent_type":        intent.intent_type if intent else None,
            "intent_confidence":  intent.confidence  if intent else None,
            "course_codes":       intent.course_codes if intent else [],
            "exam_type":          intent.exam_type    if intent else None,
            "source_type_filter": intent.source_type_filter if intent else None,
            "result_count":       result_count,
            "top_similarity":     top_similarity,
            "response_grounded":  grounded,
            "search_params":      search_params_used,
        }
        async with httpx.AsyncClient(timeout=5) as http:
            await http.post(
                f"{SUPABASE_URL}/rest/v1/query_logs",
                headers=HEADERS,
                json=row,
            )
    except Exception as exc:
        logging.warning("query_log write failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/search")
async def search(req: SearchRequest):
    # Step 1+2: normalize + classify intent
    understanding = await understand_query(req.query, ai=ai, run_classifier=True)
    intent = understanding.intent

    # Step 3: build search param list (caller-supplied filters override intent when provided)
    # If the user explicitly passed course_code / source_type, honour those over intent.
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

    # Step 4: embed each unique query text + retrieve
    # Deduplicate param_list by query text to avoid redundant embedding calls
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

            raw = await _retrieve(http, embedding, params.course_code, params.source_type, fetch_count)
            all_raw.extend(raw)
            params_log.append({
                "query": q,
                "course_code": params.course_code,
                "source_type": params.source_type,
            })

    # Step 5: anti-hallucination gate + dedup
    filtered = [row for row in all_raw if (row.get("similarity") or 0) >= MIN_SIMILARITY]
    results  = _deduplicate(filtered, req.limit)

    top_sim  = results[0].get("similarity") if results else None
    grounded = len(results) > 0

    # Step 7: log (fire-and-forget)
    asyncio.create_task(_log_query(
        understanding=understanding,
        result_count=len(results),
        top_similarity=top_sim,
        grounded=grounded,
        search_params_used=params_log,
    ))

    # Build debug info only when classification ran
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
        "debug":            debug or None,
        "results":          results,
    }
