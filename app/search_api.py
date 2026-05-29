#!/usr/bin/env python3
"""
search_api.py — RUMMAN Platform API + semantic search + grounded synthesis.

Search pipeline (POST /search):
  1. Static normalization   (normalization_dict.json, free)
  2. Intent classification  (gpt-4o-mini, structured output, ~$0.0001)
  3. Deterministic routing  (course_code + source_type filters from intent)
  4. Corpus retrieval       (pgvector match_documents RPC)
  5. Anti-hallucination gate (similarity threshold per search pass)
  6. Deduplication + re-rank
  7. Learning event log     (fire-and-forget, never blocks response)

Synthesis pipeline (POST /synthesize):
  Same as /search steps 1–6, then:
  8. Grounded synthesis     (gpt-4o-mini, corpus-only constraint, ~$0.0005)
  9. Synthesis event log    (records token usage for cost observability)

  The synthesis prompt forbids GPT from using training knowledge about SEU.
  Only facts present in the retrieved chunks may appear in the answer.
  On GPT timeout or failure, falls back to returning raw chunks (graceful degradation).

Platform API (v1):
  POST /v1/users/identify    — get or create pseudonymous user
  POST /v1/sessions          — create or resume session
  PATCH /v1/sessions/{id}    — update session context
  POST /v1/sessions/{id}/feedback — submit response feedback

OpenAI classifies intent and synthesizes answers. Corpus is sole source of truth.
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
    SearchParams,
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

MIN_SIMILARITY        = 0.40  # broad search — lowered from 0.45; Arabic queries hit ~0.40–0.44
MIN_SIMILARITY_COURSE = 0.25  # course-filtered — scope already constrained

SEU_TENANT_ID       = "00000000-0000-0000-0000-000000000001"
SESSION_TTL_SECONDS = 30 * 60

# Synthesis prompt — grounded academic companion
_SYNTHESIS_SYSTEM = """\
You are رمّان (Rummaan) — an intelligent academic companion for Saudi Electronic University students.

Each source chunk is tagged with an authority tier:
  [OFFICIAL]   — extracted from official university documents (study plans, regulations, course descriptions)
  [COMMUNITY]  — student-shared materials (exam archives, notes, group discussions)

Grounding rules:
- Use ONLY information present in the provided source chunks. Do not invent or extrapolate.
- Chunks may be in Arabic or English — understand both; respond in the student's language.
- When OFFICIAL and COMMUNITY sources agree: answer directly.
- When they differ or conflict: present the official position first, then note the community perspective.
- When chunks contain exam questions: identify the topics and concepts they test, present them clearly. Complete and valid — do not hedge.
- When chunks contain definitions, explanations, or course content: synthesize in your own words. Be the intelligent companion, not a copy-paste machine.
- When chunks partially answer the question: share what you found and be honest about the gap.
- When chunks are off-topic: say "ما لقيت إجابة واضحة في المواد المتاحة — جرّب تذكر رمز المادة أو اسأل بطريقة مختلفة."

Style:
- Gulf Arabic (خليجي) for Arabic questions. Clear, natural English for English questions.
- Be direct, specific, and substantive. 150–250 words.
- Do NOT mention professor names or predict unreleased exam content.
- Do NOT add meta-commentary ("Based on the sources...", "According to the chunks...").
- Do NOT explain what you're doing — just answer.\
"""

_SYNTHESIS_USER = "Student question: {query}\n\nSource chunks:\n{chunks}"

_SYNTHESIS_TIMEOUT = 12.0  # seconds; on timeout, fall back to chunk display


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dicts()
    yield


app = FastAPI(title="RUMMAN Platform API", version="4.0", lifespan=lifespan)
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


class SynthesizeRequest(BaseModel):
    query:      str
    limit:      int        = Field(default=5, ge=1, le=20)
    session_id: str | None = None
    user_id:    str | None = None


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
    return SEU_TENANT_ID


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
# Structured curriculum query (institutional layer — bypasses vector search)
# ---------------------------------------------------------------------------

