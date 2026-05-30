#!/usr/bin/env python3
"""
eval_bot_quality.py — Before/after synthesis quality comparison.

Simulates exactly what search_api.py does for each query:
  BEFORE: vector chunks only (old code queried intelligence_items = 0 rows)
  AFTER:  vector chunks + extracted_items (new code, daily_brief output)

Usage:
    python3 scripts/eval_bot_quality.py
"""
from __future__ import annotations

import os
import sys
import json
import asyncio
from typing import Optional, List, Dict, Any

import httpx
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL  = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY  = os.environ["SUPABASE_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
SEU_TENANT_ID = "00000000-0000-0000-0000-000000000001"

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}

EMBED_MODEL = "text-embedding-3-large"
EMBED_DIMS  = 1536

SYNTHESIS_MODEL = os.environ.get("EVAL_MODEL", "gpt-4o-mini")

# ── Synthesis prompts (exact copies from search_api.py) ──────────────────────

SYSTEM_BEFORE = """\
You are رمّان (Rummaan) — an intelligent academic companion for Saudi Electronic University students.

Each source chunk is tagged with an authority tier:
  [OFFICIAL]      — extracted from official university documents
  [COMMUNITY]     — student-shared materials
  [INTELLIGENCE]  — extracted events and announcements from Telegram group messages
  [CALENDAR]      — official SEU academic calendar dates

Grounding rules:
- Use ONLY information present in the provided source chunks. Do not invent or extrapolate.
- Chunks may be in Arabic or English — understand both; respond in the student's language.
- When chunks are off-topic: say "ما لقيت إجابة واضحة في المواد المتاحة — جرّب تذكر رمز المادة أو اسأل بطريقة مختلفة."

Style:
- Gulf Arabic (خليجي) for Arabic questions. Clear, natural English for English questions.
- Be direct, specific, and substantive. 150-250 words.
- Do NOT add meta-commentary. Do NOT explain what you're doing — just answer."""

SYSTEM_AFTER = """\
You are رمّان (Rummaan) — an intelligent academic companion for Saudi Electronic University students.

Each source chunk is tagged with an authority tier:
  [OFFICIAL]      — extracted from official university documents (study plans, regulations, course descriptions)
  [COMMUNITY]     — student-shared materials (exam archives, notes, group discussions)
  [INTELLIGENCE]  — extracted events and announcements from Telegram group messages (deadlines, exams, assignments)
  [CALENDAR]      — official SEU academic calendar dates

Grounding rules:
- Use ONLY information present in the provided source chunks. Do not invent or extrapolate.
- Chunks may be in Arabic or English — understand both; respond in the student's language.
- When OFFICIAL and COMMUNITY sources agree: answer directly.
- When they differ or conflict: present the official position first, then note the community perspective.
- [INTELLIGENCE] items represent what instructors/students actually posted in groups — treat as reliable but community-sourced.
  If an [INTELLIGENCE] item gives a deadline or exam date, present it clearly with a note it came from a group announcement.
- [CALENDAR] items are the authoritative official SEU schedule — use them for semester dates.
- When chunks contain exam questions: identify the topics and concepts they test, present them clearly.
- When chunks contain definitions, explanations, or course content: synthesize in your own words.
- When chunks partially answer the question: share what you found and be honest about the gap.
- When chunks are off-topic: say "ما لقيت إجابة واضحة في المواد المتاحة — جرّب تذكر رمز المادة أو اسأل بطريقة مختلفة."

Style:
- Gulf Arabic (خليجي) for Arabic questions. Clear, natural English for English questions.
- Answer like the smartest student in the class explaining to a friend — direct, specific, practical.
- When you have enough material: give a complete, useful answer (150-300 words is normal; use what the question requires).
- When [INTELLIGENCE] items contain deadlines or announcements: surface them prominently near the top.
- Do NOT mention professor names or predict unreleased exam content.
- Do NOT add meta-commentary ("Based on the sources...", "According to the chunks...").
- Do NOT explain what you're doing — just answer."""

USER_PROMPT = "Student question: {query}\n\nSource chunks:\n{chunks}"

# ── 10 test queries ───────────────────────────────────────────────────────────

