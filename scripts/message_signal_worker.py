#!/usr/bin/env python3
"""
message_signal_worker.py — Extract typed intelligence signals from Telegram messages.

Documents tell us what EXISTS. Messages tell us what MATTERS.
This worker mines message conversations for:
  - exam_emphasis  : professor or student flagging importance for exams
  - difficulty     : recurring confusion or struggle signals
  - professor_note : direct instructor guidance recorded in group
  - resource_rec   : recommended resources (videos, books, summaries)
  - confusion_cluster: multiple students asking the same thing = knowledge gap

Results are stored in message_signals and injected into the synthesis context
bundle by _build_context_block() in search_api.py.

Strategy:
  - Sliding window over messages per chat (WINDOW_SIZE=50, OVERLAP=10)
  - gpt-4o-mini extracts signals from each window
  - Deduplication via (course_code, chat_name, signal_type, content hash)
  - Progress tracked in analysis_runs (resumable)
  - is_current_semester inferred from message_date within CURRENT_SEMESTER_WINDOW_DAYS

Cost estimate: ~2,000 windows across 67K messages × ~600 tokens = ~$0.18 total (one-time)

Usage:
    python3 scripts/message_signal_worker.py [--dry-run] [--chat "ChatName"] [--limit 50]
    python3 scripts/message_signal_worker.py --check-schema

Requires: migration 032 applied.
"""

from __future__ import annotations

import os
import sys
import re
import json
import asyncio
import argparse
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

SUPABASE_URL   = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

SEU_TENANT_ID  = os.environ.get("SEU_TENANT_ID", "00000000-0000-0000-0000-000000000001")

ANALYST_TYPE   = "message_signal_miner"
WORKER         = "message_signal_v1"
EXTRACT_MODEL  = os.environ.get("SIGNAL_MINE_MODEL", "gpt-4o-mini")

WINDOW_SIZE    = 50    # messages per LLM call
WINDOW_OVERLAP = 10    # messages re-included from prior window
MIN_MESSAGES   = 8     # skip windows below this (too sparse for reliable signals)
BATCH_DELAY_S  = 0.8   # rate-limit courtesy between windows

# Messages dated within this many days count as "current semester"
CURRENT_SEMESTER_WINDOW_DAYS = int(os.environ.get("CURRENT_SEMESTER_WINDOW_DAYS", "120"))

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

VALID_SIGNAL_TYPES = {
    "exam_emphasis", "difficulty", "professor_note", "resource_rec", "confusion_cluster"
}

EXTRACT_SYSTEM = """\
You are an academic intelligence analyst for a Saudi university (SEU) student platform.

Given a window of Telegram messages from a student group, extract typed intelligence signals
that would help future students studying for exams or understanding course material.

Signal types (ONLY use these exact strings):
  "exam_emphasis"      — professor or student flagging a topic/chapter will be on an exam
  "difficulty"         — multiple students struggling with a specific concept or topic
  "professor_note"     — direct instructor guidance shared in the group (grading, coverage, hints)
  "resource_rec"       — a recommended resource: video, summary, book chapter, website
  "confusion_cluster"  — same question appearing from multiple students = knowledge gap

Rules:
1. Extract ONLY signals with real academic value. Skip social chat, greetings, complaints.
2. signal_content: 1-2 sentences in the SAME LANGUAGE as the source messages. Be specific.
   BAD:  "Professor mentioned chapter 4"
   GOOD: "الدكتور قال إن فصل 4 كامل سيكون في الميدترم بما في ذلك المعادلات"
3. course_code: extract if explicitly mentioned (e.g. "IT484", "CS251"). null if not clear.
4. source_count: how many distinct messages support this signal (1-50).
5. confidence: 0.9 = very clear; 0.7 = implied but clear; omit anything below 0.65.
6. semester_hint: extract if a semester is mentioned ("Fall 2025", "الفصل الثاني 2025"),
   otherwise null.
7. An empty signals array is valid — never fabricate signals.

REQUIRED output schema (valid JSON, no markdown):
{
  "signals": [
    {
      "signal_type": "exam_emphasis",
      "signal_content": "الفصل الثالث والرابع سيكون في الفاينل حسب الدكتور",
      "course_code": "CS251",
      "source_count": 3,
      "source_message_indices": [2, 7, 12],
      "confidence": 0.88,
      "semester_hint": null
    }
  ]
}"""


def log(event: str, **kwargs):
    parts = [event] + [f"{k}={v}" for k, v in kwargs.items()]
    print(" | ".join(parts), flush=True)


def _signal_fingerprint(course_code: str | None, chat_name: str, signal_type: str, content: str) -> str:
    key = f"{course_code or ''}|{chat_name}|{signal_type}|{content.strip()[:150]}"
    return hashlib.md5(key.encode()).hexdigest()


def _is_current_semester(messages: list[dict]) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(days=CURRENT_SEMESTER_WINDOW_DAYS)
    for m in messages:
        d = m.get("message_date")
        if not d:
            continue
        try:
            dt = datetime.fromisoformat(str(d).replace("Z", "+00:00"))
            if dt >= cutoff:
                return True
        except Exception:
            pass
    return False


