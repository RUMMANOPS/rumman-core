#!/usr/bin/env python3
"""
chapter_attribution_worker.py — Map exam_questions → kg_chapters via topic similarity.

WHY THIS EXISTS:
  exam_questions.chapter_numbers is INT[] but kg_chapters is the canonical entity.
  This worker sets exam_questions.chapter_id (FK) and chapter_verified=true.
  It also updates kg_chapters.question_count and triggers refresh_chapter_stats().

HOW IT WORKS:
  1. Fetch exam_questions where chapter_id IS NULL AND topic_ids != '{}' AND embedding IS NOT NULL
     (topic_ids set by topic_normalizer_worker, embedding by embed pass)
  2. For each question, fetch all kg_chapters for the same course_code
  3. Compute cosine similarity between question embedding and chapter topic embeddings
     (chapter topics aggregated into a single embedding via kg_topics.embedding average)
  4. Assign chapter_id to the best match if similarity >= threshold
  5. If multiple chapters match (e.g. similarity >= 0.70 for chapters 2 and 3), assign both
  6. Update exam_questions.chapter_id, chapter_numbers, chapter_verified=true
  7. Add kg_provenance_edges row for auditability
  8. Call refresh_chapter_stats(course_code) after each batch

FALLBACK:
  If no kg_chapters exist for a course_code, fall back to GPT-4o-mini with
  topics_raw from chapter titles as context.

ENABLE: CHAPTER_ATTRIBUTION_WORKER_ENABLED=true
"""
from __future__ import annotations

import os
import json
import asyncio
import math
from typing import Optional