QUERIES = [
    # 1. Directly in extracted_items — remote finals decision
    "هل اختبارات نهاية الفصل حضورية ولا اونلاين؟",

    # 2. IT484 quiz — item in extracted_items
    "IT484 فيه كويز متى؟",

    # 3. IT488 — item in extracted_items
    "IT488 شو المطلوب مني الحين؟",

    # 4. Summer semester timing — item in extracted_items
    "متى يبدأ الترم الصيفي في جامعة سعودية الالكترونية؟",

    # 5. معادلة deadline — item in extracted_items (due today)
    "موعد المعادلة متى يقفل؟",

    # 6. IT351 exam topics — vector + extracted_items task
    "What topics are covered in IT351?",

    # 7. CS481 — item in extracted_items
    "CS481 quiz is when?",

    # 8. Study plan — pure vector, no extracted_items (tests baseline quality)
    "ما هي متطلبات التخرج في برنامج تقنية المعلومات؟",

    # 9. IT487 quiz — item in extracted_items
    "IT487 quiz details",

    # 10. General exam question with course code — tests intel trigger
    "IT231 كويز وخلاصة متى؟",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

async def embed(ai: AsyncOpenAI, text: str) -> List[float]:
    resp = await ai.embeddings.create(model=EMBED_MODEL, input=text, dimensions=EMBED_DIMS)
    return resp.data[0].embedding


async def vector_search(http: httpx.AsyncClient, embedding: List[float],
                        course_code: Optional[str] = None) -> List[Dict[str, Any]]:
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/rpc/match_documents",
        headers=HEADERS,
        json={
            "query_embedding": embedding,
            "match_count":     50,
            "filter_course":   course_code,
            "filter_type":     None,
        },
    )
    if r.status_code >= 400:
        return []
    return [row for row in r.json() if (row.get("similarity") or 0) >= 0.25]


async def fetch_extracted_items(http: httpx.AsyncClient,
                                course_codes: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    params = [
        ("tenant_id",      f"eq.{SEU_TENANT_ID}"),
        ("confidence",     "gte.0.65"),
        ("validity_status", "neq.rejected"),
        ("select",         "item_type,content,due_date,course_code,confidence,chat_name"),
        ("order",          "due_date.asc.nullslast"),
        ("limit",          "20"),
    ]
    if course_codes:
        params.append(("course_code", f"in.({','.join(course_codes)})"))

    r = await http.get(f"{SUPABASE_URL}/rest/v1/extracted_items", headers=HEADERS, params=params)
    if r.status_code >= 400:
        return []
    return r.json()


def _tier_label(row: Dict[str, Any]) -> str:
    origin = (row.get("metadata") or {}).get("origin", "")
    if origin == "academic_calendar":
        return "[CALENDAR]"
    if origin in ("intelligence_items", "extracted_items"):
        return "[INTELLIGENCE]"
    tier = row.get("source_authority") or row.get("authority_tier") or ""
    if tier == "official":
        return "[OFFICIAL]"
    return "[COMMUNITY]"


ITEM_LABELS = {
    "task": "مهمة", "deadline": "موعد تسليم", "decision": "قرار",
    "risk": "تحذير", "follow_up": "متابعة", "exam": "اختبار",
    "quiz": "اختبار قصير", "announcement": "إعلان",
}


def format_intel_item(item: Dict[str, Any]) -> Dict[str, Any]:
    label   = ITEM_LABELS.get(item.get("item_type", ""), item.get("item_type", ""))
    content = item.get("content") or ""
    due     = item.get("due_date")
    code    = item.get("course_code") or ""
    chat    = item.get("chat_name") or "مجموعة"

    lines = [f"[{label}] {content}"]
    if due:
        lines.append(f"الموعد: {due}")
    if code:
        lines.append(f"المادة: {code}")
    lines.append(f"المصدر: {chat}")

    return {
        "content":     "\n".join(lines),
        "course_code": code or None,
        "source_authority": "community",
        "similarity":  min(float(item.get("confidence", 0.65)), 0.88),
        "metadata":    {"origin": "extracted_items"},
    }


def format_chunks_for_prompt(chunks: List[Dict[str, Any]], max_per_chunk: int, limit: int) -> str:
    return "\n\n---\n\n".join(
        f"{_tier_label(row)} [{i+1}] {(row.get('content') or '').strip()[:max_per_chunk]}"
        for i, row in enumerate(chunks[:limit])
    )


async def synthesize(ai: AsyncOpenAI, query: str, chunks: List[Dict[str, Any]],
                     system_prompt: str, max_chars: int, chunk_limit: int,
                     max_tokens: int) -> tuple:
    chunk_text = format_chunks_for_prompt(chunks, max_chars, chunk_limit)
    resp = await ai.chat.completions.create(
        model=SYNTHESIS_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": USER_PROMPT.format(
                query=query, chunks=chunk_text
            )},
        ],
        temperature=0.1,
        max_tokens=max_tokens,
    )
    answer = resp.choices[0].message.content.strip()
    tokens = resp.usage.total_tokens if resp.usage else 0
    return answer, tokens


