#!/usr/bin/env python3
"""
topic_normalizer_worker.py — Normalize topic_tags → kg_topics canonical registry.

WHY:
  exam_questions.topic_tags is a TEXT[] of free-form strings extracted by GPT-4o.
  The same concept appears as "TCP/IP", "نموذج TCP/IP", "TCP/IP Model", "طبقات TCP".
  Without normalization, topic-based queries return fractured results.

WHAT THIS WORKER DOES:
  1. Reads distinct topic_tags from exam_questions (unprocessed ones)
  2. For each tag, searches existing kg_topics by embedding similarity
  3. If cosine similarity >= 0.88 → merge as alias to existing canonical topic
  4. If no match → create new canonical topic
  5. Updates exam_questions.topic_ids with resolved kg_topics.id[]
  6. Logs aliases to kg_topic_aliases for future lookups

ENABLE: TOPIC_NORMALIZER_WORKER_ENABLED=true
COST:   1 embedding per new tag + 1 GPT-4o-mini call per genuinely new topic (for canonical naming)
"""
from __future__ import annotations

import os
import json
import asyncio
from typing import Optional

import httpx
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL   = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY   = os.environ.get("SUPABASE_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
TENANT_ID      = "00000000-0000-0000-0000-000000000001"

_ENABLED = os.getenv("TOPIC_NORMALIZER_WORKER_ENABLED", "").strip().lower() == "true"

SIMILARITY_THRESHOLD = float(os.getenv("TOPIC_SIMILARITY_THRESHOLD", "0.88"))
SLEEP_SECONDS        = int(os.getenv("TOPIC_NORMALIZER_SLEEP_SECONDS", "120"))
BATCH_SIZE           = int(os.getenv("TOPIC_NORMALIZER_BATCH_SIZE", "50"))

EMBED_MODEL  = "text-embedding-3-large"
NAMING_MODEL = "gpt-4o-mini"

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

_NAMING_SYSTEM = """\
You are an academic taxonomy expert for Saudi Electronic University (SEU).

Given a raw topic tag extracted from an exam question (may be Arabic, English, or mixed),
return a normalized canonical name that:
- Is consistent and reusable across courses
- Prefers English for international/technical terms (TCP/IP, Nash Equilibrium)
- Prefers Arabic for culture/religion/management concepts (نظرية الوكالة, الإدارة الاستراتيجية)
- Is specific: "TCP/IP Model" not "Networking"; "نظرية ناش" not "إدارة"
- Infers the academic domain: networking | management | finance | law | health | math | ...

Respond ONLY with JSON:
{"canonical_name": "TCP/IP Model", "canonical_name_ar": "نموذج TCP/IP", "domain": "networking"}
"""


def log(event: str, **kwargs):
    parts = [event] + [f"{k}={v}" for k, v in kwargs.items()]
    print(" | ".join(parts), flush=True)


async def _embed(client: AsyncOpenAI, texts: list[str]) -> list[list[float]]:
    resp = await client.embeddings.create(model=EMBED_MODEL, input=texts)
    return [r.embedding for r in resp.data]


async def _get_canonical_name(client: AsyncOpenAI, raw_tag: str) -> Optional[dict]:
    try:
        resp = await client.chat.completions.create(
            model=NAMING_MODEL,
            messages=[
                {"role": "system", "content": _NAMING_SYSTEM},
                {"role": "user",   "content": f"Raw tag: {raw_tag}"},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
            max_tokens=128,
        )
        return json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        log("NAMING_ERROR", tag=raw_tag[:50], error=str(e)[:100])
        return None


async def _find_similar_topic(http: httpx.AsyncClient, embedding: list[float]) -> Optional[dict]:
    """Find closest existing kg_topic by cosine similarity."""
    resp = await http.post(
        f"{SUPABASE_URL}/rest/v1/rpc/match_kg_topics",
        headers=HEADERS,
        json={"query_embedding": embedding, "match_threshold": SIMILARITY_THRESHOLD, "match_count": 1},
    )
    if resp.status_code == 200:
        results = resp.json()
        if results:
            return results[0]
    return None


async def _upsert_topic(http: httpx.AsyncClient, canonical: dict, embedding: list[float]) -> Optional[str]:
    name = canonical.get("canonical_name", "")
    payload = {
        "tenant_id":         TENANT_ID,
        "canonical_name":    name,
        "canonical_name_ar": canonical.get("canonical_name_ar"),
        "domain":            canonical.get("domain"),
        "embedding":         embedding,
    }
    resp = await http.post(
        f"{SUPABASE_URL}/rest/v1/kg_topics",
        headers={**HEADERS, "Prefer": "return=representation"},
        content=json.dumps(payload),
    )
    if resp.status_code in (200, 201):
        data = resp.json()
        return data[0]["id"] if isinstance(data, list) else data.get("id")
    # Already exists — look up by canonical_name
    lookup = await http.get(
        f"{SUPABASE_URL}/rest/v1/kg_topics",
        headers=HEADERS,
        params={"canonical_name": f"eq.{name}", "select": "id", "limit": "1"},
    )
    if lookup.status_code == 200:
        rows = lookup.json()
        if rows:
            return rows[0]["id"]
    log("UPSERT_TOPIC_ERROR", status=resp.status_code, body=resp.text[:200])
    return None


async def _upsert_alias(http: httpx.AsyncClient, topic_id: str, alias: str, source: str = "question"):
    payload = {
        "topic_id":  topic_id,
        "alias":     alias,
        "language":  "ar" if any("؀" <= c <= "ۿ" for c in alias) else "en",
        "source":    source,
    }
    await http.post(
        f"{SUPABASE_URL}/rest/v1/kg_topic_aliases?on_conflict=topic_id,alias",
        headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
        content=json.dumps(payload),
    )


async def _fetch_unprocessed_questions(http: httpx.AsyncClient) -> list[dict]:
    """Get exam_questions with topic_tags but empty topic_ids."""
    resp = await http.get(
        f"{SUPABASE_URL}/rest/v1/exam_questions",
        headers=HEADERS,
        params={
            "select":      "id,topic_tags,course_code",
            "topic_tags":  "neq.{}",
            "topic_ids":   "eq.{}",
            "limit":       str(BATCH_SIZE),
        },
    )
    if resp.status_code == 200:
        return resp.json()
    return []


async def _update_question_topic_ids(http: httpx.AsyncClient, question_id: str, topic_ids: list[str]):
    payload = {"topic_ids": topic_ids}
    await http.patch(
        f"{SUPABASE_URL}/rest/v1/exam_questions?id=eq.{question_id}",
        headers={**HEADERS, "Prefer": "return=minimal"},
        content=json.dumps(payload),
    )


async def process_batch(client: AsyncOpenAI, http: httpx.AsyncClient, questions: list[dict]) -> int:
    """
    Process a batch of questions: normalize their topic_tags → kg_topics.
    Returns number of questions updated.
    """
    # Collect all unique tags from the batch
    all_tags: set[str] = set()
    for q in questions:
        all_tags.update(q.get("topic_tags") or [])

    if not all_tags:
        return 0

    log("BATCH_TAGS", count=len(all_tags), questions=len(questions))

    # Embed all unique tags in one API call
    tag_list = list(all_tags)
    try:
        embeddings = await _embed(client, tag_list)
    except Exception as e:
        log("EMBED_ERROR", error=str(e)[:100])
        return 0

    tag_to_topic_id: dict[str, str] = {}

    for tag, embedding in zip(tag_list, embeddings):
        # Try to find existing similar topic
        match = await _find_similar_topic(http, embedding)
        if match:
            topic_id = match["id"]
            await _upsert_alias(http, topic_id, tag)
            tag_to_topic_id[tag] = topic_id
            log("MERGED_ALIAS", tag=tag[:40], canonical=match.get("canonical_name", "?"))
        else:
            # New topic — get canonical name from LLM
            canonical = await _get_canonical_name(client, tag)
            if not canonical or not canonical.get("canonical_name"):
                canonical = {"canonical_name": tag, "domain": "general"}

            topic_id = await _upsert_topic(http, canonical, embedding)
            if topic_id:
                await _upsert_alias(http, topic_id, tag)
                tag_to_topic_id[tag] = topic_id
                log("NEW_TOPIC", tag=tag[:40], canonical=canonical.get("canonical_name"), domain=canonical.get("domain"))

    # Update each question with resolved topic_ids
    updated = 0
    for q in questions:
        tags = q.get("topic_tags") or []
        topic_ids = list({tag_to_topic_id[t] for t in tags if t in tag_to_topic_id})
        if topic_ids:
            await _update_question_topic_ids(http, q["id"], topic_ids)
            updated += 1

    return updated


async def main():
    if not _ENABLED:
        print("DISABLED — set TOPIC_NORMALIZER_WORKER_ENABLED=true to activate", flush=True)
        return

    log("START", model_embed=EMBED_MODEL, model_name=NAMING_MODEL,
        similarity_threshold=SIMILARITY_THRESHOLD, batch_size=BATCH_SIZE)

    client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    async with httpx.AsyncClient(timeout=30) as http:
        while True:
            questions = await _fetch_unprocessed_questions(http)
            if not questions:
                log("IDLE", sleep_seconds=SLEEP_SECONDS)
                await asyncio.sleep(SLEEP_SECONDS)
                continue

            updated = await process_batch(client, http, questions)
            log("BATCH_DONE", processed=len(questions), updated=updated)


if __name__ == "__main__":
    asyncio.run(main())
