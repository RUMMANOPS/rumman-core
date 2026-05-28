#!/usr/bin/env python3
"""
create_backfill_jobs.py — Score Telegram dialogs and queue high-value ones for backfill.

Connects to Telegram via Telethon, lists all dialogs, scores each by academic value,
and inserts telegram_backfill_jobs rows for the ones worth processing.

Scoring:
  +30  name contains a course code (CS350, MGT311, …)
  +20  name contains academic keywords (exam, quiz, midterm, final, …)
  +15  dialog is a channel or megagroup (broadcast archive, not personal DM)
  +10  recent messages include PDFs or photos (media-rich)
  -20  dialog is a bot
  -10  dialog is a private DM (no academic signal)

Usage:
    python3 scripts/create_backfill_jobs.py               # dry-run preview (no DB writes)
    python3 scripts/create_backfill_jobs.py --commit      # actually create jobs
    python3 scripts/create_backfill_jobs.py --min-score 30 --commit
    python3 scripts/create_backfill_jobs.py --all --commit  # include score=0 dialogs

Requires: TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION_STRING,
          SUPABASE_URL, SUPABASE_KEY env vars.
"""

import os
import re
import sys
import asyncio
import argparse
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import (
    Channel, Chat, User,
    InputPeerChannel, InputPeerChat,
)

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

COURSE_CODE_RE = re.compile(r'\b[A-Z]{2,6}\s*\d{3,4}\b', re.IGNORECASE)

ACADEMIC_KEYWORDS = re.compile(
    r'\b(exam|quiz|midterm|final|assignment|lecture|course|subject|university|'
    r'اختبار|نهائي|منتصف|كويز|محاضرة|مادة|جامعة|تخصص|فصل|semester|study|مذاكرة)\b',
    re.IGNORECASE,
)

DEFAULT_MIN_SCORE = 20
PREVIEW_MESSAGES = 10  # how many recent messages to sample when scoring


def log(event: str, **kwargs):
    parts = [event] + [f"{k}={v}" for k, v in kwargs.items()]
    print(" | ".join(parts), flush=True)


def chat_type(dialog) -> str:
    entity = dialog.entity
    if isinstance(entity, Channel):
        return "channel" if entity.broadcast else "megagroup"
    if isinstance(entity, Chat):
        return "group"
    if isinstance(entity, User):
        return "bot" if entity.bot else "private"
    return "unknown"


async def score_dialog(client: TelegramClient, dialog) -> tuple[int, dict]:
    name = dialog.name or ""
    ct = chat_type(dialog)
    score = 0
    reasons = []

    if ct == "bot":
        return -100, {"skip": "bot"}
    if ct == "private":
        score -= 10
        reasons.append("private_dm")

    if COURSE_CODE_RE.search(name):
        score += 30
        reasons.append("course_code_in_name")

    if ACADEMIC_KEYWORDS.search(name):
        score += 20
        reasons.append("academic_keyword_in_name")

    if ct in ("channel", "megagroup"):
        score += 15
        reasons.append(ct)

    # Sample recent messages for media signals
    try:
        pdf_count = 0
        photo_count = 0
        async for msg in client.iter_messages(dialog, limit=PREVIEW_MESSAGES):
            if msg.document:
                f = getattr(msg, "file", None)
                mime = getattr(f, "mime_type", "") or ""
                name_f = getattr(f, "name", "") or ""
                if "pdf" in mime.lower() or name_f.lower().endswith(".pdf"):
                    pdf_count += 1
            if msg.photo:
                photo_count += 1
        if pdf_count > 0:
            score += 10
            reasons.append(f"pdfs={pdf_count}")
        if photo_count > 0:
            score += 5
            reasons.append(f"photos={photo_count}")
    except Exception:
        pass

    return score, {"reasons": reasons, "chat_type": ct}


async def get_existing_job_chat_ids(http: httpx.AsyncClient) -> set[str]:
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/telegram_backfill_jobs",
        headers=HEADERS,
        params={"select": "platform_chat_id", "limit": "1000"},
    )
    if r.status_code >= 400:
        return set()
    return {row["platform_chat_id"] for row in r.json()}