async def _retrieve_curriculum_facts(
    http: httpx.AsyncClient,
    course_codes: list[str],
) -> list[dict]:
    """Query inst_courses directly for authoritative course metadata.
    Returns results in the same shape as match_documents rows so they flow
    through the same dedup/synthesis pipeline without special-casing."""
    if not course_codes:
        return []

    filter_val = f"in.({','.join(course_codes)})"
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/inst_courses",
        headers=HEADERS,
        params={
            "code": filter_val,
            "tenant_id": f"eq.{SEU_TENANT_ID}",
            "select": "code,name_ar,name_en,credit_hours,level,is_required,prerequisites",
            "limit": "10",
        },
    )
    if r.status_code >= 400:
        log.warning("curriculum_facts_error | status=%s", r.status_code)
        return []

    facts = []
    for row in r.json():
        code = row.get("code", "")
        name_ar = row.get("name_ar") or ""
        name_en = row.get("name_en") or ""
        credits  = row.get("credit_hours")
        level    = row.get("level")
        is_req   = row.get("is_required")
        prereqs  = row.get("prerequisites") or []

        if not (name_ar or name_en):
            continue  # seed not yet run — no useful content to inject

        lines = [f"المقرر: {code}"]
        if name_en:
            lines.append(f"الاسم (إنجليزي): {name_en}")
        if name_ar:
            lines.append(f"الاسم (عربي): {name_ar}")
        if credits:
            lines.append(f"الساعات المعتمدة: {credits}")
        if level:
            lines.append(f"المستوى الدراسي: {level}")
        if is_req is not None:
            lines.append("مقرر إلزامي" if is_req else "مقرر اختياري")
        if prereqs:
            lines.append(f"المتطلبات السابقة: {', '.join(prereqs)}")

        facts.append({
            "content":          "\n".join(lines),
            "course_code":      code,
            "source_type":      "course_description",
            "source_authority": "official",
            "similarity":       0.95,  # exact code match — treat as top result
            "metadata":         {"origin": "inst_courses"},
        })

    return facts


# ---------------------------------------------------------------------------
# Academic calendar retrieval (temporal intents — bypasses vector search)
# ---------------------------------------------------------------------------

async def _retrieve_calendar_events(http: httpx.AsyncClient) -> list[dict]:
    """Query academic_calendar directly for exam_schedule / deadline intents.
    Returns a single rich chunk with all events + days-until calculations."""
    from datetime import date
    today = date.today()

    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/academic_calendar",
        headers=HEADERS,
        params={
            "tenant_id": f"eq.{SEU_TENANT_ID}",
            "select":    "event_type,event_name_ar,event_name_en,start_date,end_date,semester_key,academic_year,semester",
            "order":     "start_date.asc",
        },
    )
    if r.status_code >= 400:
        return []
    events = r.json()
    if not events:
        return []

    lines = [f"التاريخ اليوم: {today.isoformat()}\n\nالتقويم الأكاديمي — الجامعة السعودية الإلكترونية 1447هـ / 2025-2026:\n"]
    for ev in events:
        start   = ev.get("start_date")
        end     = ev.get("end_date")
        name_ar = ev.get("event_name_ar") or ev.get("event_type", "")
        name_en = ev.get("event_name_en") or ev.get("event_type", "")
        sem_key = ev.get("semester_key") or f"{ev.get('academic_year','')}-{ev.get('semester','')}"

        date_str = start or "—"
        if end and end != start:
            date_str = f"{start} → {end}"

        timing = ""
        if start:
            try:
                delta = (date.fromisoformat(start) - today).days
                if delta > 0:
                    timing = f"  ← بعد {delta} يوم"
                elif delta == 0:
                    timing = "  ← اليوم"
                else:
                    timing = f"  ← انتهى منذ {abs(delta)} يوم"
            except ValueError:
                pass

        lines.append(f"• {name_ar} / {name_en}:  {date_str}{timing}  [{sem_key}]")

    return [{
        "content":          "\n".join(lines),
        "course_code":      None,
        "source_type":      "regulation",
        "source_authority": "official",
        "authority_tier":   "official",
        "similarity":       0.99,
        "metadata":         {"origin": "academic_calendar"},
    }]


