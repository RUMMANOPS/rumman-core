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
  8. Grounded synthesis     (gpt-4o-mini default, gpt-4o for comparison/low-confidence, ~$0.001)
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
from collections import OrderedDict

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

_MSG_SIGNAL_LABELS: dict[str, str] = {
    "exam_emphasis":     "تأكيدات الاختبار من الطلاب",
    "difficulty":        "مواضيع صعبة",
    "professor_note":    "ملاحظة الدكتور",
    "resource_rec":      "مصدر موصى به",
    "confusion_cluster": "سؤال متكرر",
}

# ---------------------------------------------------------------------------
# Synthesis result cache
#
# During exam season, 60-80% of queries are semantically identical
# (same intent + same course + same exam type from different students).
# Cache key: SHA-256(normalized_query | primary_course_code | exam_type)
# TTL: 2 hours — answers don't change hourly; covers a full exam prep session.
# Max 1,000 entries with LRU eviction — covers all realistic exam-season patterns.
#
# Cache hits: <200ms response instead of 5-8s synthesis — the quality argument
# is stronger than the cost argument at launch scale.
# ---------------------------------------------------------------------------
_SYNTHESIS_CACHE_TTL     = int(os.environ.get("SYNTHESIS_CACHE_TTL", "7200"))  # 2h default
_SYNTHESIS_CACHE_MAX     = int(os.environ.get("SYNTHESIS_CACHE_MAX", "1000"))
_synthesis_cache: OrderedDict[str, tuple[dict, float]] = OrderedDict()  # key → (payload, ts)


def _cache_key(query_normalized: str, course_code: str | None, exam_type: str | None) -> str:
    raw = f"{query_normalized.lower().strip()}|{course_code or ''}|{exam_type or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _cache_get(key: str) -> dict | None:
    entry = _synthesis_cache.get(key)
    if entry is None:
        return None
    payload, ts = entry
    if time.time() - ts > _SYNTHESIS_CACHE_TTL:
        _synthesis_cache.pop(key, None)
        return None
    # LRU: move to end on hit
    _synthesis_cache.move_to_end(key)
    return payload


def _cache_set(key: str, payload: dict) -> None:
    if key in _synthesis_cache:
        _synthesis_cache.move_to_end(key)
    _synthesis_cache[key] = (payload, time.time())
    while len(_synthesis_cache) > _SYNTHESIS_CACHE_MAX:
        _synthesis_cache.popitem(last=False)  # evict oldest

# Synthesis prompt — grounded academic companion
_SYNTHESIS_SYSTEM = """\
You are رمّان (Rummaan) — an intelligent academic companion for Saudi Electronic University students.

Each source chunk is tagged with an authority tier:
  [OFFICIAL]      — extracted from official university documents (study plans, regulations, course descriptions)
  [COMMUNITY]     — student-shared materials (exam archives, notes, group discussions)
  [INTELLIGENCE]  — extracted events and announcements from Telegram group messages (deadlines, exams, assignments)
  [CALENDAR]      — official SEU academic calendar dates

You may receive a system context block titled "سياق المادة والطالب". Use it to:
  - Set accurate expectations about what RUMMAN knows for this course (coverage level, content types).
  - Surface recurring exam topics (المواضيع المتكررة) as strong signals when answering exam-related queries.
  - Weight "تأكيدات الاختبار من الطلاب" (exam_emphasis) and "ملاحظة الدكتور" (professor_note) signals highly —
    they reflect what students actually reported as important from their group chats.
  - Use "مواضيع صعبة" (difficulty) signals to emphasize topics students commonly struggle with.
  - "سؤال متكرر" (confusion_cluster) signals flag concepts that repeatedly confuse students — address them directly and clearly when relevant.
  - "مصدر موصى به" (resource_rec) signals name study materials students in this course found helpful — mention them when the student is asking for resources or summaries.
  - Signals tagged "(الفصل الحالي)" are from the current semester and are more reliable than historic ones.
  - Enrollment "(مؤكد)" means the student explicitly registered their courses — use it to scope the answer.
  - Enrollment "(غير مؤكد)" is inferred from prior conversation, not confirmed — treat as a weak hint only; do not assume correctness.
  - Do NOT fabricate information from this block — it is meta-context, not source content.

Grounding rules:
- Use ONLY information present in the provided source chunks. Do not invent or extrapolate.
- Chunks may be in Arabic or English — understand both; respond in the student's language.
- When OFFICIAL and COMMUNITY sources agree: answer directly.
- When they differ or conflict: present the official position first, then note the community perspective.
- [INTELLIGENCE] items represent what instructors/students actually posted in groups — treat as reliable but community-sourced.
  If an [INTELLIGENCE] item gives a deadline or exam date, present it clearly with a note it came from a group announcement.
- [CALENDAR] items are the authoritative official SEU schedule — use them for semester dates.
- When chunks contain exam questions: identify the topics and concepts they test, present them clearly. Complete and valid — do not hedge.
- When chunks contain definitions, explanations, or course content: synthesize in your own words. Be the intelligent companion, not a copy-paste machine.
- When chunks partially answer the question: share what you found and be honest about the gap.
- When chunks are off-topic: respond in the student's language — Arabic: "ما لقيت إجابة واضحة في المواد المتاحة — جرّب تذكر رمز المادة أو اسأل بطريقة مختلفة." / English: "I couldn't find a clear answer in the available materials — try including the course code or rephrasing your question."

Style:
- Gulf Arabic (خليجي) for Arabic questions. Clear, natural English for English questions.
- Answer like the smartest student in the class explaining to a friend — direct, specific, practical.
- When you have enough material: give a complete, useful answer (150–300 words is normal; use what the question requires).
- When [INTELLIGENCE] items contain deadlines or announcements: surface them prominently near the top.
- Do NOT mention professor names or predict unreleased exam content.
- Do NOT add meta-commentary ("Based on the sources...", "According to the chunks...").
- Do NOT explain what you're doing — just answer.\
"""

_SYNTHESIS_USER = "Student question: {query}\n\nSource chunks:\n{chunks}"

_SYNTHESIS_TIMEOUT = 20.0  # seconds; gpt-4o with conversation history needs more headroom


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dicts()
    yield


app = FastAPI(title="RUMMAN Platform API", version="4.1", lifespan=lifespan)
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
    query:                str
    limit:                int             = Field(default=5, ge=1, le=20)
    session_id:           str | None      = None
    user_id:              str | None      = None
    conversation_history: list[dict] | None = None  # [{"role": "user"|"assistant", "content": str}]


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


class CourseInventoryRequest(BaseModel):
    codes: list[str] = []   # explicit course codes (e.g. ["IT362", "MGT311"])
    names: list[str] = []   # free-text course names to resolve via inst_courses


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
        if key not in seen or (row.get("similarity") or 0) > (seen[key].get("similarity") or 0):
            seen[key] = row
    deduped = sorted(seen.values(), key=lambda r: r.get("similarity") or 0, reverse=True)
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
            "filter_tenant":   SEU_TENANT_ID,
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
# Intelligence layer retrieval (community-sourced items: exams, deadlines, …)
# ---------------------------------------------------------------------------

_INTELLIGENCE_ITEM_LABELS = {
    # intelligence_items types (legacy)
    "exam":         "اختبار",
    "deadline":     "موعد تسليم",
    "assignment":   "واجب",
    "quiz":         "اختبار قصير",
    "announcement": "إعلان",
    "decision":     "قرار",
    "reminder":     "تذكير",
    "meeting":      "اجتماع",
    # extracted_items types (daily_brief)
    "task":         "مهمة",
    "risk":         "تحذير",
    "follow_up":    "متابعة",
}


def _format_intel_row(item: dict, origin: str) -> dict:
    """Shared formatter: convert an intelligence_items or extracted_items row into a retrieval row."""
    label   = _INTELLIGENCE_ITEM_LABELS.get(item.get("item_type", ""), item.get("item_type", ""))
    # intelligence_items uses title+description; extracted_items uses content directly
    content = item.get("content") or (
        (item.get("title") or "") +
        ((" — " + item["description"]) if item.get("description") else "")
    )
    due  = item.get("due_date")
    code = item.get("course_code") or ""
    # intelligence_items stores chat_name inside metadata JSON
    chat = item.get("chat_name") or (item.get("metadata") or {}).get("chat_name") or "مجموعة"

    lines = [f"[{label}] {content}"]
    if due:
        lines.append(f"الموعد: {due}")
    if code:
        lines.append(f"المادة: {code}")
    lines.append(f"المصدر: {chat}")

    return {
        "content":          "\n".join(lines),
        "course_code":      code or None,
        "source_type":      "telegram_export",
        "source_authority": "community",
        "authority_tier":   "community",
        "similarity":       min(float(item.get("confidence", 0.65)), 0.88),
        "metadata":         {"origin": origin},
    }


