#!/usr/bin/env python3
"""
qa_mining_worker.py — Telegram Q&A pair extraction worker.

Mines the messages table for implicit question-answer pairs in student chat
groups. Extracted pairs are embedded and inserted as document_chunks with
source_type='telegram_export' and authority_tier='community', making them
immediately searchable by the retrieval pipeline.

Processing strategy:
  - Fetches messages in sliding windows of WINDOW_SIZE, ordered by
    (chat_name, message_date).
  - For each window, asks the LLM to identify Q&A pairs (question + answer
    + optional course code).
  - Skips windows that contain fewer than MIN_MESSAGES messages.
  - Tracks progress in analysis_runs (analyst_type='qa_miner') — resumable.
  - Deduplicates via MD5 of (chat_name, content) — never inserts the same
    Q&A pair twice.

Usage:
    python3 app/qa_mining_worker.py                      # all chats, from oldest
    python3 app/qa_mining_worker.py --chat "ChatName"    # one chat only
    python3 app/qa_mining_worker.py --limit 50           # process N windows, then exit
    python3 app/qa_mining_worker.py --dry-run            # no DB writes, stdout only
    python3 app/qa_mining_worker.py --check-schema       # verify tables, then exit

Not in Procfile — run on demand. See docs/03-workflows/railway-processes.md.
Requires: migration 026 applied.
"""

import os
import re
import json
import asyncio
import argparse
import hashlib
from datetime import datetime, timezone, date
from typing import Optional

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

SUPABASE_URL   = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

SEU_TENANT_ID  = os.environ.get("SEU_TENANT_ID", "00000000-0000-0000-0000-000000000001")

ANALYST_TYPE   = "qa_miner"
WORKER         = "qa_mining_v1"
EXTRACT_MODEL  = os.environ.get("QA_MINE_MODEL", "gpt-4o-mini")
EMBED_MODEL    = "text-embedding-3-large"
EMBED_DIMS     = 1536

WINDOW_SIZE    = 30   # messages per LLM call
WINDOW_OVERLAP = 5    # messages re-included from prior window to avoid split pairs
MIN_MESSAGES   = 5    # skip windows below this size
BATCH_DELAY_S  = 1.0  # sleep between windows (rate limit courtesy)

_MAX_EMBED_CHARS = 16_000

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

EXTRACT_SYSTEM = """\
You are an academic Q&A extraction assistant for a Saudi university student platform.

Given a sequence of Telegram messages from a student group, extract question-and-answer
pairs where a student asked an academic question and received a meaningful answer.

Rules:
1. Only extract pairs with clear academic value (course content, exam topics, deadlines,
   procedures). Skip social chat, greetings, complaints without useful answers.
2. The answer must actually appear in the messages — do not infer or fabricate.
3. A valid pair needs: a question (explicit or implicit), AND a substantive answer.
4. For Arabic messages: preserve Arabic text in both question and answer fields.
5. Confidence: 0.9 = very clear Q&A exchange; 0.7 = implied but clear; below 0.7 = omit.
6. course_code: extract if mentioned (e.g. "IT484"), otherwise null.
7. An empty pairs array is always valid — never fabricate pairs.

REQUIRED output schema:
{
  "pairs": [
    {
      "question": "the student question",
      "answer": "the substantive answer from the conversation",
      "course_code": "IT484 or null",
      "confidence": 0.85,
      "message_indices": [2, 5]
    }
  ]
}

Return ONLY valid JSON. No markdown, no extra text.\
"""


def log(event: str, **kwargs):
    parts = [event] + [f"{k}={v}" for k, v in kwargs.items()]
    print(" | ".join(parts), flush=True)


def _pair_fingerprint(chat_name: str, question: str, answer: str) -> str:
    key = f"{chat_name}|{question.strip()[:200]}|{answer.strip()[:200]}"
    return hashlib.md5(key.encode()).hexdigest()


# ── Schema check ──────────────────────────────────────────────────────────────

async def check_schema(http: httpx.AsyncClient) -> bool:
    ok = True
    for table in ["analysis_runs", "gap_items", "document_chunks"]:
        r = await http.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=HEADERS,
            params=[("limit", "0")],
        )
        status = "OK" if r.status_code == 200 else "MISSING"
        log(f"SCHEMA_{status}", table=table, http=r.status_code)
        if r.status_code != 200:
            ok = False
    return ok


# ── Data fetching ─────────────────────────────────────────────────────────────