def extract_course_codes(query: str) -> List[str]:
    """Simple regex-based course code extraction (no query_understanding dependency)."""
    import re
    return list(set(re.findall(r'\b([A-Z]{2,6}\d{3})\b', query.upper())))


# ── Main evaluation loop ──────────────────────────────────────────────────────

SEP = "═" * 72

async def main():
    ai = AsyncOpenAI(api_key=OPENAI_API_KEY)

    print(SEP)
    print("  RUMMAN BOT QUALITY EVALUATION — BEFORE vs AFTER")
    print(f"  Model: {SYNTHESIS_MODEL}")
    print(SEP)

    start_from = int(os.environ.get("EVAL_START", "1"))

    async with httpx.AsyncClient(timeout=60) as http:
        for i, query in enumerate(QUERIES, 1):
            if i < start_from:
                continue

            print(f"\n{'─'*72}")
            print(f"  Q{i}: {query}")
            print(f"{'─'*72}")

            # ── Embed query ───────────────────────────────────────────────────
            embedding = await embed(ai, query)
            course_codes = extract_course_codes(query)

            # ── Vector retrieval (same for both) ──────────────────────────────
            cc = course_codes[0] if course_codes else None
            raw_chunks = await vector_search(http, embedding, cc)

            # Deduplicate
            import hashlib
            seen: dict = {}
            for row in raw_chunks:
                key = hashlib.md5((row.get("content") or "").encode()).hexdigest()
                if key not in seen or row.get("similarity", 0) > seen[key].get("similarity", 0):
                    seen[key] = row
            deduped = sorted(seen.values(), key=lambda r: r.get("similarity", 0), reverse=True)

            # ── BEFORE: 5 chunks × 500 chars, no extracted_items ─────────────
            before_chunks = deduped[:5]
            before_answer, before_tok = await synthesize(
                ai, query, before_chunks,
                system_prompt=SYSTEM_BEFORE,
                max_chars=500, chunk_limit=5, max_tokens=350,
            )

            # ── AFTER: 8 chunks × 800 chars + extracted_items ────────────────
            intel_items_raw = await fetch_extracted_items(http, course_codes if course_codes else None)
            intel_chunks = [format_intel_item(item) for item in intel_items_raw]

            # Merge: intel items get priority (sort by similarity desc)
            after_all = deduped[:8] + intel_chunks
            after_all.sort(key=lambda r: r.get("similarity", 0), reverse=True)

            after_answer, after_tok = await synthesize(
                ai, query, after_all,
                system_prompt=SYSTEM_AFTER,
                max_chars=800, chunk_limit=8, max_tokens=600,
            )

            # ── Print comparison ──────────────────────────────────────────────
            intel_injected = len(intel_chunks)
            print(f"\n  [BEFORE] {len(before_chunks)} chunks · 500 chars · max_tokens=350 · 0 INTELLIGENCE items · {before_tok} tokens used")
            print(f"  {'─'*66}")
            for line in before_answer.split("\n"):
                print(f"  {line}")

            print(f"\n  [AFTER] {min(8, len(after_all))} chunks · 800 chars · max_tokens=600 · {intel_injected} INTELLIGENCE items · {after_tok} tokens used")
            print(f"  {'─'*66}")
            for line in after_answer.split("\n"):
                print(f"  {line}")

            print()

    print(SEP)
    print("  Evaluation complete.")
    print(SEP)


if __name__ == "__main__":
    asyncio.run(main())