async def _retrieve_intelligence_items(
    http: httpx.AsyncClient,
    course_codes: list[str],
    item_types: list[str] | None = None,
    days_back: int = 60,
) -> list[dict]:
    """Query both extracted_items (daily_brief) and intelligence_items (worker) for
    recent community-sourced events. Returns results in match_documents row shape."""
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=days_back)).isoformat()

    # ── Query 1: extracted_items via active_extracted_items view ─────────────
    ext_params: list[tuple] = [
        ("tenant_id",  f"eq.{SEU_TENANT_ID}"),
        ("created_at", f"gte.{cutoff}"),
        ("confidence", "gte.0.65"),
        ("select",     "item_type,content,due_date,course_code,confidence,chat_name,created_at"),
        ("order",      "due_date.asc.nullslast"),
        ("limit",      "20"),
    ]
    if course_codes:
        ext_params.append(("course_code", f"in.({','.join(course_codes)})"))
    if item_types:
        ext_params.append(("item_type", f"in.({','.join(item_types)})"))

    # ── Query 2: intelligence_items (real-time worker output) ────────────────
    # Filter: no due_date (undated announcements always relevant) OR due_date >= today
    # (prevents stale past-deadline items surfacing — intelligence_items has no valid_until column)
    today = date.today().isoformat()
    int_params: list[tuple] = [
        ("tenant_id",  f"eq.{SEU_TENANT_ID}"),
        ("created_at", f"gte.{cutoff}"),
        ("confidence", "gte.0.65"),
        ("or",         f"(due_date.is.null,due_date.gte.{today})"),
        ("select",     "item_type,title,description,due_date,course_code,confidence,metadata,created_at"),
        ("order",      "due_date.asc.nullslast"),
        ("limit",      "20"),
    ]
    if course_codes:
        int_params.append(("course_code", f"in.({','.join(course_codes)})"))
    if item_types:
        int_params.append(("item_type", f"in.({','.join(item_types)})"))

    ext_resp, int_resp = await asyncio.gather(
        http.get(f"{SUPABASE_URL}/rest/v1/active_extracted_items", headers=HEADERS, params=ext_params),
        http.get(f"{SUPABASE_URL}/rest/v1/intelligence_items",     headers=HEADERS, params=int_params),
        return_exceptions=True,
    )

    results: list[dict] = []
    if not isinstance(ext_resp, Exception) and ext_resp.status_code == 200:
        for item in (ext_resp.json() or []):
            results.append(_format_intel_row(item, "extracted_items"))
    if not isinstance(int_resp, Exception) and int_resp.status_code == 200:
        for item in (int_resp.json() or []):
            results.append(_format_intel_row(item, "intelligence_items"))

    # Sort by similarity (proxy for confidence) descending, cap total
    results.sort(key=lambda r: r.get("similarity", 0), reverse=True)
    return results[:30]


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

    all_raw: list[dict] = []
    params_log: list[dict] = []

    # Deduplicate param_list before spawning tasks
    seen_params: set[tuple] = set()
    unique_params: list[SearchParams] = []
    for p in param_list:
        k = (p.query, p.course_code)
        if k not in seen_params:
            seen_params.add(k)
            unique_params.append(p)

    async with httpx.AsyncClient(timeout=30) as http:

        async def _embed_and_retrieve(params: SearchParams) -> tuple[list[dict], dict]:
            """Embed a query and retrieve matching chunks. Returns (rows_above_threshold, log)."""
            resp = await ai.embeddings.create(
                model=EMBED_MODEL, input=params.query, dimensions=EMBED_DIMS
            )
            embedding = resp.data[0].embedding
            fetch_count = min(params.limit * 3, 150)
            threshold  = MIN_SIMILARITY_COURSE if params.course_code else MIN_SIMILARITY
            raw = await _retrieve(http, embedding, params.course_code, params.source_type, fetch_count)
            rows = [r for r in raw if (r.get("similarity") or 0) >= threshold]
            log_entry = {
                "query":       params.query,
                "course_code": params.course_code,
                "source_type": params.source_type,
            }
            return rows, log_entry

        # Run all embed+retrieve pairs in parallel — reduces latency from O(N) to O(1)
        # for multi-course enrolled users (up to 6 pairs: 3 courses × 2 languages).
        gather_results = await asyncio.gather(
            *[_embed_and_retrieve(p) for p in unique_params],
            return_exceptions=True,
        )
        for result in gather_results:
            if isinstance(result, Exception):
                log.warning("embed_retrieve_failed | %s", result)
                continue
            rows, log_entry = result
            all_raw.extend(rows)
            params_log.append(log_entry)

        # Broad fallback: if a course-specific search returned almost nothing,
        # run one additional broad search (no course filter). This catches the
        # 84% of corpus chunks that lack a course_code assignment — once the
        # attribution worker closes that gap this branch will almost never fire.
        # Threshold raised to MIN_SIMILARITY to keep noise low.
        _had_course_intent = bool(understanding.intent and understanding.intent.course_codes)
        if _had_course_intent and len(all_raw) < 3:
            _fallback_query = (
                understanding.intent.normalized_text
                if understanding.intent and understanding.intent.normalized_text
                else understanding.query_normalized
            )
            try:
                _fb_rows, _fb_log = await _embed_and_retrieve(
                    SearchParams(query=_fallback_query, course_code=None, source_type=None, limit=limit)
                )
                if _fb_rows:
                    all_raw.extend(_fb_rows)
                    params_log.append({**_fb_log, "fallback": True})
                    log.info("broad_fallback_fired | course=%s | found=%d",
                             understanding.intent.course_codes[0] if understanding.intent else "?",
                             len(_fb_rows))
            except Exception as _fb_exc:
                log.warning("broad_fallback_error | %s", _fb_exc)

        # Inject structured course facts for any detected course codes (institutional layer)
        if understanding.intent and understanding.intent.course_codes:
            curriculum = await _retrieve_curriculum_facts(http, understanding.intent.course_codes)
            all_raw.extend(curriculum)

        # Inject calendar events for temporal intents — deterministic, not vector search
        if (understanding.intent and
                understanding.intent.intent_type in ("exam_schedule", "deadline")):
            calendar = await _retrieve_calendar_events(http)
            all_raw.extend(calendar)

        # Inject intelligence layer items (community Telegram signals from extracted_items)
        # Two separate triggers to avoid flooding broad queries with unrelated items:
        #   1. Course-specific: inject items for detected course codes (always)
        #   2. Temporal/operational: inject ALL items when intent is exam/deadline (no course filter)
        #      — covers "are finals online?", "when is the معادلة deadline?" without course codes
        # General queries without course codes get NO injection (avoids context pollution).
        intel_codes  = (understanding.intent.course_codes if understanding.intent else []) or []
        intel_intent = understanding.intent.intent_type if understanding.intent else ""
        if intel_codes:
            intel = await _retrieve_intelligence_items(http, course_codes=intel_codes)
            all_raw.extend(intel)
        elif intel_intent in ("exam_schedule", "deadline"):
            intel = await _retrieve_intelligence_items(http, course_codes=[])
            all_raw.extend(intel)

    results = _deduplicate(all_raw, limit)
    return results, all_raw, understanding, params_log


# ---------------------------------------------------------------------------
# Grounded synthesis
# ---------------------------------------------------------------------------

# Default: gpt-4o-mini for all synthesis (~75% cost reduction vs gpt-4o).
# Comparison and high-complexity queries are promoted to the premium model automatically.
# Override both with env vars on Railway if budget constraints change.
_SYNTHESIS_MODEL         = os.environ.get("SYNTHESIS_MODEL",         "gpt-4o-mini")
_SYNTHESIS_PREMIUM_MODEL = os.environ.get("SYNTHESIS_PREMIUM_MODEL", "gpt-4o")

# Intent types that warrant the premium model — require genuine cross-concept reasoning.
_PREMIUM_INTENTS = frozenset({"comparison"})


def _select_synthesis_model(intent_type: str | None, confidence: float | None) -> tuple[str, int]:
    """Return (model_name, max_tokens) for a given intent signal."""
    if intent_type in _PREMIUM_INTENTS:
        return _SYNTHESIS_PREMIUM_MODEL, 700
    if confidence is not None and confidence < 0.55:
        return _SYNTHESIS_PREMIUM_MODEL, 700
    if intent_type == "concept_explain":
        return _SYNTHESIS_MODEL, 600  # complex concepts need room for full definitions
    return _SYNTHESIS_MODEL, 400


