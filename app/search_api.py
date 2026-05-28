#!/usr/bin/env python3
"""
search_api.py — Semantic search over RUMMAN's knowledge base.

POST /search   { query, limit?, course_code?, source_type? }
GET  /health

Embeds the query with text-embedding-3-large (1536 dims), calls the
match_documents Supabase RPC, returns ranked document_chunks.
"""

import os
import hashlib
import httpx

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

EMBED_MODEL = "text-embedding-3-large"
EMBED_DIMS  = 1536

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}

app = FastAPI(title="RUMMAN Search API", version="1.0")
ai  = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])


MIN_SIMILARITY = 0.35  # discard results below this threshold


def _deduplicate(results: list[dict], limit: int) -> list[dict]:
    """Keep highest-similarity result per unique content fingerprint."""
    seen: dict[str, dict] = {}
    for row in results:
        key = hashlib.md5((row.get("content") or "").encode()).hexdigest()
        if key not in seen or row.get("similarity", 0) > seen[key].get("similarity", 0):
            seen[key] = row
    deduped = sorted(seen.values(), key=lambda r: r.get("similarity", 0), reverse=True)
    return deduped[:limit]


class SearchRequest(BaseModel):
    query:       str
    limit:       int  = Field(default=10, ge=1, le=50)
    course_code: str  | None = None
    source_type: str  | None = None  # "exam" | "study_plan" | "upload"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/search")
async def search(req: SearchRequest):
    # 1. Embed the query
    resp = await ai.embeddings.create(
        model=EMBED_MODEL, input=req.query, dimensions=EMBED_DIMS
    )
    embedding = resp.data[0].embedding

    # 2. Semantic search via Supabase RPC — fetch 3× limit to have headroom after dedup
    fetch_count = min(req.limit * 3, 150)
    async with httpx.AsyncClient(timeout=30) as http:
        r = await http.post(
            f"{SUPABASE_URL}/rest/v1/rpc/match_documents",
            headers=HEADERS,
            json={
                "query_embedding": embedding,
                "match_count":     fetch_count,
                "filter_course":   req.course_code,
                "filter_type":     req.source_type,
            },
        )

    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=r.text[:300])

    raw = r.json()

    # 3. Drop low-confidence results, deduplicate by content, re-rank
    filtered = [row for row in raw if (row.get("similarity") or 0) >= MIN_SIMILARITY]
    results  = _deduplicate(filtered, req.limit)

    return {
        "query":        req.query,
        "count":        len(results),
        "raw_fetched":  len(raw),
        "results":      results,
    }