import httpx
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL   = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY   = os.environ.get("SUPABASE_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
TENANT_ID      = "00000000-0000-0000-0000-000000000001"

_ENABLED             = os.getenv("CHAPTER_ATTRIBUTION_WORKER_ENABLED", "").strip().lower() == "true"
SIMILARITY_THRESHOLD = float(os.getenv("CHAPTER_SIMILARITY_THRESHOLD", "0.72"))
SLEEP_SECONDS        = int(os.getenv("CHAPTER_ATTRIBUTION_SLEEP_SECONDS", "60"))
BATCH_SIZE           = int(os.getenv("CHAPTER_ATTRIBUTION_BATCH_SIZE", "20"))
EMBED_MODEL          = "text-embedding-3-large"
FALLBACK_MODEL       = "gpt-4o-mini"

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

_FALLBACK_SYSTEM = """\
You are an academic chapter attribution expert for Saudi Electronic University (SEU).

Given an exam question and a list of course chapters (number + title + topics),
identify which chapter(s) this question most likely belongs to.

A question may span multiple chapters (e.g. a synthesis question). List all that apply.

Respond ONLY with JSON:
{"chapter_numbers": [3, 4], "confidence": 0.88}

Rules:
- chapter_numbers: list of integers from the provided chapters only
- confidence: 0.0–1.0 (how certain you are)
- If truly uncertain, return {"chapter_numbers": [], "confidence": 0.0}
"""


def log(event: str, **kwargs):
    parts = [event] + [f"{k}={v}" for k, v in kwargs.items()]
    print(" | ".join(parts), flush=True)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na  = math.sqrt(sum(x * x for x in a))
    nb  = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _average_embedding(embeddings: list[list[float]]) -> list[float]:
    if not embeddings:
        return []
    dim = len(embeddings[0])
    avg = [sum(e[i] for e in embeddings) / len(embeddings) for i in range(dim)]
    return avg


async def _embed(client: AsyncOpenAI, text: str) -> Optional[list[float]]:
    try:
        resp = await client.embeddings.create(model=EMBED_MODEL, input=[text[:8000]])
        return resp.data[0].embedding
    except Exception as e:
        log("EMBED_ERROR", error=str(e)[:100])
        return None


async def _fetch_questions_needing_attribution(http: httpx.AsyncClient) -> list[dict]:
    resp = await http.get(
        f"{SUPABASE_URL}/rest/v1/exam_questions",
        headers=HEADERS,
        params={
            "select":         "id,course_code,question_text,topic_ids,embedding",
            "chapter_id":     "is.null",
            "chapter_verified": "eq.false",
            "embedding":      "not.is.null",
            "limit":          str(BATCH_SIZE),
        },
    )
    return resp.json() if resp.status_code == 200 else []


async def _fetch_chapters_for_course(http: httpx.AsyncClient, course_code: str) -> list[dict]:
    """Fetch all kg_chapters for a course, including topic embeddings via join."""
    resp = await http.get(
        f"{SUPABASE_URL}/rest/v1/kg_chapters",
        headers=HEADERS,
        params={
            "select":      "id,chapter_number,chapter_title,topic_ids,topics_raw",
            "course_code": f"eq.{course_code}",
            "order":       "chapter_number.asc",
        },
    )
    return resp.json() if resp.status_code == 200 else []


async def _get_topic_embeddings(http: httpx.AsyncClient, topic_ids: list[str]) -> list[list[float]]:
    """Fetch embeddings for a list of kg_topics.id."""
    if not topic_ids:
        return []
    ids_filter = "in.(" + ",".join(topic_ids) + ")"
    resp = await http.get(
        f"{SUPABASE_URL}/rest/v1/kg_topics",
        headers=HEADERS,
        params={
            "select":    "embedding",
            "id":        ids_filter,
            "embedding": "not.is.null",
        },
    )
    if resp.status_code == 200:
        return [r["embedding"] for r in resp.json() if r.get("embedding")]
    return []


async def _fallback_gpt_attribution(
    client: AsyncOpenAI,
    question: dict,
    chapters: list[dict],
) -> tuple[list[int], float]:
    """Use GPT-4o-mini when no embeddings available."""
    chapter_context = "\n".join(
        f"Chapter {c['chapter_number']}: {c.get('chapter_title', '')} "
        f"— Topics: {', '.join(c.get('topics_raw') or [])}"
        for c in chapters[:15]
    )
    try:
        resp = await client.chat.completions.create(
            model=FALLBACK_MODEL,
            messages=[
                {"role": "system", "content": _FALLBACK_SYSTEM},
                {"role": "user",   "content": (
                    f"Question (course: {question['course_code']}):\n"
                    f"{question['question_text'][:600]}\n\n"
                    f"Available chapters:\n{chapter_context}"
                )},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
            max_tokens=128,
        )
        result = json.loads(resp.choices[0].message.content or "{}")
        return result.get("chapter_numbers", []), result.get("confidence", 0.0)
    except Exception as e:
        log("FALLBACK_ERROR", error=str(e)[:100])
        return [], 0.0


async def _update_question_chapter(
    http: httpx.AsyncClient,
    question_id: str,
    chapter_id: str,
    chapter_numbers: list[int],
    confidence: float,
):
    payload = {
        "chapter_id":       chapter_id,
        "chapter_numbers":  chapter_numbers,
        "chapter_verified": confidence >= 0.70,
    }
    await http.patch(
        f"{SUPABASE_URL}/rest/v1/exam_questions?id=eq.{question_id}",
        headers={**HEADERS, "Prefer": "return=minimal"},
        content=json.dumps(payload),
    )


async def _add_provenance(
    http: httpx.AsyncClient,
    question_id: str,
    chapter_id: str,
    confidence: float,
    method: str,
):
    payload = {
        "subject_type": "exam_question",
        "subject_id":   question_id,
        "predicate":    "assigned_to",
        "object_type":  "kg_chapter",
        "object_id":    chapter_id,
        "confidence":   confidence,
        "created_by":   f"chapter_attribution_worker/{method}",
    }
    await http.post(
        f"{SUPABASE_URL}/rest/v1/kg_provenance_edges",
        headers={**HEADERS, "Prefer": "return=minimal"},
        content=json.dumps(payload),
    )


async def _refresh_chapter_stats(http: httpx.AsyncClient, course_code: str):
    await http.post(
        f"{SUPABASE_URL}/rest/v1/rpc/refresh_chapter_stats",
        headers=HEADERS,
        json={"p_course_code": course_code},
    )


async def process_question(
    client: AsyncOpenAI,
    http: httpx.AsyncClient,
    question: dict,
    chapters: list[dict],
    chapter_topic_embeddings: dict[str, list[float]],
) -> bool:
    q_embedding = question.get("embedding")
    if not q_embedding or not chapters:
        return False

    best_chapter_id = None
    best_number     = None
    best_sim        = 0.0
    matched_numbers = []

    for chapter in chapters:
        ch_embedding = chapter_topic_embeddings.get(chapter["id"])
        if not ch_embedding:
            continue
        sim = _cosine(q_embedding, ch_embedding)
        if sim >= SIMILARITY_THRESHOLD:
            matched_numbers.append(chapter["chapter_number"])
            if sim > best_sim:
                best_sim        = sim
                best_chapter_id = chapter["id"]
                best_number     = chapter["chapter_number"]

    if best_chapter_id and best_number is not None:
        chapter_numbers = sorted(set(matched_numbers))
        await _update_question_chapter(http, question["id"], best_chapter_id, chapter_numbers, best_sim)
        await _add_provenance(http, question["id"], best_chapter_id, best_sim, "embedding_similarity")
        return True

    # Fallback to GPT-4o-mini
    fallback_numbers, confidence = await _fallback_gpt_attribution(client, question, chapters)
    if fallback_numbers and confidence >= 0.60:
        # Find the chapter_id for the primary (first) chapter number
        ch_map = {c["chapter_number"]: c["id"] for c in chapters}
        primary_id = ch_map.get(fallback_numbers[0])
        if primary_id:
            await _update_question_chapter(http, question["id"], primary_id, fallback_numbers, confidence)
            await _add_provenance(http, question["id"], primary_id, confidence, "gpt_fallback")
            return True

    return False


async def main():
    if not _ENABLED:
        print("DISABLED — set CHAPTER_ATTRIBUTION_WORKER_ENABLED=true", flush=True)
        return

    log("START", threshold=SIMILARITY_THRESHOLD, batch=BATCH_SIZE)

    client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    async with httpx.AsyncClient(timeout=30) as http:
        while True:
            questions = await _fetch_questions_needing_attribution(http)
            if not questions:
                log("IDLE", sleep_seconds=SLEEP_SECONDS)
                await asyncio.sleep(SLEEP_SECONDS)
                continue

            # Group by course_code to batch chapter fetches
            by_course: dict[str, list[dict]] = {}
            for q in questions:
                by_course.setdefault(q["course_code"], []).append(q)

            total_attributed = 0
            for course_code, course_questions in by_course.items():
                chapters = await _fetch_chapters_for_course(http, course_code)
                if not chapters:
                    log("NO_CHAPTERS", course=course_code)
                    continue

                # Pre-fetch and average topic embeddings per chapter
                chapter_topic_embeddings: dict[str, list[float]] = {}
                for ch in chapters:
                    topic_ids = ch.get("topic_ids") or []
                    embeddings = await _get_topic_embeddings(http, topic_ids)
                    if embeddings:
                        chapter_topic_embeddings[ch["id"]] = _average_embedding(embeddings)

                attributed = 0
                for q in course_questions:
                    ok = await process_question(client, http, q, chapters, chapter_topic_embeddings)
                    if ok:
                        attributed += 1

                await _refresh_chapter_stats(http, course_code)
                log("COURSE_DONE", course=course_code, questions=len(course_questions), attributed=attributed)
                total_attributed += attributed

            log("BATCH_DONE", total=len(questions), attributed=total_attributed)


if __name__ == "__main__":
    asyncio.run(main())
