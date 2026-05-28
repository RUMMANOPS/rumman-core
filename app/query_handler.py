#!/usr/bin/env python3
"""
query_handler.py — The First Magical Loop.

Takes a course code + optional question, synthesizes intelligence from:
  1. course_intelligence view — official course metadata, downstream impact
  2. extracted_items — live Telegram intelligence (deadlines, tasks, decisions)
  3. document_chunks (via match_course_chunks) — historical exam/document content
  4. document_chunks (via match_chunks_general) — regulations and policy context

Usage:
    python3 app/query_handler.py MGT311
    python3 app/query_handler.py MGT311 "what topics appear most in finals?"
    python3 app/query_handler.py CS350 "what do I need to know about SQL exams?"
    python3 app/query_handler.py --recent-items MGT311  # show extracted_items only

Can also be imported as a module:
    from app.query_handler import answer_course_query
    result = await answer_course_query("MGT311", "exam topics")

Requires: SUPABASE_URL, SUPABASE_KEY, OPENAI_API_KEY env vars.
"""

import os
import json
import argparse
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

INSTITUTION = "SEU"
EMBED_MODEL = "text-embedding-3-large"
EMBED_DIMS = 1536
SYNTHESIS_MODEL = "gpt-4o-mini"

RECENT_ITEMS_DAYS = 30
MAX_CHUNKS = 20
MIN_SIMILARITY = 0.25
MAX_REGULATION_CHUNKS = 8

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

SYNTHESIS_SYSTEM_PROMPT = """\
You are RUMMAN, an academic intelligence assistant for a student at Saudi Electronic University (SEU).

Your role: synthesize all available intelligence about a course into a clear, actionable response.

You receive structured data about a course:
- Official course metadata (title, level, prerequisites, downstream impact)
- Live peer intelligence from Telegram chats (recent deadlines, tasks, decisions)
- Historical document content (past exams, study materials)
- University regulation context (when relevant)

Response guidelines:
1. Lead with what matters most operationally — deadlines, exam topics, risks.
2. Cite intelligence sources (e.g., "from Telegram group", "from past exam", "from course description").
3. Flag high-stakes courses (downstream_count > 2) prominently — failing them blocks many future courses.
4. Be specific: if a topic appears in past exams, say which exam type and how often.
5. If Telegram shows a deadline or decision, prioritize it — it's live peer data.
6. Use the same language as the student's question (Arabic if they ask in Arabic, English if English).
7. Empty intelligence is valid — say "no recent Telegram activity" rather than fabricating.
8. Keep responses focused and under 400 words unless the question requires more depth.
"""

SYNTHESIS_USER_TEMPLATE = """\
Course: {course_code} — {course_title}
Level: {level} | Credits: {credit_hours} | Program: {program}
Prerequisites: {prereqs}
Courses blocked if this is failed: {downstream} ({downstream_count} courses)
Description: {description}

--- LIVE TELEGRAM INTELLIGENCE (last {days} days) ---
{telegram_items}

--- HISTORICAL DOCUMENT CONTENT ---
{document_chunks}

--- REGULATION CONTEXT ---
{regulation_chunks}

--- STUDENT QUESTION ---
{question}
"""


def log(event: str, **kwargs):
    parts = [event] + [f"{k}={v}" for k, v in kwargs.items()]
    print(" | ".join(parts), flush=True)


async def get_course_intelligence(http: httpx.AsyncClient, course_code: str) -> Optional[dict]:
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/course_intelligence",
        headers=HEADERS,
        params={
            "course_code": f"eq.{course_code}",
            "institution": f"eq.{INSTITUTION}",
            "limit": "1",
        },
    )
    if r.status_code >= 400:
        log("COURSE_INTEL_ERROR", status=r.status_code, error=r.text[:120])
        return None
    rows = r.json()
    return rows[0] if rows else None