async def _synthesize_answer(
    query: str,
    chunks: list[dict],
    conversation_history: list[dict] | None = None,
    model: str | None = None,
    max_tokens: int = 400,
    student_context_block: str | None = None,
) -> tuple[str, int]:
    """
    Synthesize a grounded answer from chunks.
    conversation_history: alternating user/assistant turns from the current session (last 3).
    student_context_block: optional context primer injected after the system prompt.
    Returns (answer_text, total_tokens_used).
    Raises asyncio.TimeoutError on timeout — caller handles fallback.
    """
    def _tier_label(row: dict) -> str:
        tier    = row.get("authority_tier") or ""
        origin  = (row.get("metadata") or {}).get("origin", "")
        if origin == "academic_calendar":
            return "[CALENDAR]"
        if origin in ("intelligence_items", "extracted_items"):
            return "[INTELLIGENCE]"
        if tier == "official" or "inst_courses" in origin:
            return "[OFFICIAL]"
        return "[COMMUNITY]"

    chunk_text = "\n\n---\n\n".join(
        f"{_tier_label(row)} [{i+1}] {(row.get('content') or '').strip()[:800]}"
        for i, row in enumerate(chunks[:8])
    )

    messages: list[dict] = [{"role": "system", "content": _SYNTHESIS_SYSTEM}]

    # Inject student context (enrolled courses, active focus) before conversation history.
    # Keeps the model grounded to what we know about this specific student.
    if student_context_block:
        messages.append({"role": "system", "content": student_context_block})

    # Inject last N turns so the model can resolve pronouns and follow-up questions.
    # Truncate each turn to 400 chars to keep context cost bounded.
    if conversation_history:
        for turn in conversation_history[-6:]:  # max 3 user+assistant pairs
            role    = turn.get("role", "user")
            content = (turn.get("content") or "")[:400]
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": _SYNTHESIS_USER.format(
        query=query, chunks=chunk_text
    )})

    resp = await asyncio.wait_for(
        ai.chat.completions.create(
            model=model or _SYNTHESIS_MODEL,
            messages=messages,
            temperature=0.1,
            max_tokens=max_tokens,
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
# Student context — read/write helpers
# ---------------------------------------------------------------------------

async def _fetch_student_context(http: httpx.AsyncClient, user_id: str) -> dict:
    """Return dict of context_type → row for non-expired context signals."""
    try:
        r = await http.get(
            f"{SUPABASE_URL}/rest/v1/student_context",
            headers=HEADERS,
            params={
                "user_id": f"eq.{user_id}",
                "select":  "context_type,context_value,confidence,source",
                "or":      "(expires_at.is.null,expires_at.gt.now())",
                "limit":   "10",
            },
            timeout=4,
        )
        if r.status_code == 200:
            return {row["context_type"]: row for row in r.json()}
    except Exception:
        pass
    return {}


async def _save_active_focus(
    user_id: str,
    course_code: str,
    exam_type: str | None,
) -> None:
    """Fire-and-forget: persist the course the student just queried about."""
    from datetime import datetime, timezone, timedelta
    try:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        async with httpx.AsyncClient(timeout=4) as http:
            await http.post(
                f"{SUPABASE_URL}/rest/v1/student_context",
                headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
                params={"on_conflict": "user_id,context_type"},
                json={
                    "user_id":        user_id,
                    "tenant_id":      SEU_TENANT_ID,
                    "context_type":   "active_focus",
                    "context_value":  {"course_code": course_code, "exam_type": exam_type},
                    "confidence":     "low",
                    "source":         "inferred",
                    "observed_count": 1,
                    "expires_at":     expires_at,
                },
            )
    except Exception:
        pass


def _build_context_block(
    ctx: dict,
    course_profile: dict | None = None,
    exam_signals: list[dict] | None = None,
    msg_signals: list[dict] | None = None,
) -> str | None:
    """Format student context + corpus intelligence into a system-prompt injection block."""
    parts: list[str] = []

    # Student signals
    enrolled_ctx = ctx.get("enrolled_courses") or {}
    enrolled = enrolled_ctx.get("context_value", {}).get("codes", [])
    if enrolled:
        confidence = enrolled_ctx.get("confidence", "low")
        if confidence == "high":
            parts.append(f"الطالب مسجل (مؤكد) في: {', '.join(enrolled)}")
        else:
            parts.append(f"الطالب يبدو مسجلاً (غير مؤكد) في: {', '.join(enrolled)} — لا تفترض هذه المواد بشكل قاطع")
    focus = (ctx.get("active_focus") or {}).get("context_value", {})
    if focus.get("course_code"):
        line = f"المادة الحالية: {focus['course_code']}"
        if focus.get("exam_type"):
            line += f" ({focus['exam_type']})"
        parts.append(line)

    # Corpus intelligence
    if course_profile:
        cov   = course_profile.get("coverage_level", "none")
        total = course_profile.get("total_chunks", 0)
        exam  = course_profile.get("exam_chunks", 0)
        flags = []
        if course_profile.get("has_exam_archives"):
            flags.append("أرشيف اختبارات")
        if course_profile.get("has_official_docs"):
            flags.append("وثائق رسمية")
        if course_profile.get("has_summaries"):
            flags.append("ملخصات")
        cov_label = {
            "strong":   "تغطية قوية",
            "moderate": "تغطية متوسطة",
            "thin":     "تغطية محدودة",
            "none":     "لا توجد بيانات",
        }.get(cov, cov)
        flag_str = " | ".join(flags) if flags else "لا يوجد محتوى مصنّف"
        parts.append(
            f"معرفة RUMMAN بالمادة: {cov_label} ({total} مقطع، منها {exam} من الاختبارات) — {flag_str}"
        )

    if exam_signals:
        for sig in exam_signals[:2]:
            topics = sig.get("top_topics") or []
            if topics:
                etype = sig.get("exam_type", "")
                label = {
                    "midterm": "الميدترم",
                    "final":   "الفاينل",
                    "quiz":    "الكويز",
                    "general": "الاختبارات",
                }.get(etype, "الاختبارات")
                parts.append(f"المواضيع المتكررة في {label}: {', '.join(topics[:6])}")

    # Message intelligence signals
    if msg_signals:
        # Current-semester signals first, then by source_count descending
        current  = [s for s in msg_signals if s.get("is_current_semester")]
        historic = [s for s in msg_signals if not s.get("is_current_semester")]
        for sig in (current + historic)[:3]:
            stype   = sig.get("signal_type", "")
            content = (sig.get("signal_content") or "").strip()[:150]
            if not content:
                continue
            label = _MSG_SIGNAL_LABELS.get(stype, stype)
            src   = sig.get("source_count", 1)
            semester_tag = " (الفصل الحالي)" if sig.get("is_current_semester") else ""
            parts.append(f"{label}{semester_tag} ({src} رسالة): {content}")

    if not parts:
        return None
    return "سياق المادة والطالب:\n" + "\n".join(parts)


async def _fetch_message_signals(
    http: httpx.AsyncClient,
    course_code: str,
    signal_types: list[str] | None = None,
) -> list[dict]:
    """Fetch top message signals for a course, current semester first."""
    try:
        params: dict = {
            "course_code": f"eq.{course_code}",
            "tenant_id":   f"eq.{SEU_TENANT_ID}",
            "confidence":  "gte.0.70",
            "select":      "signal_type,signal_content,source_count,is_current_semester,semester_hint",
            "order":       "is_current_semester.desc,source_count.desc",
            "limit":       "6",
        }
        if signal_types:
            params["signal_type"] = f"in.({','.join(signal_types)})"
        r = await http.get(
            f"{SUPABASE_URL}/rest/v1/message_signals",
            headers=HEADERS,
            params=params,
            timeout=4,
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return []


async def _fetch_course_intelligence(
    http: httpx.AsyncClient,
    course_code: str,
    exam_type: str | None = None,
) -> tuple[dict | None, list[dict]]:
    """Fetch course profile and exam signals for a course. Returns (profile, signals)."""
    try:
        r_profile = await http.get(
            f"{SUPABASE_URL}/rest/v1/course_intelligence_profiles",
            headers=HEADERS,
            params={
                "course_code": f"eq.{course_code}",
                "tenant_id":   f"eq.{SEU_TENANT_ID}",
                "select":      "coverage_level,total_chunks,exam_chunks,has_exam_archives,has_official_docs,has_summaries",
                "limit":       "1",
            },
            timeout=4,
        )
        profile = r_profile.json()[0] if r_profile.status_code == 200 and r_profile.json() else None
    except Exception:
        profile = None

    signals: list[dict] = []
    try:
        params: dict = {
            "course_code": f"eq.{course_code}",
            "tenant_id":   f"eq.{SEU_TENANT_ID}",
            "confidence":  "in.(medium,high)",   # low = too few chunks, attribution noise risk
            "select":      "exam_type,top_topics,confidence",
            "limit":       "4",
        }
        if exam_type:
            params["exam_type"] = f"eq.{exam_type}"
        r_sig = await http.get(
            f"{SUPABASE_URL}/rest/v1/exam_intelligence",
            headers=HEADERS,
            params=params,
            timeout=4,
        )
        if r_sig.status_code == 200:
            signals = r_sig.json()
    except Exception:
        pass

    return profile, signals


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
            except Exception as exc:
                log.warning("session_timestamp_parse_failed | session=%s | %s", s.get("id"), exc)

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
# Student context (persistent memory for enrolled courses and focus)
# ---------------------------------------------------------------------------

class StudentContextRequest(BaseModel):
    context_type:  str
    context_value: dict
    confidence:    str = "low"   # high | medium | low
    source:        str = "inferred"  # explicit | inferred | confirmed


@app.post("/v1/users/{user_id}/context")
async def upsert_user_context(user_id: str, req: StudentContextRequest):
    """
    Persist a student context signal. High-confidence writes come from explicit
    commands (/mycourses); low/medium come from behavioral inference.
    """
    from datetime import datetime, timezone, timedelta
    ttl_days: dict[str, int | None] = {"high": None, "medium": 30, "low": 7}
    ttl = ttl_days.get(req.confidence)
    expires_at = None
    if ttl:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=ttl)).isoformat()

    async with httpx.AsyncClient(timeout=10) as http:
        r = await http.post(
            f"{SUPABASE_URL}/rest/v1/student_context",
            headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": "user_id,context_type"},
            json={
                "user_id":        user_id,
                "tenant_id":      SEU_TENANT_ID,
                "context_type":   req.context_type,
                "context_value":  req.context_value,
                "confidence":     req.confidence,
                "source":         req.source,
                "observed_count": 1,
                "last_seen_at":   "now()",
                "expires_at":     expires_at,
            },
        )
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail="context write failed")
    return {"ok": True}


@app.get("/v1/users/{user_id}/context")
async def get_user_context(user_id: str):
    """Return all non-expired context signals for a user (keyed by context_type)."""
    async with httpx.AsyncClient(timeout=5) as http:
        ctx = await _fetch_student_context(http, user_id)
    return ctx


# ---------------------------------------------------------------------------
# Course inventory (used by planning handler in the bot)
# ---------------------------------------------------------------------------

_SOURCE_TYPE_LABELS = {
    "exam":               "تجميعات اختبارات",
    "study_plan":         "خطة دراسية",
    "course_description": "محتوى المادة",
    "upload":             "مواد طلابية",
    "regulation":         "لوائح",
    "telegram_export":    "مناقشات طلابية",
}


_NAME_STOPWORDS = {
    "introduction", "intro", "principles", "fundamentals", "advanced",
    "basic", "general", "special", "applied", "to", "of", "in", "and",
    "the", "a", "an", "for",
}


def _name_search_candidates(name: str) -> list[str]:
    """
    Return a ranked list of search strings for ILIKE matching.
    Handles British/American spelling variants and common abbreviations.
    """
    name_clean = name.strip()[:60]
    # British → American normalizations that matter in course names
    normalized = (name_clean
                  .replace("Behaviour", "Behavior").replace("behaviour", "behavior")
                  .replace("Organisation", "Organization").replace("organisation", "organization")
                  .replace("Organise", "Organize").replace("organise", "organize"))
    candidates = [name_clean]
    if normalized != name_clean:
        candidates.append(normalized)
    # Significant words (skip stopwords, 5+ chars), longest first — fallback for
    # cases like "Introduction to Operations Management" → "Operations"
    sig = sorted(
        [w for w in name_clean.lower().split()
         if w.rstrip(".,") not in _NAME_STOPWORDS and len(w) >= 5],
        key=len, reverse=True,
    )
    for word in sig[:3]:
        word_norm = (word.replace("behaviour", "behavior")
                        .replace("organisation", "organization"))
        for w in {word, word_norm}:
            if w not in candidates:
                candidates.append(w)
    return candidates