async def fetch_chat_names(http: httpx.AsyncClient, filter_chat: Optional[str]) -> list[str]:
    if filter_chat:
        return [filter_chat]

    seen: set[str] = set()

    # Primary: telegram_backfill_jobs has all known chats (one job per chat).
    # This covers all 86 channels including those only reached via backfill,
    # not just the small subset tracked in telegram_sync_state.
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/telegram_backfill_jobs",
        headers=HEADERS,
        params=[("select", "chat_name"), ("limit", "5000")],
    )
    if r.status_code < 400:
        for row in r.json():
            name = row.get("chat_name")
            if name:
                seen.add(name)

    # Fallback: telegram_sync_state for chats the live listener has seen
    # but may not have a backfill job.
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/telegram_sync_state",
        headers=HEADERS,
        params=[("select", "chat_name"), ("limit", "5000")],
    )
    if r.status_code < 400:
        for row in r.json():
            name = row.get("chat_name")
            if name:
                seen.add(name)

    if not seen:
        log("FETCH_CHATS_ERROR", msg="no chats found in backfill_jobs or sync_state")
    return sorted(seen)


async def fetch_message_window(
    http: httpx.AsyncClient,
    chat_name: str,
    offset: int,
    size: int,
) -> list[dict]:
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/messages",
        headers=HEADERS,
        params=[
            ("select", "id,chat_name,sender_name,message_text,message_date"),
            ("chat_name", f"eq.{chat_name}"),
            ("message_type", "eq.text"),
            ("order", "message_date.asc"),
            ("limit", str(size)),
            ("offset", str(offset)),
        ],
    )
    if r.status_code >= 400:
        log("FETCH_MESSAGES_ERROR", chat=chat_name, status=r.status_code)
        return []

    return [m for m in r.json() if (m.get("message_text") or "").strip()]


async def count_messages(http: httpx.AsyncClient, chat_name: str) -> int:
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/messages",
        headers={**HEADERS, "Prefer": "count=exact"},
        params=[
            ("chat_name", f"eq.{chat_name}"),
            ("message_type", "eq.text"),
            ("limit", "0"),
        ],
    )
    if r.status_code >= 400:
        return 0
    content_range = r.headers.get("Content-Range", "")
    if "/" in content_range:
        try:
            return int(content_range.split("/")[1])
        except ValueError:
            return 0
    return 0


# ── Already-processed fingerprint check ──────────────────────────────────────

async def fetch_existing_fingerprints(
    http: httpx.AsyncClient,
    chat_name: str,
) -> set[str]:
    """
    Fetch content hashes of existing QA chunks for this chat to avoid duplicates.
    We store fingerprint in document_chunks.metadata->>'qa_fingerprint'.
    """
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/document_chunks",
        headers=HEADERS,
        params=[
            ("select", "metadata"),
            ("source_type", "eq.telegram_export"),
            ("chat_name", f"eq.{chat_name}"),
            ("limit", "5000"),
        ],
    )
    if r.status_code >= 400:
        return set()

    fps: set[str] = set()
    for row in r.json():
        meta = row.get("metadata") or {}
        fp = meta.get("qa_fingerprint")
        if fp:
            fps.add(fp)
    return fps


# ── Progress tracking via analysis_runs ──────────────────────────────────────

async def get_resume_offset(http: httpx.AsyncClient, chat_name: str) -> int:
    """Return the last saved offset for this chat, or 0 to start fresh."""
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/analysis_runs",
        headers=HEADERS,
        params=[
            ("analyst_type", f"eq.{ANALYST_TYPE}"),
            ("select", "output"),
            ("order", "ran_at.desc"),
            ("limit", "10"),
        ],
    )
    if r.status_code >= 400:
        return 0

    for row in r.json():
        output = row.get("output") or {}
        progress = output.get("chat_progress") or {}
        if chat_name in progress:
            return int(progress[chat_name].get("last_offset", 0))
    return 0


async def save_progress(
    http: httpx.AsyncClient,
    chat_name: str,
    offset: int,
    pairs_found: int,
    cost_usd: float,
):
    await http.post(
        f"{SUPABASE_URL}/rest/v1/analysis_runs",
        headers=HEADERS,
        json={
            "tenant_id":    SEU_TENANT_ID,
            "analyst_type": ANALYST_TYPE,
            "worker":       WORKER,
            "model":        EXTRACT_MODEL,
            "output": {
                "chat_progress": {
                    chat_name: {
                        "last_offset": offset,
                        "pairs_found": pairs_found,
                        "updated_at":  datetime.now(timezone.utc).isoformat(),
                    }
                }
            },
            "cost_usd": cost_usd,
            "notes": f"progress checkpoint: {chat_name} offset={offset}",
        },
    )