async def get_recent_extracted_items(http: httpx.AsyncClient, course_code: str, days: int = RECENT_ITEMS_DAYS) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/extracted_items",
        headers=HEADERS,
        params={
            "course_code": f"eq.{course_code}",
            "created_at": f"gte.{cutoff}",
            "order": "created_at.desc",
            "limit": "20",
        },
    )
    if r.status_code >= 400:
        return []
    return r.json()


async def embed_query(ai: AsyncOpenAI, query: str) -> list[float]:
    resp = await ai.embeddings.create(model=EMBED_MODEL, input=query, dimensions=EMBED_DIMS)
    return resp.data[0].embedding


async def match_course_chunks(http: httpx.AsyncClient, embedding: list[float], course_code: str) -> list[dict]:
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/rpc/match_course_chunks",
        headers={**HEADERS, "Content-Type": "application/json"},
        json={
            "query_embedding": embedding,
            "p_course_code": course_code,
            "p_institution": INSTITUTION,
            "match_count": MAX_CHUNKS,
            "min_similarity": MIN_SIMILARITY,
        },
    )
    if r.status_code >= 400:
        log("MATCH_CHUNKS_ERROR", status=r.status_code, error=r.text[:120])
        return []
    return r.json()


async def match_regulation_chunks(http: httpx.AsyncClient, embedding: list[float]) -> list[dict]:
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/rpc/match_chunks_general",
        headers={**HEADERS, "Content-Type": "application/json"},
        json={
            "query_embedding": embedding,
            "p_institution": INSTITUTION,
            "p_source_type": "regulation",
            "match_count": MAX_REGULATION_CHUNKS,
            "min_similarity": MIN_SIMILARITY,
        },
    )
    if r.status_code >= 400:
        return []
    return r.json()


def format_telegram_items(items: list[dict]) -> str:
    if not items:
        return "No recent Telegram intelligence for this course."

    lines = []
    for item in items:
        item_type = item.get("item_type", "note").upper()
        content = item.get("content", "")
        due = item.get("due_date")
        confidence = item.get("confidence", 0)
        chat = item.get("chat_name", "unknown chat")
        created = str(item.get("created_at", ""))[:10]

        line = f"[{item_type}] {content}"
        if due:
            line += f" → due: {due}"
        line += f" (conf: {int(confidence * 100)}% | {chat} | {created})"
        lines.append(line)

    return "\n".join(lines)


def format_document_chunks(chunks: list[dict]) -> str:
    if not chunks:
        return "No historical document content available for this course."

    lines = []
    for chunk in chunks:
        source_type = chunk.get("source_type", "document")
        exam_type = chunk.get("exam_type") or ""
        year = chunk.get("academic_year") or ""
        semester = chunk.get("semester") or ""
        similarity = chunk.get("similarity", 0)
        content = chunk.get("content", "")[:600]  # truncate very long chunks

        label = source_type
        if exam_type:
            label += f"/{exam_type}"
        if year:
            label += f" {year}"
        if semester:
            label += f" {semester}"

        lines.append(f"[{label} | {similarity:.0%} match]\n{content}")

    return "\n\n".join(lines)


def format_regulation_chunks(chunks: list[dict]) -> str:
    if not chunks:
        return "No regulation context retrieved."
    lines = []
    for chunk in chunks:
        content = chunk.get("content", "")[:400]
        similarity = chunk.get("similarity", 0)
        lines.append(f"[regulation | {similarity:.0%} match]\n{content}")
    return "\n\n".join(lines)