@app.post("/v1/courses/inventory")
async def course_inventory(req: CourseInventoryRequest):
    """
    Given course codes and/or free-text names, return what RUMMAN has for each.
    Names are fuzzy-matched against inst_courses (name_en / name_ar) with
    British/American spelling normalization and significant-word fallback.
    Used by the bot's planning handler to show students what content is available.
    """
    async with httpx.AsyncClient(timeout=15) as http:

        # ── Step 1: Resolve names → codes via inst_courses ────────────────────
        resolved: dict[str, str] = {}  # name → code
        for name in req.names[:8]:
            for search_term in _name_search_candidates(name):
                found = False
                for col in ("name_en", "name_ar"):
                    r = await http.get(
                        f"{SUPABASE_URL}/rest/v1/inst_courses",
                        headers=HEADERS,
                        params={
                            col:         f"ilike.*{search_term}*",
                            "tenant_id": f"eq.{SEU_TENANT_ID}",
                            "select":    "code,name_en,name_ar",
                            "limit":     "1",
                        },
                    )
                    if r.status_code == 200 and r.json():
                        resolved[name] = r.json()[0]["code"]
                        found = True
                        break
                if found:
                    break  # resolved — stop trying more search terms for this name

        # ── Step 2: Merge explicit codes + resolved names ─────────────────────
        all_codes: list[str] = list({c.upper() for c in req.codes[:8]})
        for code in resolved.values():
            if code and code.upper() not in all_codes:
                all_codes.append(code.upper())

        if not all_codes:
            return {
                "inventory":        {},
                "unresolved_names": req.names,
            }

        codes_param = ",".join(all_codes)

        # ── Step 3: Fetch course metadata (name, catalog presence) ────────────
        course_meta: dict[str, dict] = {}
        r = await http.get(
            f"{SUPABASE_URL}/rest/v1/inst_courses",
            headers=HEADERS,
            params={
                "code":      f"in.({codes_param})",
                "tenant_id": f"eq.{SEU_TENANT_ID}",
                "select":    "code,name_en,name_ar",
                "limit":     "20",
            },
        )
        if r.status_code == 200:
            for row in r.json():
                course_meta[row["code"]] = {
                    "name_en": row.get("name_en") or "",
                    "name_ar": row.get("name_ar") or "",
                }

        # ── Step 4: Fetch pre-computed profiles (faster than raw chunk scan) ────
        profile_data: dict[str, dict] = {}
        r = await http.get(
            f"{SUPABASE_URL}/rest/v1/course_intelligence_profiles",
            headers=HEADERS,
            params={
                "course_code": f"in.({codes_param})",
                "tenant_id":   f"eq.{SEU_TENANT_ID}",
                "select":      "course_code,total_chunks,exam_chunks,has_exam_archives,has_official_docs,has_summaries,coverage_level",
                "limit":       "20",
            },
        )
        if r.status_code == 200:
            for row in r.json():
                profile_data[row["course_code"]] = row

        # ── Step 5: Build inventory ───────────────────────────────────────────
        inventory: dict[str, dict] = {}
        for code in all_codes:
            meta    = course_meta.get(code, {})
            profile = profile_data.get(code, {})
            src_labels: list[str] = []
            src_types:  list[str] = []
            if profile.get("has_exam_archives"):
                src_labels.append(_SOURCE_TYPE_LABELS.get("exam", "تجميعات اختبارات"))
                src_types.append("exam")
            if profile.get("has_official_docs"):
                src_labels.append(_SOURCE_TYPE_LABELS.get("study_plan", "وثائق رسمية"))
                src_types.append("official")
            if profile.get("has_summaries"):
                src_labels.append(_SOURCE_TYPE_LABELS.get("upload", "ملخصات"))
                src_types.append("summary")
            inventory[code] = {
                "name_en":      meta.get("name_en", ""),
                "name_ar":      meta.get("name_ar", ""),
                "in_catalog":   code in course_meta,
                "chunk_count":  profile.get("total_chunks", 0),
                "exam_chunks":  profile.get("exam_chunks", 0),
                "coverage_level": profile.get("coverage_level", "none"),
                "source_types":  src_types,
                "source_labels": src_labels,
            }

        unresolved = [n for n in req.names if n not in resolved]
        return {"inventory": inventory, "unresolved_names": unresolved}


# ---------------------------------------------------------------------------
# Exam Bank — Domino 1
# ---------------------------------------------------------------------------

@app.get("/v1/exam-bank/{course_code}/recurring")
async def exam_bank_recurring(
    course_code: str,
    exam_type:   str | None = None,
    limit:       int = 15,
    user_id:     str | None = None,
):
    """
    Recurring topics for a course sorted by how many distinct exam years they appear in.
    Powers the /bank Telegram command and the Exam Bank screen.

    Returns topics from exam_questions.topic_tags grouped by year.
    Uses the get_recurring_topics RPC (migration 040).
    """
    course_code = course_code.upper().strip()
    if exam_type and exam_type not in ("midterm", "final", "quiz", "general"):
        raise HTTPException(status_code=400, detail="exam_type must be midterm|final|quiz|general")
    if not 1 <= limit <= 30:
        limit = 15

    async with httpx.AsyncClient(timeout=10) as http:
        rpc_resp = await http.post(
            f"{SUPABASE_URL}/rest/v1/rpc/get_recurring_topics",
            headers=HEADERS,
            json={
                "p_course_code": course_code,
                "p_tenant_id":   SEU_TENANT_ID,
                "p_exam_type":   exam_type,
                "p_limit":       limit,
            },
        )

    if rpc_resp.status_code != 200:
        log.error("exam_bank_rpc_error | course=%s | status=%d | %s",
                  course_code, rpc_resp.status_code, rpc_resp.text[:200])
        raise HTTPException(status_code=503, detail="exam bank temporarily unavailable")

    topics = rpc_resp.json() or []

    # Log student interaction (fire-and-forget) — foundation layer data collection
    if user_id and topics:
        asyncio.create_task(_log_exam_bank_view(user_id, course_code, exam_type))

    return {
        "course_code": course_code,
        "exam_type":   exam_type,
        "topic_count": len(topics),
        "topics":      topics,
    }


async def _log_exam_bank_view(user_id: str, course_code: str, exam_type: str | None):
    """Log student_interactions row for exam bank views — foundation layer."""
    async with httpx.AsyncClient(timeout=5) as http:
        await http.post(
            f"{SUPABASE_URL}/rest/v1/student_interactions",
            headers={**HEADERS, "Prefer": "return=minimal"},
            json={
                "tenant_id":    SEU_TENANT_ID,
                "user_id":      user_id,
                "interaction":  "viewed_recurring",
                "entity_type":  "course_bank",
                "course_code":  course_code,
                "exam_type":    exam_type,
            },
        )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "version": "4.2"}


@app.get("/cache/stats")
def cache_stats():
    """Synthesis cache diagnostics — monitor hit rates in production."""
    now = time.time()
    alive = sum(
        1 for _, (_, ts) in _synthesis_cache.items()
        if now - ts <= _SYNTHESIS_CACHE_TTL
    )
    return {
        "entries_total":   len(_synthesis_cache),
        "entries_alive":   alive,
        "entries_expired": len(_synthesis_cache) - alive,
        "capacity":        _SYNTHESIS_CACHE_MAX,
        "ttl_seconds":     _SYNTHESIS_CACHE_TTL,
    }


# ---------------------------------------------------------------------------
# Search (raw retrieval — for debugging, evaluation, and direct API consumers)
# ---------------------------------------------------------------------------