async def create_backfill_job(http: httpx.AsyncClient, dialog, ct: str, score: int) -> str:
    entity = dialog.entity
    chat_id = str(dialog.id if not hasattr(entity, 'id') else entity.id)

    # Telethon uses negative IDs for channels internally; normalize to positive
    if chat_id.startswith("-100"):
        chat_id = chat_id[4:]
    elif chat_id.startswith("-"):
        chat_id = chat_id[1:]

    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/telegram_backfill_jobs",
        headers=HEADERS,
        json={
            "platform_chat_id": chat_id,
            "chat_name": dialog.name or "",
            "chat_type": ct,
            "status": "pending",
            "priority": score,
            "batch_size": 500,
            "retry_count": 0,
            "metadata": {"score": score, "created_by": "create_backfill_jobs.py"},
        },
    )
    if r.status_code == 409:
        return "duplicate"
    if r.status_code >= 400:
        log("JOB_CREATE_ERROR", chat=dialog.name, status=r.status_code, error=r.text[:120])
        return "error"
    return "created"


async def main():
    parser = argparse.ArgumentParser(description="Queue Telegram dialogs for backfill")
    parser.add_argument("--commit", action="store_true", help="Write to DB (default: dry-run)")
    parser.add_argument("--min-score", type=int, default=DEFAULT_MIN_SCORE,
                        help=f"Minimum score to queue (default: {DEFAULT_MIN_SCORE})")
    parser.add_argument("--all", action="store_true",
                        help="Queue all dialogs regardless of score")
    parser.add_argument("--limit", type=int, default=200,
                        help="Max dialogs to inspect (default: 200)")
    args = parser.parse_args()

    client = TelegramClient(
        StringSession(os.environ["TELEGRAM_SESSION_STRING"]),
        int(os.environ["TELEGRAM_API_ID"]),
        os.environ["TELEGRAM_API_HASH"],
    )

    await client.start()
    me = await client.get_me()
    log("LOGGED_IN", user_id=me.id, username=me.username)

    async with httpx.AsyncClient(timeout=60) as http:
        existing = await get_existing_job_chat_ids(http) if args.commit else set()
        log("EXISTING_JOBS", count=len(existing))

        candidates = []
        skipped_bots = 0

        log("SCANNING_DIALOGS", limit=args.limit)
        count = 0
        async for dialog in client.iter_dialogs(limit=args.limit):
            count += 1
            score, meta = await score_dialog(client, dialog)

            if score == -100:
                skipped_bots += 1
                continue

            ct = meta.get("chat_type", "unknown")
            candidates.append((score, dialog, ct, meta))

        candidates.sort(key=lambda x: x[0], reverse=True)

        log("SCAN_DONE", total=count, scoreable=len(candidates), bots_skipped=skipped_bots)

        threshold = 0 if args.all else args.min_score
        queued = 0
        skipped_score = 0
        skipped_existing = 0

        print()
        print(f"{'SCORE':>6}  {'TYPE':<12}  {'NAME'}")
        print("-" * 60)

        for score, dialog, ct, meta in candidates:
            entity = dialog.entity
            raw_id = str(entity.id if hasattr(entity, 'id') else dialog.id)
            norm_id = raw_id.lstrip("-").lstrip("100") if raw_id.startswith("-100") else raw_id.lstrip("-")

            reasons = ", ".join(meta.get("reasons", []))
            marker = ""

            if score < threshold:
                skipped_score += 1
                marker = "  (below threshold)"
            elif norm_id in existing or raw_id in existing:
                skipped_existing += 1
                marker = "  (already queued)"
            elif args.commit:
                result = await create_backfill_job(http, dialog, ct, score)
                if result == "created":
                    queued += 1
                    marker = "  ✓ queued"
                elif result == "duplicate":
                    skipped_existing += 1
                    marker = "  (already queued)"
                else:
                    marker = "  ✗ error"
            else:
                marker = "  [dry-run]"
                queued += 1

            print(f"{score:>6}  {ct:<12}  {dialog.name or '(unnamed)'}{marker}")
            if reasons:
                print(f"{'':>6}  {'':12}  → {reasons}")

        print()
        log(
            "SUMMARY",
            mode="commit" if args.commit else "dry-run",
            threshold=threshold,
            queued=queued,
            skipped_score=skipped_score,
            skipped_existing=skipped_existing,
        )

        if not args.commit and queued > 0:
            print()
            print(f"Run with --commit to create {queued} backfill job(s).")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