async def answer_course_query(
    course_code: str,
    question: str = "What should I know about this course for my exam?",
    recent_items_only: bool = False,
) -> dict:
    """
    Main entry point. Returns:
      {
        "course_code": str,
        "course_title": str,
        "response": str,
        "telegram_items": list,
        "document_chunks_count": int,
        "cost_usd": float,
      }
    """
    ai = AsyncOpenAI(api_key=OPENAI_API_KEY)

    async with httpx.AsyncClient(timeout=60) as http:
        course = await get_course_intelligence(http, course_code.upper())

        if not course:
            return {
                "course_code": course_code,
                "course_title": None,
                "response": f"Course {course_code} not found in database. Run scripts/seed_courses.py first.",
                "telegram_items": [],
                "document_chunks_count": 0,
                "cost_usd": 0.0,
            }

        telegram_items = await get_recent_extracted_items(http, course_code.upper())
        log("INTEL_FETCHED", course=course_code, telegram_items=len(telegram_items))

        if recent_items_only:
            return {
                "course_code": course_code,
                "course_title": course.get("course_title"),
                "response": format_telegram_items(telegram_items),
                "telegram_items": telegram_items,
                "document_chunks_count": 0,
                "cost_usd": 0.0,
            }

        embedding = await embed_query(ai, f"{course_code} {question}")
        log("EMBEDDING_DONE", course=course_code)

        doc_chunks, reg_chunks = await asyncio.gather(
            match_course_chunks(http, embedding, course_code.upper()),
            match_regulation_chunks(http, embedding),
        )
        log("CHUNKS_FETCHED", course=course_code, doc_chunks=len(doc_chunks), reg_chunks=len(reg_chunks))

        prereqs = ", ".join(course.get("prerequisite_codes") or []) or "None"
        downstream = ", ".join(course.get("blocks_codes") or []) or "None"
        downstream_count = course.get("downstream_count", 0)

        user_content = SYNTHESIS_USER_TEMPLATE.format(
            course_code=course_code.upper(),
            course_title=course.get("course_title", ""),
            level=course.get("level", "?"),
            credit_hours=course.get("credit_hours", "?"),
            program=course.get("program", "?"),
            prereqs=prereqs,
            downstream=downstream,
            downstream_count=downstream_count,
            description=course.get("description", "No description available.") or "No description available.",
            days=RECENT_ITEMS_DAYS,
            telegram_items=format_telegram_items(telegram_items),
            document_chunks=format_document_chunks(doc_chunks),
            regulation_chunks=format_regulation_chunks(reg_chunks),
            question=question,
        )

        response = await ai.chat.completions.create(
            model=SYNTHESIS_MODEL,
            temperature=0.1,
            messages=[
                {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )

        answer = response.choices[0].message.content
        input_tokens = response.usage.prompt_tokens
        output_tokens = response.usage.completion_tokens
        cost_usd = (input_tokens * 0.000_000_150) + (output_tokens * 0.000_000_600)

        log(
            "SYNTHESIS_DONE",
            course=course_code,
            tokens=f"{input_tokens}+{output_tokens}",
            cost_usd=f"${cost_usd:.4f}",
        )

        return {
            "course_code": course_code.upper(),
            "course_title": course.get("course_title"),
            "response": answer,
            "telegram_items": telegram_items,
            "document_chunks_count": len(doc_chunks),
            "cost_usd": cost_usd,
        }


async def main():
    parser = argparse.ArgumentParser(description="RUMMAN Course Intelligence Query")
    parser.add_argument("course_code", help="Course code (e.g. MGT311, CS350)")
    parser.add_argument("question", nargs="?", default=None, help="Optional question about the course")
    parser.add_argument("--recent-items", action="store_true", help="Show only recent Telegram intelligence, no synthesis")
    parser.add_argument("--json", action="store_true", help="Output raw JSON instead of formatted response")
    args = parser.parse_args()

    question = args.question or f"What should I know about {args.course_code} for my exam?"

    log("QUERY_START", course=args.course_code, question=question[:80])

    result = await answer_course_query(
        args.course_code,
        question=question,
        recent_items_only=args.recent_items,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print()
        print("━" * 60)
        print(f"  {result['course_code']} — {result.get('course_title', '?')}")
        print("━" * 60)
        print()
        print(result["response"])
        print()
        if not args.recent_items:
            print(f"  Sources: {result['document_chunks_count']} document chunks | "
                  f"{len(result['telegram_items'])} Telegram items | "
                  f"cost: ${result['cost_usd']:.4f}")
        print("━" * 60)


if __name__ == "__main__":
    asyncio.run(main())