@app.post("/search")
async def search(req: SearchRequest):
    t_start = time.monotonic()

    try:
        results, all_raw, understanding, params_log = await _run_retrieval(
            req.query, req.limit, req.course_code, req.source_type
        )
    except Exception as exc:
        log.error("retrieval_error | %s | query=%.60s", exc, req.query)
        raise HTTPException(status_code=503, detail="retrieval temporarily unavailable")
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
    Retrieve relevant chunks, then synthesize a grounded answer.
    Model selection: gpt-4o-mini for factual/exam queries; gpt-4o for comparison/low-confidence.
    The synthesis prompt hard-constrains GPT to only use retrieved chunk content.
    Falls back to returning raw chunks if synthesis times out or fails.
    Supports conversation_history for multi-turn follow-up queries.
    """
    t_start = time.monotonic()

    try:
        results, all_raw, understanding, params_log = await _run_retrieval(
            req.query, req.limit
        )
    except Exception as exc:
        log.error("retrieval_error | %s | query=%.60s", exc, req.query)
        raise HTTPException(status_code=503, detail="retrieval temporarily unavailable")

    intent   = understanding.intent
    top_sim  = results[0].get("similarity") if results else None
    grounded = len(results) > 0
    answer: str | None = None
    synthesis_tokens = 0
    synthesis_failed = False
    cache_hit        = False

    # ---------------------------------------------------------------------------
    # Synthesis cache — check before any GPT call.
    # Keyed on (normalized_query, primary_course_code, exam_type) so semantically
    # identical exam-season queries hit the cache regardless of minor wording variation.
    # Not cached: zero-result, synthesis failures, queries with conversation history
    # (personalized multi-turn cannot be shared across users).
    # ---------------------------------------------------------------------------
    primary_course = (intent.course_codes[0] if intent and intent.course_codes else None)
    exam_type      = (intent.exam_type        if intent else None)
    c_key = _cache_key(
        understanding.query_normalized or req.query,
        primary_course,
        exam_type,
    )
    # Never cache: conversation turns (personalized), or queries with no course code.
    # Broad queries (primary_course=None) may yield answers scoped to an enrolled
    # user's courses — caching those would serve User A's personalized answer to User B.
    # Course-specific queries (primary_course=IT362) are safe to cache because the
    # course context dominates over any enrollment scoping.
    use_cache = grounded and not req.conversation_history and primary_course is not None

    if use_cache:
        cached = _cache_get(c_key)
        if cached is not None:
            latency = int((time.monotonic() - t_start) * 1000)
            log.info("CACHE_HIT | key=%s | latency_ms=%d", c_key[:8], latency)
            asyncio.create_task(_log_event(
                "synthesis",
                session_id=req.session_id,
                user_id=req.user_id,
                understanding=understanding,
                result_count=cached["source_count"],
                top_similarity=top_sim,
                grounded=True,
                latency_ms=latency,
                metadata={"cache_hit": True, "synthesis_model": "cached"},
            ))
            return {**cached, "latency_ms": latency, "cache_hit": True}

    # Fetch student context + course intelligence + message signals in parallel.
    # All reads are fire-and-forget-safe; silently skipped on timeout/error.
    # 5s budget: Railway→Supabase latency averages 100-200ms; 2s was too tight.
    student_context_block: str | None = None
    async with httpx.AsyncClient(timeout=5) as ctx_http:
        ctx: dict = {}
        course_profile: dict | None = None
        exam_signals: list[dict] = []
        msg_signals: list[dict] = []

        fetches = []
        fetch_keys = []
        if req.user_id:
            fetches.append(_fetch_student_context(ctx_http, req.user_id))
            fetch_keys.append("student_ctx")
        if primary_course:
            fetches.append(_fetch_course_intelligence(ctx_http, primary_course, exam_type))
            fetch_keys.append("course_intel")
            _intent_type = intent.intent_type if intent else None
            _msg_signal_types = {
                "resource":       ["resource_rec", "difficulty", "confusion_cluster"],
                "concept_explain":["difficulty", "confusion_cluster", "professor_note"],
                "exam_schedule":  ["professor_note", "exam_emphasis"],
            }.get(_intent_type, ["exam_emphasis", "difficulty", "professor_note", "confusion_cluster"])
            fetches.append(_fetch_message_signals(
                ctx_http, primary_course,
                signal_types=_msg_signal_types,
            ))
            fetch_keys.append("msg_signals")

        if fetches:
            results_ctx = await asyncio.gather(*fetches, return_exceptions=True)
            for key, result in zip(fetch_keys, results_ctx):
                if isinstance(result, Exception):
                    continue
                if key == "student_ctx":
                    ctx = result
                elif key == "course_intel":
                    course_profile, exam_signals = result
                elif key == "msg_signals":
                    msg_signals = result

    student_context_block = _build_context_block(ctx, course_profile, exam_signals, msg_signals)

    # Select model based on intent complexity — comparison/low-confidence get gpt-4o,
    # all other factual/exam queries get gpt-4o-mini (~75% cheaper).
    synth_model, synth_max_tokens = _select_synthesis_model(
        intent.intent_type if intent else None,
        intent.confidence  if intent else None,
    )

    if grounded:
        try:
            answer, synthesis_tokens = await _synthesize_answer(
                req.query, results, req.conversation_history,
                model=synth_model, max_tokens=synth_max_tokens,
                student_context_block=student_context_block,
            )
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

    # Save active focus (what course the student just asked about) as inferred context.
    # Fire-and-forget — never blocks the response.
    if req.user_id and grounded and intent and intent.course_codes:
        asyncio.create_task(_save_active_focus(
            req.user_id, intent.course_codes[0], intent.exam_type,
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
            "synthesis_model":    synth_model,
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

    response_payload = {
        "query":                req.query,
        "grounded":             grounded,
        "answer":               answer,
        "synthesis_failed":     synthesis_failed,
        "source_count":         len(results),
        "sources":              sources,
        "latency_ms":           latency,
        "cache_hit":            False,
        "fallback_chunks":      results if synthesis_failed else [],
        "course_coverage_level": course_profile.get("coverage_level") if course_profile else None,
    }

    # Store in cache if synthesis succeeded and query is cacheable.
    # Strip fallback_chunks from cached payload — they're only for failed synthesis.
    if use_cache and grounded and answer and not synthesis_failed:
        cacheable = {k: v for k, v in response_payload.items() if k != "fallback_chunks"}
        cacheable["fallback_chunks"] = []
        _cache_set(c_key, cacheable)
        log.info("CACHE_SET | key=%s | course=%s | exam=%s", c_key[:8], primary_course, exam_type)

    return response_payload


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


# ---------------------------------------------------------------------------
# Operations Cockpit — /ops/status + /ops
# ---------------------------------------------------------------------------

async def _safe_count(http: httpx.AsyncClient, url: str, params: dict) -> int:
    """GET a PostgREST endpoint and return the integer count from the
    Prefer:count=exact response header.  Returns -1 on any error."""
    try:
        r = await http.get(
            url,
            headers={**HEADERS, "Prefer": "count=exact"},
            params={**params, "limit": "1"},
            timeout=10,
        )
        if r.status_code >= 400:
            return -1
        cr = r.headers.get("content-range", "")  # e.g. "0-0/42"
        total_part = cr.split("/")[-1] if "/" in cr else ""
        return int(total_part) if total_part.lstrip("-").isdigit() else -1
    except Exception:
        return -1


async def _safe_json(http: httpx.AsyncClient, url: str, params: dict) -> list:
    """GET a PostgREST endpoint and return JSON list. Returns [] on error."""
    try:
        r = await http.get(url, headers=HEADERS, params=params, timeout=10)
        if r.status_code >= 400:
            return []
        return r.json() or []
    except Exception:
        return []


def _compute_academic_phase(events: list[dict]) -> dict:
    """Derive current academic phase from academic_calendar rows."""
    from datetime import date
    today = date.today()

    active_windows: list[str] = []
    active_labels: list[str] = []
    upcoming: list[dict] = []

    for ev in events:
        ev_type  = ev.get("event_type", "")
        label_ar = ev.get("event_name_ar") or ev_type
        start_s  = ev.get("start_date")
        end_s    = ev.get("end_date")
        if not start_s:
            continue
        try:
            start_d = date.fromisoformat(start_s)
            end_d   = date.fromisoformat(end_s) if end_s else start_d
        except ValueError:
            continue

        if start_d <= today <= end_d:
            active_windows.append(ev_type)
            active_labels.append(label_ar)
        elif start_d > today:
            days_away = (start_d - today).days
            upcoming.append({
                "event_type": ev_type,
                "label_ar":   label_ar,
                "start_date": start_s,
                "days_away":  days_away,
            })

    upcoming.sort(key=lambda x: x["days_away"])
    upcoming = upcoming[:3]

    # Determine phase
    if any(w in active_windows for w in ("final_exam", "midterm_exam")):
        phase = "exam"
    elif any(w in active_windows for w in ("pre_exam_review", "exam_preparation")):
        phase = "pre_exam"
    elif any(w in active_windows for w in ("registration", "add_drop", "course_registration")):
        phase = "registration"
    elif any(w in active_windows for w in ("grade_release", "result_announcement")):
        phase = "grade_release"
    elif any(w in active_windows for w in ("semester_break", "vacation", "holiday")):
        phase = "break"
    elif active_windows:
        phase = "regular"
    else:
        # Infer from nearest upcoming event
        if upcoming:
            nxt = upcoming[0]["event_type"]
            if "exam" in nxt:
                phase = "pre_exam"
            else:
                phase = "regular"
        else:
            phase = "regular"

    return {
        "phase":             phase,
        "active_windows":    active_windows,
        "active_labels_ar":  active_labels,
        "upcoming":          upcoming,
    }


def _build_recommended_actions(
    backfill_failed: int,
    backfill_pending: int,
    needs_review: int,
    needs_official_review: int,
    embed_pending: int,
    upcoming: list[dict],
) -> list[dict]:
    actions: list[dict] = []
    priority = 1

    if backfill_failed > 0:
        actions.append({
            "priority": priority,
            "action":   f"راوي لم ينضم لـ {backfill_failed} قناة بعد",
            "detail":   "أضف راوي لهذه القنوات ثم أعد تشغيل الاستيراد",
        })
        priority += 1

    if needs_official_review > 0:
        actions.append({
            "priority": priority,
            "action":   f"{needs_official_review} إجابة في الدليل تحتاج تحقق رسمي",
            "detail":   "راجع دليل الإجابات الآنية وقارن مع اللوائح الرسمية",
        })
        priority += 1

    if needs_review > 0:
        actions.append({
            "priority": priority,
            "action":   f"{needs_review} إجابة تحتاج مراجعة قبل النشر للطلاب",
            "detail":   "افتح دليل الإجابات وراجع الإجابات المعلّقة",
        })
        priority += 1

    for ev in upcoming[:1]:
        if "exam" in ev.get("event_type", "") and ev.get("days_away", 99) <= 14:
            actions.append({
                "priority": priority,
                "action":   f"اختبار قادم خلال {ev['days_away']} يوم — تحقق من تغطية الإشارات",
                "detail":   f"{ev['label_ar']} يبدأ {ev['start_date']}",
            })
            priority += 1

    if embed_pending > 50:
        actions.append({
            "priority": priority,
            "action":   f"{embed_pending} chunk(s) في انتظار التضمين (embedding)",
            "detail":   "تأكد أن embed worker يعمل على Railway",
        })
        priority += 1

    if backfill_pending > 0 and backfill_failed == 0:
        actions.append({
            "priority": priority,
            "action":   f"{backfill_pending} backfill job(s) معلقة",
            "detail":   "راوي يعمل على المعالجة — لا إجراء مطلوب الآن",
        })

    return actions[:5]


# ── College-coverage helpers (module-level to avoid re-creation per request) ──

# College mapping sourced from inst_colleges + inst_specializations in DB.
# SEU has exactly 5 colleges — no engineering college.
# ENGT = اللغة الإنجليزية والترجمة (under THEO), NOT engineering.
_COLLEGE_PREFIX: dict[str, tuple[str, str]] = {
    # COMP — كلية الحوسبة والمعلوماتية
    "IT":   ("COMP",    "كلية الحوسبة والمعلوماتية"),
    "CS":   ("COMP",    "كلية الحوسبة والمعلوماتية"),
    "IS":   ("COMP",    "كلية الحوسبة والمعلوماتية"),
    "CIS":  ("COMP",    "كلية الحوسبة والمعلوماتية"),
    "DS":   ("COMP",    "كلية الحوسبة والمعلوماتية"),
    "MCS":  ("COMP",    "كلية الحوسبة والمعلوماتية"),
    # ADMIN — كلية العلوم الإدارية والمالية
    "MGT":  ("ADMIN",   "كلية العلوم الإدارية والمالية"),
    "FIN":  ("ADMIN",   "كلية العلوم الإدارية والمالية"),
    "ACC":  ("ADMIN",   "كلية العلوم الإدارية والمالية"),
    "ACCT": ("ADMIN",   "كلية العلوم الإدارية والمالية"),
    "ECOM": ("ADMIN",   "كلية العلوم الإدارية والمالية"),
    "ECON": ("ADMIN",   "كلية العلوم الإدارية والمالية"),
    "HRM":  ("ADMIN",   "كلية العلوم الإدارية والمالية"),
    "MKT":  ("ADMIN",   "كلية العلوم الإدارية والمالية"),
    "BUS":  ("ADMIN",   "كلية العلوم الإدارية والمالية"),
    # HEALTH — كلية العلوم الصحية
    "HLTH": ("HEALTH",  "كلية العلوم الصحية"),
    "NUR":  ("HEALTH",  "كلية العلوم الصحية"),
    "HSA":  ("HEALTH",  "كلية العلوم الصحية"),
    "PHR":  ("HEALTH",  "كلية العلوم الصحية"),
    "HCI":  ("HEALTH",  "كلية العلوم الصحية"),
    "PH":   ("HEALTH",  "كلية العلوم الصحية"),
    # THEO — كلية العلوم والدراسات النظرية
    # (LAW = القانون, ENGT = اللغة الإنجليزية والترجمة, DM = الإعلام الإلكتروني)
    "LAW":  ("THEO",    "كلية العلوم والدراسات النظرية"),
    "LAG":  ("THEO",    "كلية العلوم والدراسات النظرية"),
    "DM":   ("THEO",    "كلية العلوم والدراسات النظرية"),
    "ENGT": ("THEO",    "كلية العلوم والدراسات النظرية"),
    "ISLM": ("THEO",    "كلية العلوم والدراسات النظرية"),
    "ARB":  ("THEO",    "كلية العلوم والدراسات النظرية"),
    # GENERAL — مواد مشتركة
    "ENG":  ("GENERAL", "مواد مشتركة"),
    "STAT": ("GENERAL", "مواد مشتركة"),
    "MATH": ("GENERAL", "مواد مشتركة"),
    "GEN":  ("GENERAL", "مواد مشتركة"),
    "SCI":  ("GENERAL", "مواد مشتركة"),
    "PHYS": ("GENERAL", "مواد مشتركة"),
}
COLLEGE_ORDER = ["COMP", "ADMIN", "HEALTH", "THEO", "GENERAL"]
_COLLEGE_LABELS = {
    "COMP":    "كلية الحوسبة والمعلوماتية",
    "ADMIN":   "كلية العلوم الإدارية والمالية",
    "HEALTH":  "كلية العلوم الصحية",
    "THEO":    "كلية العلوم والدراسات النظرية",
    "GENERAL": "مواد مشتركة",
}


def _code_to_college(code: str):
    """Map a course code to its (college_key, name_ar) tuple, or None."""
    if not code or code == "UNKNOWN":
        return None
    for prefix in sorted(_COLLEGE_PREFIX.keys(), key=len, reverse=True):
        if code.startswith(prefix):
            return _COLLEGE_PREFIX[prefix]
    return None


# ── Founder layer: project-level asset computation ───────────────────────────

def _compute_assets(
    messages_total: int,
    channels_total: int,
    backfill_failed: int,
    exam_questions: int,
    qe_pending: int,
    source_docs: int,
    qa_active: int,
    cal_events_count: int,
) -> dict:
    """Qualitative state for each RUMMAN asset — founder perspective."""
    # Community
    if messages_total >= 1_000_000:
        msg_label = f"{messages_total / 1_000_000:.1f}M رسالة"
    elif messages_total >= 1_000:
        msg_label = f"{messages_total // 1000}K رسالة"
    else:
        msg_label = f"{messages_total} رسالة"

    if messages_total >= 1_000_000 and backfill_failed < 5:
        comm_state, comm_benefit = "قوي", "نعم"
    elif messages_total >= 500_000:
        comm_state, comm_benefit = "متوسط", "جزئياً"
    else:
        comm_state, comm_benefit = "مبكر", "جزئياً"
    if backfill_failed > 0:
        comm_benefit = "جزئياً"

    # Exams
    if exam_questions >= 15_000 and qe_pending < 100:
        exam_state, exam_benefit = "قوي", "نعم"
    elif exam_questions >= 5_000:
        exam_state, exam_benefit = "متوسط", "جزئياً"
    else:
        exam_state, exam_benefit = "مبكر", "جزئياً"

    # Documents
    if source_docs >= 100:
        docs_state, docs_benefit = "متوسط", "جزئياً"
    elif source_docs >= 20:
        docs_state, docs_benefit = "مبكر", "جزئياً"
    else:
        docs_state, docs_benefit = "ضعيف", "لا"

    # Operational Knowledge
    if qa_active >= 20:
        opk_state, opk_benefit = "متوسط", "جزئياً"
    elif qa_active > 0:
        opk_state, opk_benefit = "ضعيف", "جزئياً"
    else:
        opk_state, opk_benefit = "ضعيف", "لا"

    # Academic Context
    if cal_events_count > 0:
        ac_state, ac_benefit = "قوي", "نعم"
    else:
        ac_state, ac_benefit = "مبكر", "لا"

    return {
        "community": {
            "label": "المجتمع",
            "desc":  f"{msg_label} · {channels_total} قناة",
            "state": comm_state,
            "benefit": comm_benefit,
            "note": f"{backfill_failed} قناة لم تُستورد بعد" if backfill_failed > 0 else None,
        },
        "exams": {
            "label": "أرشيف الاختبارات",
            "desc":  f"{exam_questions:,} سؤال من 5 كليات",
            "state": exam_state,
            "benefit": exam_benefit,
            "note": f"{qe_pending:,} ملف لم يُستخرج بعد" if qe_pending > 0 else None,
        },
        "documents": {
            "label": "الوثائق الرسمية",
            "desc":  f"{source_docs:,} مصدر مُفهرس",
            "state": docs_state,
            "benefit": docs_benefit,
            "note": "93 وثيقة رسمية في انتظار الرفع" if source_docs < 80 else None,
        },
        "operational_knowledge": {
            "label": "المعرفة التشغيلية",
            "desc":  f"{qa_active} إجابة جاهزة للطلاب" if qa_active > 0 else "لا إجابات نشطة بعد",
            "state": opk_state,
            "benefit": opk_benefit,
            "note": "البنية التحتية جاهزة — تحتاج إدخال محتوى" if qa_active == 0 else None,
        },
        "academic_context": {
            "label": "السياق الأكاديمي",
            "desc":  "التقويم الأكاديمي نشط" if cal_events_count > 0 else "لم يُضف التقويم بعد",
            "state": ac_state,
            "benefit": ac_benefit,
            "note": None,
        },
    }


def _derive_launch_readiness(assets: dict) -> str:
    scores = {"نعم": 2, "جزئياً": 1, "لا": 0}
    total = sum(scores.get(a["benefit"], 0) for a in assets.values())
    ratio = total / (len(assets) * 2)
    if ratio >= 0.70:
        return "مرتفعة"
    if ratio >= 0.40:
        return "متوسطة"
    return "منخفضة"


def _derive_main_blocker(assets: dict, backfill_failed: int, qe_pending: int, qa_active: int):
    if assets.get("operational_knowledge", {}).get("benefit") == "لا":
        return "المعرفة التشغيلية لم تُفعَّل للطلاب بعد"
    if backfill_failed > 10:
        return f"راوي لم يكمل استيراد {backfill_failed} قناة"
    if qe_pending > 500:
        return f"{qe_pending:,} ملف اختبار في انتظار الاستخراج"
    if assets.get("documents", {}).get("benefit") == "لا":
        return "الوثائق الرسمية لم تُرفع بعد"
    return None


def _derive_strategic_risks(backfill_failed: int, qe_completed: int) -> list:
    risks = []
    if backfill_failed > 10:
        risks.append({"name": "جلسة راوي", "state": "خطر",
                      "detail": f"متعثرة — {backfill_failed} قناة لم تُستورد",
                      "impact": "يوقف استيراد التاريخ كاملاً"})
    else:
        risks.append({"name": "جلسات تيليجرام", "state": "سليم",
                      "detail": "غيث يستمع · راوي يستورد",
                      "impact": "يوقف الاستماع والاستيراد"})
    risks.append({"name": "Supabase", "state": "سليم",
                  "detail": "قاعدة البيانات تستجيب",
                  "impact": "يوقف كل شيء"})
    risks.append({"name": "OpenAI", "state": "سليم" if qe_completed > 0 else "غير محدد",
                  "detail": "الاستخراج والبوت يعملان" if qe_completed > 0 else "لا نشاط استخراج مرصود",
                  "impact": "يوقف البوت والاستخراج"})
    risks.append({"name": "Railway", "state": "يعمل",
                  "detail": "الخدمة نشطة وتستجيب",
                  "impact": "يوقف كل الخدمات"})
    return risks


@app.get("/ops/status")
async def ops_status():
    """Operations status snapshot — all Supabase counts gathered in parallel."""
    from datetime import datetime, timezone, timedelta

    base = f"{SUPABASE_URL}/rest/v1"
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    async with httpx.AsyncClient(timeout=10) as http:

        # ── All parallel queries ──────────────────────────────────────────────
        (
            backfill_pending,
            backfill_running,
            backfill_failed,
            qe_pending,
            source_docs_total,
            embed_pending,
            intel_total,
            exam_questions_total,
            doc_chunks_total,
            kg_topics_total,
            cal_events,
            coverage_rows,
            recent_signals,
            course_rows,
            sync_state_rows,
        ) = await asyncio.gather(
            _safe_count(http, f"{base}/telegram_backfill_jobs",
                        {"status": "eq.pending", "select": "id"}),
            _safe_count(http, f"{base}/telegram_backfill_jobs",
                        {"status": "eq.running", "select": "id"}),
            _safe_count(http, f"{base}/telegram_backfill_jobs",
                        {"status": "eq.failed",
                         "error_message": "not.like.PERMANENTLY_SKIPPED*",
                         "select": "id"}),
            _safe_count(http, f"{base}/source_documents",
                        {"question_extraction_status": "eq.pending", "select": "id"}),
            _safe_count(http, f"{base}/source_documents",
                        {"select": "id"}),
            _safe_count(http, f"{base}/processing_jobs",
                        {"job_type": "eq.embed_chunk", "status": "eq.pending", "select": "id"}),
            _safe_count(http, f"{base}/intelligence_items",
                        {"tenant_id": f"eq.{SEU_TENANT_ID}", "select": "id"}),
            _safe_count(http, f"{base}/exam_questions",
                        {"tenant_id": f"eq.{SEU_TENANT_ID}", "select": "id"}),
            _safe_count(http, f"{base}/document_chunks",
                        {"tenant_id": f"eq.{SEU_TENANT_ID}", "select": "id"}),
            _safe_count(http, f"{base}/kg_topics",
                        {"tenant_id": f"eq.{SEU_TENANT_ID}", "select": "id"}),
            _safe_json(http, f"{base}/academic_calendar", {
                "tenant_id": f"eq.{SEU_TENANT_ID}",
                "select":    "event_type,event_name_ar,start_date,end_date",
                "order":     "start_date.asc",
            }),
            # Exam bank coverage (for college breakdown)
            _safe_json(http, f"{base}/exam_bank_coverage", {
                "select": "course_code,coverage_score,is_exam_bank_ready",
                "limit":  "500",
            }),
            # Recent signals (for student pulse — last 7 days)
            _safe_json(http, f"{base}/telegram_signals", {
                "select":     "course_code,signal_type",
                "created_at": f"gte.{week_ago}",
                "limit":      "1000",
            }),
            # Course names for labelling pulse results
            _safe_json(http, f"{base}/inst_courses", {
                "select": "code,name_ar,name_en",
                "limit":  "500",
            }),
            # Channels + message totals: sum total_messages_seen across ~65 rows
            # (avoids slow COUNT(*) on the 2M+ row messages table)
            _safe_json(http, f"{base}/telegram_sync_state",
                       {"select": "total_messages_seen"}),
            return_exceptions=False,
        )

        # ── Messages + channels from sync state ──────────────────────────────
        _ss = sync_state_rows if isinstance(sync_state_rows, list) else []
        messages_total = sum((r.get("total_messages_seen") or 0) for r in _ss)
        channels_total = len(_ss)

        # ── telegram_signals (may not exist) ─────────────────────────────────
        signals_total = 0
        signals_last  = None
        try:
            sig_rows = await _safe_json(http, f"{base}/telegram_signals", {
                "tenant_id": f"eq.{SEU_TENANT_ID}",
                "select":    "id,created_at",
                "order":     "created_at.desc",
                "limit":     "1",
            })
            sig_count = await _safe_count(http, f"{base}/telegram_signals",
                                          {"tenant_id": f"eq.{SEU_TENANT_ID}", "select": "id"})
            signals_total = sig_count if sig_count >= 0 else 0
            signals_last  = sig_rows[0].get("created_at") if sig_rows else None
        except Exception:
            pass

        # ── media pending (telegram_media jobs) ──────────────────────────────
        media_pending = await _safe_count(http, f"{base}/processing_jobs", {
            "job_type": "eq.telegram_media",
            "status":   "eq.pending",
            "select":   "id",
        })

        # ── community_qa (may not exist — migration 046) ─────────────────────
        qa_total = qa_active = qa_draft = qa_needs_review = qa_needs_official = 0
        try:
            qa_total          = await _safe_count(http, f"{base}/community_qa",
                                                  {"tenant_id": f"eq.{SEU_TENANT_ID}", "select": "id"})
            qa_active         = await _safe_count(http, f"{base}/community_qa",
                                                  {"tenant_id": f"eq.{SEU_TENANT_ID}",
                                                   "lifecycle_status": "eq.active", "select": "id"})
            qa_draft          = await _safe_count(http, f"{base}/community_qa",
                                                  {"tenant_id": f"eq.{SEU_TENANT_ID}",
                                                   "lifecycle_status": "eq.draft", "select": "id"})
            qa_needs_review   = await _safe_count(http, f"{base}/community_qa",
                                                  {"tenant_id": f"eq.{SEU_TENANT_ID}",
                                                   "lifecycle_status": "eq.needs_review", "select": "id"})
            qa_needs_official = await _safe_count(http, f"{base}/community_qa",
                                                  {"tenant_id": f"eq.{SEU_TENANT_ID}",
                                                   "needs_official_review": "eq.true", "select": "id"})
            if qa_total < 0:
                qa_total = qa_active = qa_draft = qa_needs_review = qa_needs_official = 0
        except Exception:
            pass

    # ── College coverage ─────────────────────────────────────────────────────
    college_buckets: dict[str, list] = {k: [] for k in COLLEGE_ORDER}
    for row in (coverage_rows if isinstance(coverage_rows, list) else []):
        result = _code_to_college(row.get("course_code", ""))
        if result:
            college_buckets[result[0]].append(row)

    college_coverage = []
    for col_key in COLLEGE_ORDER:
        courses = college_buckets[col_key]
        total = len(courses)
        ready = sum(1 for c in courses if c.get("is_exam_bank_ready"))
        pct = round(ready / total * 100) if total > 0 else 0
        college_coverage.append({
            "key":      col_key,
            "name_ar":  _COLLEGE_LABELS[col_key],
            "pct":      pct,
            "ready":    ready,
            "total":    total,
            "has_data": total > 0,
        })

    # ── Course name lookup (from inst_courses) ───────────────────────────────
    import collections as _collections
    _course_name: dict[str, str] = {}
    for c in (course_rows if isinstance(course_rows, list) else []):
        code = c.get("code", "")
        name = c.get("name_ar") or c.get("name_en") or ""
        if code and name:
            _course_name[code] = name

    # ── Student pulse (signals last 7 days) ──────────────────────────────────
    course_counter = _collections.Counter(
        s.get("course_code") for s in (recent_signals if isinstance(recent_signals, list) else [])
        if s.get("course_code")
    )
    student_pulse = [
        {
            "course_code":  cc,
            "course_name":  _course_name.get(cc, ""),
            "signal_count": cnt,
        }
        for cc, cnt in course_counter.most_common(5)
    ]

    # ── Academic phase ────────────────────────────────────────────────────────
    academic = _compute_academic_phase(cal_events if isinstance(cal_events, list) else [])

    # ── Recommended actions ───────────────────────────────────────────────────
    actions = _build_recommended_actions(
        backfill_failed=max(backfill_failed, 0),
        backfill_pending=max(backfill_pending, 0),
        needs_review=max(qa_needs_review, 0),
        needs_official_review=max(qa_needs_official, 0),
        embed_pending=max(embed_pending, 0),
        upcoming=academic["upcoming"],
    )

    qe_completed = (source_docs_total - qe_pending) if source_docs_total >= 0 and qe_pending >= 0 else -1

    # ── Founder layer ─────────────────────────────────────────────────────────
    _msgs   = max(messages_total,  0) if messages_total  >= 0 else 0
    _chans  = max(channels_total,  0) if channels_total  >= 0 else 0
    _qs     = max(exam_questions_total, 0) if exam_questions_total >= 0 else 0
    _qep    = max(qe_pending,      0) if qe_pending      >= 0 else 0
    _sdocs  = max(source_docs_total, 0) if source_docs_total >= 0 else 0
    _qecomp = max(qe_completed,    0) if qe_completed    >= 0 else 0

    founder_assets = _compute_assets(
        messages_total=_msgs,
        channels_total=_chans,
        backfill_failed=max(backfill_failed, 0),
        exam_questions=_qs,
        qe_pending=_qep,
        source_docs=_sdocs,
        qa_active=max(qa_active, 0),
        cal_events_count=len(cal_events) if isinstance(cal_events, list) else 0,
    )
    launch_readiness = _derive_launch_readiness(founder_assets)
    main_blocker     = _derive_main_blocker(
        founder_assets,
        backfill_failed=max(backfill_failed, 0),
        qe_pending=_qep,
        qa_active=max(qa_active, 0),
    )
    strategic_risks  = _derive_strategic_risks(
        backfill_failed=max(backfill_failed, 0),
        qe_completed=_qecomp,
    )

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "academic": academic,
        # ── Founder layer ────────────────────────────────────────────────────
        "assets":           founder_assets,
        "launch_readiness": launch_readiness,
        "main_blocker":     main_blocker,
        "strategic_risks":  strategic_risks,
        # ── System layer ─────────────────────────────────────────────────────
        "services": {
            "backfill": {
                "total_pending": max(backfill_pending, 0) if backfill_pending >= 0 else -1,
                "total_running": max(backfill_running, 0) if backfill_running >= 0 else -1,
                "total_failed":  max(backfill_failed,  0) if backfill_failed  >= 0 else -1,
            },
            "question_extraction": {
                "total_completed": max(qe_completed, 0) if qe_completed >= 0 else -1,
                "total_pending":   max(qe_pending, 0)   if qe_pending   >= 0 else -1,
            },
            "embed": {
                "total_pending": max(embed_pending, 0) if embed_pending >= 0 else -1,
            },
            "signals": {
                "total":           signals_total,
                "last_stored_at":  signals_last,
            },
            "intelligence": {
                "total": max(intel_total, 0) if intel_total >= 0 else -1,
            },
            "media": {
                "disabled":      False,
                "total_pending": max(media_pending, 0) if media_pending >= 0 else -1,
            },
        },
        "knowledge": {
            "messages_total":    _msgs,
            "channels_total":    _chans,
            "exam_questions":    max(exam_questions_total, 0) if exam_questions_total >= 0 else -1,
            "source_documents":  max(source_docs_total,    0) if source_docs_total    >= 0 else -1,
            "document_chunks":   max(doc_chunks_total,     0) if doc_chunks_total     >= 0 else -1,
            "kg_topics":         max(kg_topics_total,      0) if kg_topics_total      >= 0 else -1,
            "telegram_signals":  signals_total,
        },
        "operational_qa": {
            "total":                  max(qa_total,          0) if qa_total          >= 0 else -1,
            "active":                 max(qa_active,         0) if qa_active         >= 0 else -1,
            "draft":                  max(qa_draft,          0) if qa_draft          >= 0 else -1,
            "needs_review":           max(qa_needs_review,   0) if qa_needs_review   >= 0 else -1,
            "needs_official_review":  max(qa_needs_official, 0) if qa_needs_official >= 0 else -1,
        },
        "recommended_actions": actions,
        "college_coverage": college_coverage,
        "student_pulse": student_pulse,
    }


@app.get("/ops")
async def ops_cockpit():
    """RUMMAN Founder Command Center — project-layer view."""
    from fastapi.responses import HTMLResponse
    html = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>رمان — Command Center</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,sans-serif;background:#09090f;color:#e2e8f0;min-height:100vh;padding:16px;max-width:800px;margin:0 auto}
.hdr{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:18px}
.hdr-title{font-size:1.5rem;font-weight:800;color:#f8fafc;letter-spacing:-.02em}
.hdr-sub{font-size:.7rem;color:#334155;margin-top:2px}
.hdr-meta{text-align:left;display:flex;flex-direction:column;align-items:flex-end;gap:4px}
.ts-lbl{font-size:.72rem;color:#475569}
.phase-chip{display:inline-block;font-size:.7rem;padding:2px 8px;border-radius:8px;font-weight:600}
.verdict{background:#111827;border:1px solid #1e293b;border-radius:12px;padding:16px 18px;margin-bottom:20px}
.verdict-top{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:12px}
.rd-label{font-size:.68rem;color:#334155;font-weight:600;text-transform:uppercase;letter-spacing:.08em;margin-bottom:3px}
.rd-val{font-size:1.45rem;font-weight:800;letter-spacing:-.02em;line-height:1}
.blocker{display:flex;align-items:flex-start;gap:8px;background:#140d00;border:1px solid rgba(251,191,36,.18);border-radius:8px;padding:10px 12px;font-size:.83rem;color:#fbbf24;line-height:1.4}
.ok-box{background:#030f07;border:1px solid rgba(34,197,94,.18);border-radius:8px;padding:10px 12px;font-size:.83rem;color:#4ade80}
.sec{font-size:.68rem;font-weight:700;color:#334155;text-transform:uppercase;letter-spacing:.1em;margin:22px 0 10px}
.assets{display:grid;grid-template-columns:1fr 1fr;gap:10px}
@media(max-width:480px){.assets{grid-template-columns:1fr}}
.acard{background:#111827;border:1px solid #1e293b;border-radius:10px;padding:13px 14px}
.acard-top{display:flex;align-items:center;justify-content:space-between;margin-bottom:7px}
.aname{font-size:.86rem;font-weight:700;color:#f1f5f9}
.pill{font-size:.67rem;font-weight:700;padding:2px 7px;border-radius:5px}
.adesc{font-size:.79rem;color:#94a3b8;margin-bottom:6px;line-height:1.4}
.brow{display:flex;align-items:center;gap:5px;font-size:.74rem}
.bdot{width:6px;height:6px;border-radius:50%;flex-shrink:0}
.anote{font-size:.69rem;color:#475569;margin-top:6px;padding-top:6px;border-top:1px solid #1e293b;line-height:1.4}
.delta-card{background:#111827;border:1px solid #1e293b;border-radius:10px;padding:14px 16px}
.di-row{display:flex;gap:20px;flex-wrap:wrap;margin-bottom:5px}
.di{display:flex;flex-direction:column;gap:1px}
.di-num{font-size:1.1rem;font-weight:700;line-height:1.2}
.di-lbl{font-size:.69rem;color:#475569}
.di-since{font-size:.7rem;color:#334155}
.risks{display:flex;flex-direction:column;gap:8px}
.rrow{background:#111827;border:1px solid #1e293b;border-radius:8px;padding:12px 14px;display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
.rinfo{flex:1;min-width:0}
.rname{font-size:.85rem;font-weight:700;color:#f1f5f9;margin-bottom:2px}
.rdetail{font-size:.74rem;color:#64748b}
.rimpact{font-size:.69rem;color:#334155;margin-top:3px}
.rbadge{flex-shrink:0;font-size:.7rem;font-weight:700;padding:3px 9px;border-radius:6px;white-space:nowrap;align-self:flex-start;margin-top:1px}
.tgl{width:100%;text-align:center;padding:11px;background:#111827;border:1px solid #1e293b;border-radius:8px;color:#334155;font-size:.77rem;cursor:pointer;margin-top:22px}
.tgl:hover{color:#64748b}
.tcard{background:#111827;border:1px solid #1e293b;border-radius:10px;padding:14px;margin-top:8px}
.ttbl{width:100%;border-collapse:collapse;font-size:.79rem}
.ttbl td{padding:5px 6px;border-bottom:1px solid #1e2030;color:#64748b}
.ttbl td:last-child{text-align:left;color:#e2e8f0;font-variant-numeric:tabular-nums;font-weight:500}
.ttbl tr:last-child td{border-bottom:none}
#tech{display:none}
#err{display:none;padding:10px 14px;color:#ef4444;background:#180000;border-radius:8px;margin-bottom:14px;font-size:.81rem}
.ld{text-align:center;padding:50px;color:#334155;font-size:.87rem}
</style>
</head>
<body>
<div class="hdr">
  <div><div class="hdr-title">رمان</div><div class="hdr-sub">Founder Command Center</div></div>
  <div class="hdr-meta">
    <span class="ts-lbl" id="ts">جاري التحميل…</span>
    <span id="pchip"></span>
  </div>
</div>
<div id="err"></div>
<div class="verdict" id="verdict"><div class="ld">جاري جلب البيانات…</div></div>
<div class="sec">ماذا بنى رمان؟</div>
<div class="assets" id="assets"></div>
<div class="sec">ماذا تغيّر منذ آخر زيارة؟</div>
<div id="delta"></div>
<div class="sec">مخاطر التوقف</div>
<div class="risks" id="risks"></div>
<button class="tgl" id="tgl-btn">▼ حالة الأنظمة التقنية</button>
<div id="tech"><div class="tcard"><table class="ttbl" id="ttbl"></table></div></div>
<script>
const PL={exam:'الاختبارات النهائية',pre_exam:'ما قبل الاختبارات',registration:'فترة التسجيل',grade_release:'نتائج وتظلمات',break:'إجازة',regular:'دراسة عادية'};
const PC={exam:'#ef4444',pre_exam:'#f59e0b',registration:'#3b82f6',grade_release:'#a78bfa',break:'#64748b',regular:'#22c55e'};
const SS={'قوي':{bg:'#052e16',c:'#4ade80'},'متوسط':{bg:'#1c1400',c:'#fbbf24'},'مبكر':{bg:'#0c1a3a',c:'#60a5fa'},'ضعيف':{bg:'#1a0505',c:'#f87171'}};
const BC={'نعم':'#4ade80','جزئياً':'#fbbf24','لا':'#f87171'};
const BT={'نعم':'يستفيد الطالب','جزئياً':'يستفيد الطالب جزئياً','لا':'لا يستفيد الطالب بعد'};
const RS={'سليم':{bg:'#052e16',c:'#4ade80'},'يعمل':{bg:'#052e16',c:'#4ade80'},'خطر':{bg:'#1a0505',c:'#f87171'},'غير محدد':{bg:'#1c1a06',c:'#facc15'}};

document.getElementById('tgl-btn').onclick=function(){
  const s=document.getElementById('tech'),open=s.style.display!=='none';
  s.style.display=open?'none':'block';
  this.textContent=open?'▼ حالة الأنظمة التقنية':'▲ إخفاء الأنظمة التقنية';
};

function acard(a){
  const s=SS[a.state]||{bg:'#1e293b',c:'#94a3b8'};
  const bc=BC[a.benefit]||'#94a3b8', bt=BT[a.benefit]||a.benefit;
  const note=a.note?`<div class="anote">↳ ${a.note}</div>`:'';
  return `<div class="acard"><div class="acard-top"><span class="aname">${a.label}</span><span class="pill" style="background:${s.bg};color:${s.c}">${a.state}</span></div><div class="adesc">${a.desc}</div><div class="brow"><span class="bdot" style="background:${bc}"></span><span style="color:${bc}">${bt}</span></div>${note}</div>`;
}

function renderDelta(snap,prev){
  const el=document.getElementById('delta');
  if(!prev||!prev.ts){el.innerHTML='<div class="delta-card"><span style="color:#334155;font-size:.81rem">ستظهر التغييرات في زيارتك القادمة</span></div>';return;}
  const hrs=Math.round((Date.now()-prev.ts)/3600000);
  const since=hrs<1?'منذ أقل من ساعة':hrs===1?'منذ ساعة':`منذ ${hrs} ساعة`;
  const items=[{v:snap.m-(prev.m||0),l:'رسالة'},{v:snap.q-(prev.q||0),l:'سؤال اختبار'},{v:snap.d-(prev.d||0),l:'مصدر'}];
  let rows='';
  items.forEach(i=>{const c=i.v>0?'#4ade80':i.v<0?'#f87171':'#334155',p=i.v>0?'+':'';rows+=`<div class="di"><span class="di-num" style="color:${c}">${p}${i.v.toLocaleString('ar-SA')}</span><span class="di-lbl">${i.l}</span></div>`;});
  el.innerHTML=`<div class="delta-card"><div class="di-row">${rows}</div><div class="di-since">${since}</div></div>`;
}

function render(d){
  const ac=d.academic,sv=d.services,kn=d.knowledge,bf=sv.backfill;
  // Header
  document.getElementById('ts').textContent=new Date(d.timestamp).toLocaleTimeString('ar-SA',{hour:'2-digit',minute:'2-digit'});
  const pc=PC[ac.phase]||'#64748b';
  document.getElementById('pchip').innerHTML=`<span class="phase-chip" style="background:${pc}22;color:${pc}">${PL[ac.phase]||ac.phase}</span>`;
  // Verdict
  const lr=d.launch_readiness||'—';
  const lc=lr==='مرتفعة'?'#4ade80':lr==='متوسطة'?'#fbbf24':'#f87171';
  const bhtml=d.main_blocker?`<div class="blocker"><span>⚠</span><span>${d.main_blocker}</span></div>`:`<div class="ok-box">✓ لا عقبات — المشروع يسير بشكل طبيعي</div>`;
  document.getElementById('verdict').innerHTML=`<div class="verdict-top"><div><div class="rd-label">جاهزية رمان للطالب</div><div class="rd-val" style="color:${lc}">${lr}</div></div></div>${bhtml}`;
  // Assets
  const assets=d.assets||{}, ORDER=['community','exams','documents','operational_knowledge','academic_context'];
  document.getElementById('assets').innerHTML=ORDER.filter(k=>assets[k]).map(k=>acard(assets[k])).join('');
  // Delta
  const snap={m:kn.messages_total||0,q:kn.exam_questions||0,d:kn.source_documents||0,ts:Date.now()};
  renderDelta(snap,JSON.parse(localStorage.getItem('rcc')||'null'));
  localStorage.setItem('rcc',JSON.stringify(snap));
  // Risks
  document.getElementById('risks').innerHTML=(d.strategic_risks||[]).map(r=>{
    const rs=RS[r.state]||{bg:'#1e293b',c:'#94a3b8'};
    return `<div class="rrow"><div class="rinfo"><div class="rname">${r.name}</div><div class="rdetail">${r.detail}</div><div class="rimpact">إذا توقف: ${r.impact}</div></div><span class="rbadge" style="background:${rs.bg};color:${rs.c}">${r.state}</span></div>`;
  }).join('');
  // Tech table
  document.getElementById('ttbl').innerHTML=`
    <tr><td>الرسائل الكلية</td><td>${(kn.messages_total||0).toLocaleString()}</td></tr>
    <tr><td>القنوات المرصودة</td><td>${(kn.channels_total||0).toLocaleString()}</td></tr>
    <tr><td>أسئلة الاختبارات</td><td>${kn.exam_questions>=0?kn.exam_questions.toLocaleString():'?'}</td></tr>
    <tr><td>مقاطع المستندات</td><td>${kn.document_chunks>=0?kn.document_chunks.toLocaleString():'?'}</td></tr>
    <tr><td>مصادر رسمية</td><td>${kn.source_documents>=0?kn.source_documents.toLocaleString():'?'}</td></tr>
    <tr><td>مواضيع مُفهرسة</td><td>${kn.kg_topics>=0?kn.kg_topics.toLocaleString():'?'}</td></tr>
    <tr><td>Backfill (pending/running/failed)</td><td>${bf.total_pending}/${bf.total_running}/${bf.total_failed}</td></tr>
    <tr><td>Embed pending</td><td>${sv.embed.total_pending>=0?sv.embed.total_pending:'?'}</td></tr>
    <tr><td>Question extraction (done/pending)</td><td>${sv.question_extraction.total_completed}/${sv.question_extraction.total_pending}</td></tr>
    <tr><td>Media pending</td><td>${sv.media.total_pending>=0?sv.media.total_pending:'?'}</td></tr>`;
}

async function refresh(){
  try{
    const r=await fetch('/ops/status');
    if(!r.ok)throw new Error('HTTP '+r.status);
    document.getElementById('err').style.display='none';
    render(await r.json());
  }catch(e){
    const el=document.getElementById('err');
    el.style.display='block';el.textContent='تعذّر تحميل البيانات: '+e.message;
  }
}
refresh();setInterval(refresh,30000);
</script>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)
