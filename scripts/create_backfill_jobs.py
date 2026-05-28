#!/usr/bin/env python3
"""
create_backfill_jobs.py — Score known chats and queue them for backfill.

Queries the messages table for all distinct chats already seen by the listener,
scores each by academic value, and inserts telegram_backfill_jobs rows.

No Telethon connection needed — works while the Railway listener is running.

Scoring:
  +30  name contains a course code (CS350, MGT311, …)
  +20  name contains academic keywords (exam, quiz, midterm, final, …)
  +15  chat_type is channel or megagroup
  +10  >10% of sampled messages have media (PDF/photo-rich)
  -10  chat_type is private (DM, no academic signal)

Usage:
    python3 scripts/create_backfill_jobs.py               # dry-run preview
    python3 scripts/create_backfill_jobs.py --commit      # create jobs
    python3 scripts/create_backfill_jobs.py --min-score 30 --commit
    python3 scripts/create_backfill_jobs.py --all --commit  # queue everything

Requires: SUPABASE_URL, SUPABASE_KEY env vars.
"""

import os
import re
import sys
import asyncio
import argparse

import httpx
from dotenv import load_dotenv

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


def log(event: str, **kwargs):
    parts = [event] + [f"{k}={v}" for k, v in kwargs.items()]
    print(" | ".join(parts), flush=True)


def score_chat(name: str, chat_type: str, msg_count: int, media_count: int) -> tuple[int, list[str]]:
    score = 0
    reasons = []

    if COURSE_CODE_RE.search(name):
        score += 30
        reasons.append("course_code_in_name")

    if ACADEMIC_KEYWORDS.search(name):
        score += 20
        reasons.append("academic_keyword_in_name")

    if chat_type in ("channel", "megagroup"):
        score += 15
        reasons.append(chat_type)
    elif chat_type == "private":
        score -= 10
        reasons.append("private_dm")

    if msg_count > 0 and media_count / msg_count > 0.10:
        score += 10
        reasons.append(f"media_rich={media_count}/{msg_count}")

    return score, reasons


async def get_all_chats(http: httpx.AsyncClient) -> list[dict]:
    """Aggregate distinct chats from messages table with message + media counts."""
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/messages",
        headers=HEADERS,
        params={
            "select": "platform_chat_id,chat_name,telegram_chat_type,has_media",
            "limit": "10000",
            "order": "created_at.desc",
        },
    )
    if r.status_code >= 400:
        log("MESSAGES_FETCH_ERROR", status=r.status_code, error=r.text[:120])
        return []

    rows = r.json()

    # Aggregate by chat_id
    chats: dict[str, dict] = {}
    for row in rows:
        cid = row.get("platform_chat_id") or ""
        if not cid:
            continue
        if cid not in chats:
            chats[cid] = {
                "platform_chat_id": cid,
                "chat_name": row.get("chat_name") or "",
                "chat_type": row.get("telegram_chat_type") or "unknown",
                "msg_count": 0,
                "media_count": 0,
            }
        chats[cid]["msg_count"] += 1
        if row.get("has_media"):
            chats[cid]["media_count"] += 1

    return list(chats.values())


async def get_existing_job_chat_ids(http: httpx.AsyncClient) -> set[str]:
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/telegram_backfill_jobs",
        headers=HEADERS,
        params={"select": "platform_chat_id", "limit": "1000"},
    )
    if r.status_code >= 400:
        return set()
    return {row["platform_chat_id"] for row in r.json()}


async def create_backfill_job(http: httpx.AsyncClient, chat: dict, score: int) -> str:
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/telegram_backfill_jobs",
        headers=HEADERS,
        json={
            "platform_chat_id": chat["platform_chat_id"],
            "chat_name": chat["chat_name"],
            "chat_type": chat["chat_type"],
            "status": "pending",
            "priority": score,
            "batch_size": 500,
            "retry_count": 0,
            "metadata": {
                "score": score,
                "msg_count_at_creation": chat["msg_count"],
                "created_by": "create_backfill_jobs.py",
            },
        },
    )
    if r.status_code == 409:
        return "duplicate"
    if r.status_code >= 400:
        log("JOB_CREATE_ERROR", chat=chat["chat_name"],
            status=r.status_code, error=r.text[:120])
        return "error"
    return "created"


async def main():
    parser = argparse.ArgumentParser(description="Queue Telegram chats for backfill")
    parser.add_argument("--commit", action="store_true",
                        help="Write jobs to DB (default: dry-run)")
    parser.add_argument("--min-score", type=int, default=DEFAULT_MIN_SCORE,
                        help=f"Minimum score to queue (default: {DEFAULT_MIN_SCORE})")
    parser.add_argument("--all", action="store_true",
                        help="Queue all chats regardless of score")
    args = parser.parse_args()

    async with httpx.AsyncClient(timeout=60) as http:
        log("FETCHING_CHATS")
        chats = await get_all_chats(http)

        if not chats:
            print("No chats found in messages table. Run the listener first.", file=sys.stderr)
            sys.exit(1)

        log("CHATS_FOUND", count=len(chats))

        existing = await get_existing_job_chat_ids(http)
        log("EXISTING_JOBS", count=len(existing))

        # Score and sort
        scored = []
        for chat in chats:
            score, reasons = score_chat(
                chat["chat_name"], chat["chat_type"],
                chat["msg_count"], chat["media_count"],
            )
            scored.append((score, chat, reasons))

        scored.sort(key=lambda x: x[0], reverse=True)

        threshold = 0 if args.all else args.min_score
        queued = 0
        skipped_score = 0
        skipped_existing = 0
        errors = 0

        print()
        print(f"{'SCORE':>6}  {'TYPE':<12}  {'MSGS':>5}  {'MEDIA':>5}  NAME")
        print("-" * 72)

        for score, chat, reasons in scored:
            cid = chat["platform_chat_id"]
            name = chat["chat_name"] or "(unnamed)"
            ct = chat["chat_type"]
            msgs = chat["msg_count"]
            media = chat["media_count"]
            reason_str = ", ".join(reasons)

            if cid in existing:
                skipped_existing += 1
                marker = "  (already queued)"
            elif score < threshold:
                skipped_score += 1
                marker = "  (below threshold)"
            elif args.commit:
                result = await create_backfill_job(http, chat, score)
                if result == "created":
                    queued += 1
                    marker = "  ✓ queued"
                elif result == "duplicate":
                    skipped_existing += 1
                    marker = "  (already queued)"
                else:
                    errors += 1
                    marker = "  ✗ error"
            else:
                queued += 1
                marker = "  [dry-run]"

            print(f"{score:>6}  {ct:<12}  {msgs:>5}  {media:>5}  {name}{marker}")
            if reason_str:
                print(f"{'':>6}  {'':12}  {'':>5}  {'':>5}  → {reason_str}")

        print()
        log(
            "SUMMARY",
            mode="commit" if args.commit else "dry-run",
            threshold=threshold,
            queued=queued,
            skipped_existing=skipped_existing,
            skipped_score=skipped_score,
            errors=errors,
        )

        if not args.commit and queued > 0:
            print(f"\nRun with --commit to create {queued} backfill job(s).")


if __name__ == "__main__":
    asyncio.run(main())