async def check_schema(http: httpx.AsyncClient) -> bool:
    ok = True
    for table in ["message_signals", "analysis_runs", "messages"]:
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


async def fetch_chat_names(http: httpx.AsyncClient, filter_chat: Optional[str]) -> list[str]:
    if filter_chat:
        return [filter_chat]

    seen: set[str] = set()
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
            ("select",       "id,platform_message_id,chat_name,sender_name,message_text,message_date"),
            ("chat_name",    f"eq.{chat_name}"),
            ("message_type", "eq.text"),
            ("order",        "message_date.asc"),
            ("limit",        str(size)),
            ("offset",       str(offset)),
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
            ("chat_name",    f"eq.{chat_name}"),
            ("message_type", "eq.text"),
            ("limit",        "0"),
        ],
    )
    if r.status_code >= 400:
        return 0
    cr = r.headers.get("Content-Range", "")
    if "/" in cr:
        try:
            return int(cr.split("/")[1])
        except ValueError:
            pass
    return 0


async def fetch_existing_fingerprints(http: httpx.AsyncClient, chat_name: str) -> set[str]:
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/message_signals",
        headers=HEADERS,
        params=[
            ("chat_name", f"eq.{chat_name}"),
            ("select",    "signal_content,signal_type,course_code"),
            ("limit",     "5000"),
        ],
    )
    if r.status_code >= 400:
        return set()
    fps: set[str] = set()
    for row in r.json():
        fp = _signal_fingerprint(
            row.get("course_code"),
            chat_name,
            row.get("signal_type", ""),
            row.get("signal_content", ""),
        )
        fps.add(fp)
    return fps


async def get_resume_offset(http: httpx.AsyncClient, chat_name: str) -> int:
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/analysis_runs",
        headers=HEADERS,
        params=[
            ("analyst_type", f"eq.{ANALYST_TYPE}"),
            ("select",       "output"),
            ("order",        "ran_at.desc"),
            ("limit",        "20"),
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
    signals_found: int,
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
                        "last_offset":   offset,
                        "signals_found": signals_found,
                        "updated_at":    datetime.now(timezone.utc).isoformat(),
                    }
                }
            },
            "cost_usd": cost_usd,
            "notes":    f"progress: {chat_name} offset={offset} signals={signals_found}",
        },
    )


def _format_window(messages: list[dict]) -> str:
    lines = []
    for i, m in enumerate(messages):
        sender = m.get("sender_name") or "—"
        text   = (m.get("message_text") or "").strip().replace("\x00", "")
        d      = str(m.get("message_date") or "")[:10]
        lines.append(f"[{i}] {sender} ({d}): {text[:300]}")
    return "\n".join(lines)


async def extract_signals(
    ai: AsyncOpenAI,
    messages: list[dict],
    chat_name: str,
) -> tuple[list[dict], float]:
    """Returns (signals_list, cost_usd)."""
    formatted = _format_window(messages)
    prompt    = f'Chat: "{chat_name}"\n\nMessages:\n{formatted}'

    resp = await ai.chat.completions.create(
        model=EXTRACT_MODEL,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": EXTRACT_SYSTEM},
            {"role": "user",   "content": prompt[:12000]},
        ],
        max_tokens=800,
    )

    raw  = resp.choices[0].message.content or "{}"
    data = json.loads(raw)
    signals = data.get("signals") or []

    in_tok  = resp.usage.prompt_tokens     if resp.usage else 0
    out_tok = resp.usage.completion_tokens if resp.usage else 0
    cost    = (in_tok * 0.15 + out_tok * 0.60) / 1_000_000

    validated = []
    for s in signals:
        stype = s.get("signal_type", "")
        if stype not in VALID_SIGNAL_TYPES:
            continue
        content = (s.get("signal_content") or "").strip()
        if not content:
            continue
        conf = float(s.get("confidence", 0.0))
        if conf < 0.65:
            continue
        validated.append({
            "signal_type":             stype,
            "signal_content":          content,
            "course_code":             s.get("course_code"),
            "source_count":            int(s.get("source_count", 1)),
            "source_message_indices":  s.get("source_message_indices", []),
            "confidence":              round(conf, 3),
            "semester_hint":           s.get("semester_hint"),
        })

    return validated, cost