# ---------------------------------------------------------------------------
# Shared retrieval pipeline
# ---------------------------------------------------------------------------

async def _run_retrieval(
    query: str,
    limit: int,
    course_code: str | None = None,
    source_type: str | None = None,
) -> tuple[list[dict], list[dict], QueryUnderstanding, list[dict]]:
    """
    Run the full retrieval pipeline: understand → embed → retrieve → dedup.
    Returns (results, all_raw, understanding, params_log).
    """
    understanding = await understand_query(query, ai=ai, run_classifier=True)
    intent = understanding.intent

    if course_code or source_type:
        param_list = [SearchParams(
            query=intent.normalized_text if intent else understanding.query_normalized,
            course_code=course_code,
            source_type=source_type,
            limit=limit,
        )]
    else:
        param_list = build_search_params(understanding, limit)

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

        # Inject structured course facts for any detected course codes (institutional layer)
        if understanding.intent and understanding.intent.course_codes:
            curriculum = await _retrieve_curriculum_facts(http, understanding.intent.course_codes)
            all_raw.extend(curriculum)

        # Inject calendar events for temporal intents — deterministic, not vector search
        if (understanding.intent and
                understanding.intent.intent_type in ("exam_schedule", "deadline")):
            calendar = await _retrieve_calendar_events(http)
            all_raw.extend(calendar)

    results = _deduplicate(all_raw, limit)
    return results, all_raw, understanding, params_log


# ---------------------------------------------------------------------------
# Grounded synthesis
# ---------------------------------------------------------------------------