# ── LLM extraction ────────────────────────────────────────────────────────────

def _format_window(messages: list[dict]) -> str:
    lines = []
    for i, m in enumerate(messages):
        sender = m.get("sender_name") or "—"
        text   = (m.get("message_text") or "").strip().replace("\x00", "")
        d      = str(m.get("message_date") or "")[:10]
        lines.append(f"[{i}] {sender} ({d}): {text}")
    return "\n".join(lines)


async def extract_pairs(
    ai: AsyncOpenAI,
    messages: list[dict],
    chat_name: str,
) -> tuple[list[dict], float]:
    """Returns (pairs_list, cost_usd)."""
    formatted = _format_window(messages)
    prompt    = f'Chat: "{chat_name}"\n\nMessages:\n{formatted}'

    resp = await asyncio.wait_for(
        ai.chat.completions.create(
            model=EXTRACT_MODEL,
            temperature=0.1,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": EXTRACT_SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=1000,
        ),
        timeout=30,
    )

    raw     = resp.choices[0].message.content
    parsed  = json.loads(raw)
    pairs   = parsed.get("pairs", [])

    input_t = resp.usage.prompt_tokens
    output_t = resp.usage.completion_tokens
    cost = (input_t * 0.000_000_150) + (output_t * 0.000_000_600)

    return pairs, cost


# ── Embedding + chunk insert ──────────────────────────────────────────────────

async def embed_and_insert_pair(
    ai: AsyncOpenAI,
    http: httpx.AsyncClient,
    pair: dict,
    chat_name: str,
    source_message_ids: list[str],
    dry_run: bool,
) -> bool:
    question = (pair.get("question") or "").strip().replace("\x00", "")
    answer   = (pair.get("answer") or "").strip().replace("\x00", "")
    if not question or not answer:
        return False

    course_code = pair.get("course_code")
    content     = f"سؤال: {question}\nجواب: {answer}" if _is_arabic(question) else f"Q: {question}\nA: {answer}"
    # PostgreSQL TEXT columns reject null bytes from Telegram message content
    content = content.replace("\x00", "")

    if len(content) > _MAX_EMBED_CHARS:
        content = content[:_MAX_EMBED_CHARS]

    fingerprint = _pair_fingerprint(chat_name, question, answer)

    if dry_run:
        log("DRY_RUN_PAIR", chat=chat_name, course=course_code,
            q=question[:60], a=answer[:60])
        return True

    resp = await asyncio.wait_for(
        ai.embeddings.create(model=EMBED_MODEL, input=content, dimensions=EMBED_DIMS),
        timeout=30,
    )
    embedding = resp.data[0].embedding

    row = {
        "tenant_id":          SEU_TENANT_ID,
        "source_document_id": None,
        "content":            content,
        "embedding":          embedding,
        "embedding_model":    EMBED_MODEL,
        "embedding_dims":     EMBED_DIMS,
        "institution":        "SEU",
        "course_code":        course_code,
        "source_type":        "telegram_export",
        "authority_tier":     "community",
        "attribution_status": "original",
        "valid_from":         date.today().isoformat(),
        "chunk_index":        0,
        "total_chunks":       1,
        "chat_name":          chat_name,
        "metadata": {
            "qa_fingerprint":    fingerprint,
            "source_message_ids": source_message_ids[:10],
            "origin":            "qa_mining",
        },
    }

    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/document_chunks",
        headers=HEADERS,
        json=row,
    )

    if r.status_code == 409:
        return False  # duplicate
    if r.status_code >= 400:
        log("INSERT_ERROR", status=r.status_code, error=r.text[:120])
        return False
    return True


def _is_arabic(text: str) -> bool:
    arabic_chars = sum(1 for c in text if '؀' <= c <= 'ۿ')
    return arabic_chars > len(text) * 0.3


# ── Chat processing ───────────────────────────────────────────────────────────