async def insert_signals(
    http: httpx.AsyncClient,
    chat_name: str,
    signals: list[dict],
    messages: list[dict],
    existing_fps: set[str],
    dry_run: bool,
) -> int:
    current = _is_current_semester(messages)
    inserted = 0

    for s in signals:
        fp = _signal_fingerprint(
            s.get("course_code"), chat_name, s["signal_type"], s["signal_content"]
        )
        if fp in existing_fps:
            continue

        # Resolve source_message_ids from indices
        src_ids: list[int] = []
        for idx in (s.get("source_message_indices") or []):
            if 0 <= idx < len(messages):
                mid = messages[idx].get("platform_message_id")
                if mid:
                    src_ids.append(int(mid))

        row = {
            "tenant_id":         SEU_TENANT_ID,
            "course_code":       s.get("course_code"),
            "chat_name":         chat_name,
            "signal_type":       s["signal_type"],
            "signal_content":    s["signal_content"],
            "source_count":      s["source_count"],
            "source_message_ids": src_ids or None,
            "confidence":        s["confidence"],
            "semester_hint":     s.get("semester_hint"),
            "is_current_semester": current,
            "model":             EXTRACT_MODEL,
        }

        if dry_run:
            print(f"    [DRY RUN] {s['signal_type']:20s} | {s.get('course_code','—'):8s} | "
                  f"conf={s['confidence']:.2f} | {s['signal_content'][:70]}")
            existing_fps.add(fp)
            inserted += 1
            continue

        r = await http.post(
            f"{SUPABASE_URL}/rest/v1/message_signals",
            headers=HEADERS,
            json=row,
        )
        if r.status_code < 300:
            existing_fps.add(fp)
            inserted += 1
        else:
            log("INSERT_ERROR", status=r.status_code, error=r.text[:120])

    return inserted


async def process_chat(
    http: httpx.AsyncClient,
    ai: AsyncOpenAI,
    chat_name: str,
    windows_limit: int | None,
    dry_run: bool,
) -> tuple[int, float]:
    total_count = await count_messages(http, chat_name)
    if total_count < MIN_MESSAGES:
        log("CHAT_SKIP", chat=chat_name, messages=total_count, reason="too few messages")
        return 0, 0.0

    start_offset  = await get_resume_offset(http, chat_name)
    existing_fps  = await fetch_existing_fingerprints(http, chat_name)

    log("CHAT_START", chat=chat_name, messages=total_count,
        resume_offset=start_offset, existing_signals=len(existing_fps))

    offset         = start_offset
    total_signals  = 0
    total_cost     = 0.0
    windows_done   = 0

    while offset < total_count:
        if windows_limit and windows_done >= windows_limit:
            break

        messages = await fetch_message_window(http, chat_name, offset, WINDOW_SIZE)
        if not messages:
            break

        if len(messages) < MIN_MESSAGES:
            offset += max(1, len(messages))
            continue

        try:
            signals, cost = await extract_signals(ai, messages, chat_name)
        except Exception as exc:
            log("EXTRACT_ERROR", chat=chat_name, offset=offset, error=str(exc)[:120])
            offset += WINDOW_SIZE - WINDOW_OVERLAP
            continue

        total_cost += cost
        new_sigs = await insert_signals(http, chat_name, signals, messages, existing_fps, dry_run)
        total_signals += new_sigs

        windows_done += 1
        offset += WINDOW_SIZE - WINDOW_OVERLAP

        log("WINDOW_DONE", chat=chat_name[:20], offset=offset, total=total_count,
            signals_new=new_sigs, signals_total=total_signals,
            cost_usd=f"{total_cost:.4f}")

        if not dry_run and windows_done % 10 == 0:
            await save_progress(http, chat_name, offset, total_signals, total_cost)

        await asyncio.sleep(BATCH_DELAY_S)

    if not dry_run:
        await save_progress(http, chat_name, offset, total_signals, total_cost)

    log("CHAT_DONE", chat=chat_name, signals=total_signals,
        windows=windows_done, cost_usd=f"{total_cost:.4f}")
    return total_signals, total_cost


async def main():
    parser = argparse.ArgumentParser(description="Extract message intelligence signals")
    parser.add_argument("--dry-run",      action="store_true", help="No DB writes, stdout only")
    parser.add_argument("--chat",         type=str, default=None, help="Process only one chat")
    parser.add_argument("--limit",        type=int, default=None, help="Max windows per chat")
    parser.add_argument("--check-schema", action="store_true", help="Verify tables, then exit")
    args = parser.parse_args()

    async with httpx.AsyncClient(timeout=30) as http:
        if args.check_schema:
            ok = await check_schema(http)
            sys.exit(0 if ok else 1)

        ai = AsyncOpenAI(api_key=OPENAI_API_KEY)
        chats = await fetch_chat_names(http, args.chat)

        if not chats:
            log("NO_CHATS_FOUND")
            sys.exit(1)

        log("SIGNAL_MINER_START", chats=len(chats), model=EXTRACT_MODEL,
            window_size=WINDOW_SIZE, current_semester_days=CURRENT_SEMESTER_WINDOW_DAYS,
            dry_run=args.dry_run)

        grand_total_signals = 0
        grand_total_cost    = 0.0

        for chat_name in chats:
            signals, cost = await process_chat(
                http, ai, chat_name,
                windows_limit=args.limit,
                dry_run=args.dry_run,
            )
            grand_total_signals += signals
            grand_total_cost    += cost

        print(f"\n{'='*60}")
        print(f"Total signals extracted: {grand_total_signals}")
        print(f"Total cost:              ${grand_total_cost:.4f}")
        print(f"Model:                   {EXTRACT_MODEL}")
        if args.dry_run:
            print("[DRY RUN] No writes performed.")


if __name__ == "__main__":
    asyncio.run(main())