async def _synthesize_answer(query: str, chunks: list[dict]) -> tuple[str, int]:
    """
    Call gpt-4o-mini to synthesize a grounded answer from chunks.
    Returns (answer_text, total_tokens_used).
    Raises asyncio.TimeoutError on timeout — caller handles fallback.
    """
    def _tier_label(row: dict) -> str:
        tier = row.get("authority_tier") or (row.get("metadata") or {}).get("origin", "")
        if tier == "official" or "inst_courses" in tier or "seu_courses" in tier:
            return "[OFFICIAL]"
        return "[COMMUNITY]"

    chunk_text = "\n\n---\n\n".join(
        f"{_tier_label(row)} [{i+1}] {(row.get('content') or '').strip()[:500]}"
        for i, row in enumerate(chunks[:5])
    )
    resp = await asyncio.wait_for(
        ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYNTHESIS_SYSTEM},
                {"role": "user",   "content": _SYNTHESIS_USER.format(
                    query=query, chunks=chunk_text
                )},
            ],
            temperature=0.1,
            max_tokens=350,
        ),
        timeout=_SYNTHESIS_TIMEOUT,
    )
    answer = resp.choices[0].message.content.strip()
    tokens = resp.usage.total_tokens if resp.usage else 0
    return answer, tokens


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
            "concept_ids":        [],
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
    platform_user_hash = SHA-256(RUMMAN_USER_SALT + ":" + platform + ":" + raw_user_id)
    Raw user IDs are never sent to or stored by the platform.
    """
    tenant_id = _tenant_id_for_slug(req.tenant_slug)
    async with httpx.AsyncClient(timeout=10) as http:
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
            await http.patch(
                f"{SUPABASE_URL}/rest/v1/rumman_users?id=eq.{user['id']}",
                headers=HEADERS,
                json={"last_active_at": "now()"},
            )
            return {"user_id": user["id"], "created": False}

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
    """Resume the most recent active session if within TTL, else create new."""
    tenant_id = _tenant_id_for_slug(req.tenant_slug)
    async with httpx.AsyncClient(timeout=10) as http:
        r = await http.get(
            f"{SUPABASE_URL}/rest/v1/rumman_sessions",
            headers=HEADERS,
            params={
                "user_id":   f"eq.{req.user_id}",
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
            try:
                from datetime import datetime, timezone
                last_dt = datetime.fromisoformat(
                    s.get("last_active_at", "").replace("Z", "+00:00")
                )
                age_seconds = (datetime.now(timezone.utc) - last_dt).total_seconds()
                if age_seconds <= SESSION_TTL_SECONDS:
                    return {
                        "session_id":        s["id"],
                        "created":           False,
                        "active_course_code": s.get("active_course_code"),
                        "turn_count":        s.get("turn_count", 0),
                    }
            except Exception:
                pass

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
    """Record feedback as a learning_event."""
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
    return {"status": "ok", "version": "4.0"}


# ---------------------------------------------------------------------------
# Search (raw retrieval — for debugging, evaluation, and direct API consumers)
# ---------------------------------------------------------------------------

@app.post("/search")
async def search(req: SearchRequest):
    t_start = time.monotonic()

    results, all_raw, understanding, params_log = await _run_retrieval(
        req.query, req.limit, req.course_code, req.source_type
    )
    intent    = understanding.intent
    top_sim   = results[0].get("similarity") if results else None
    grounded  = len(results) > 0
    latency   = int((time.monotonic() - t_start) * 1000)

    if req.session_id and intent and intent.course_codes:
        asyncio.create_task(_patch_session_focus(
            req.session_id, intent.course_codes[0], intent.exam_type,
        ))

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


# ---------------------------------------------------------------------------
# Synthesize (grounded answer from corpus — used by the bot)
# ---------------------------------------------------------------------------

@app.post("/synthesize")
async def synthesize(req: SynthesizeRequest):
    """
    Retrieve relevant chunks, then synthesize a grounded answer via gpt-4o-mini.
    The synthesis prompt hard-constrains GPT to only use retrieved chunk content.
    Falls back to returning raw chunks if synthesis times out or fails.
    """
    t_start = time.monotonic()

    results, all_raw, understanding, params_log = await _run_retrieval(
        req.query, req.limit
    )
    intent   = understanding.intent
    top_sim  = results[0].get("similarity") if results else None
    grounded = len(results) > 0
    answer: str | None = None
    synthesis_tokens = 0
    synthesis_failed = False

    if grounded:
        try:
            answer, synthesis_tokens = await _synthesize_answer(req.query, results)
        except asyncio.TimeoutError:
            log.warning("synthesis_timeout | query=%.60s", req.query)
            synthesis_failed = True
        except Exception as exc:
            log.warning("synthesis_error | %s | query=%.60s", exc, req.query)
            synthesis_failed = True

    latency = int((time.monotonic() - t_start) * 1000)

    if req.session_id and intent and intent.course_codes:
        asyncio.create_task(_patch_session_focus(
            req.session_id, intent.course_codes[0], intent.exam_type,
        ))

    event_type = "zero_result" if not grounded else "synthesis"
    asyncio.create_task(_log_event(
        event_type,
        session_id=req.session_id,
        user_id=req.user_id,
        understanding=understanding,
        result_count=len(results),
        top_similarity=top_sim,
        grounded=grounded,
        latency_ms=latency,
        metadata={
            "search_params":      params_log,
            "synthesis_tokens":   synthesis_tokens,
            "synthesis_failed":   synthesis_failed,
        },
    ))

    # Build source metadata for the response (no raw content — just provenance)
    sources = [
        {
            "course_code": r.get("course_code") or (r.get("metadata") or {}).get("course_code"),
            "source_type": r.get("source_type"),
            "similarity":  round(r.get("similarity", 0), 3),
        }
        for r in results[:5]
    ]

    return {
        "query":             req.query,
        "grounded":          grounded,
        "answer":            answer,           # None if not grounded or synthesis failed
        "synthesis_failed":  synthesis_failed,
        "source_count":      len(results),
        "sources":           sources,
        "latency_ms":        latency,
        # Fallback chunks — present only when synthesis failed so caller can degrade gracefully
        "fallback_chunks":   results if synthesis_failed else [],
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