async def process_chat(
    ai: AsyncOpenAI,
    http: httpx.AsyncClient,
    chat_name: str,
    window_limit: Optional[int],
    dry_run: bool,
) -> dict:
    total_messages = await count_messages(http, chat_name)
    if total_messages == 0:
        log("CHAT_SKIP", chat=chat_name, reason="no_messages")
        return {"chat_name": chat_name, "pairs": 0, "cost_usd": 0.0}

    resume_offset  = await get_resume_offset(http, chat_name)
    known_fps      = await fetch_existing_fingerprints(http, chat_name)

    log("CHAT_START", chat=chat_name, total_messages=total_messages,
        resume_offset=resume_offset, known_qa_pairs=len(known_fps))

    offset          = resume_offset
    pairs_inserted  = 0
    windows_done    = 0
    total_cost      = 0.0

    while offset < total_messages:
        if window_limit and windows_done >= window_limit:
            log("WINDOW_LIMIT_REACHED", chat=chat_name, windows=windows_done)
            break

        messages = await fetch_message_window(http, chat_name, offset, WINDOW_SIZE)
        if not messages:
            break

        if len(messages) < MIN_MESSAGES:
            offset += len(messages)
            continue

        try:
            pairs, cost = await extract_pairs(ai, messages, chat_name)
            total_cost += cost
        except Exception as e:
            log("EXTRACT_ERROR", chat=chat_name, offset=offset, error=str(e)[:120])
            offset += WINDOW_SIZE - WINDOW_OVERLAP
            await asyncio.sleep(BATCH_DELAY_S)
            continue

        source_ids = [m["id"] for m in messages]

        for pair in pairs:
            confidence = float(pair.get("confidence", 0.0))
            if confidence < 0.70:
                continue

            fp = _pair_fingerprint(chat_name,
                                   pair.get("question") or "",
                                   pair.get("answer") or "")
            if fp in known_fps:
                continue

            inserted = await embed_and_insert_pair(
                ai, http, pair, chat_name, source_ids, dry_run
            )
            if inserted:
                pairs_inserted += 1
                known_fps.add(fp)

        offset += WINDOW_SIZE - WINDOW_OVERLAP
        windows_done += 1

        if not dry_run and windows_done % 10 == 0:
            await save_progress(http, chat_name, offset, pairs_inserted, total_cost)

        log("WINDOW_DONE", chat=chat_name, offset=offset,
            pairs_this_window=len([p for p in pairs if float(p.get("confidence", 0)) >= 0.70]),
            total_pairs=pairs_inserted,
            cost=f"${total_cost:.4f}")

        await asyncio.sleep(BATCH_DELAY_S)

    if not dry_run:
        await save_progress(http, chat_name, offset, pairs_inserted, total_cost)

    log("CHAT_DONE", chat=chat_name, pairs_inserted=pairs_inserted,
        windows=windows_done, cost_usd=f"${total_cost:.4f}")

    return {"chat_name": chat_name, "pairs": pairs_inserted, "cost_usd": total_cost}


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="RUMMAN Q&A Mining Worker")
    parser.add_argument("--chat",         type=str,  default=None,
                        help="Process only this chat name")
    parser.add_argument("--limit",        type=int,  default=None,
                        help="Stop after N windows per chat (useful for testing)")
    parser.add_argument("--dry-run",      action="store_true",
                        help="Extract and print pairs, no DB writes")
    parser.add_argument("--check-schema", action="store_true",
                        help="Verify tables exist, then exit")
    args = parser.parse_args()

    log("QA_MINER_START", model=EXTRACT_MODEL, chat=args.chat or "all",
        dry_run=args.dry_run)

    ai = AsyncOpenAI(api_key=OPENAI_API_KEY)

    async with httpx.AsyncClient(timeout=90) as http:

        if args.check_schema:
            await check_schema(http)
            return

        chat_names = await fetch_chat_names(http, args.chat)
        if not chat_names:
            log("NO_CHATS", hint="No text messages found in messages table")
            return

        log("CHATS_FOUND", count=len(chat_names), names=chat_names)

        total_pairs = 0
        total_cost  = 0.0

        for chat_name in chat_names:
            result = await process_chat(
                ai, http, chat_name,
                window_limit=args.limit,
                dry_run=args.dry_run,
            )
            total_pairs += result["pairs"]
            total_cost  += result["cost_usd"]

        log("QA_MINER_DONE",
            chats=len(chat_names),
            total_pairs=total_pairs,
            total_cost_usd=f"${total_cost:.4f}")


if __name__ == "__main__":
    asyncio.run(main())
