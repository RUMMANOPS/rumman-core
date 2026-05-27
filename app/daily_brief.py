#!/usr/bin/env python3
"""
Daily Brief Worker — extracts operational intelligence from recent messages.

Usage:
    python3 app/daily_brief.py                        # last 24 hours, all chats
    python3 app/daily_brief.py --hours 72             # last 3 days
    python3 app/daily_brief.py --chat "ChatName"      # one chat only
    python3 app/daily_brief.py --dry-run              # no DB writes, stdout only
    python3 app/daily_brief.py --check-schema         # verify tables exist then exit

Requires: supabase/migrations/001_daily_brief_tables.sql applied first.
Not in Procfile — run on demand. See docs/03-workflows/railway-processes.md.
"""

import os
import json
import argparse
import asyncio
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI
from typing import Optional

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

WORKER = "daily_brief_v1"
PROMPT_VERSION = "v1"
MODEL = "gpt-4o-mini"
CONFIDENCE_THRESHOLD = 0.65
MIN_MESSAGES_PER_CHAT = 2

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

# Extraction prompt — see docs/05-agents-prompts/prompt-registry.md for versioning notes
SYSTEM_PROMPT = """\
You are RUMMAN, an operational intelligence assistant for a university student in Saudi Arabia.

Extract ONLY items with clear operational value from the provided Telegram messages.

Item types:
- task: assignment, quiz preparation, deliverable, action item
- deadline: exam date, quiz window, submission deadline, registration deadline
- decision: official announcement affecting students (exams moved online, schedule change, cancellation)
- risk: warning, missed item, something at risk of being overlooked
- follow_up: something that needs checking on or following up

Rules:
1. Conservative: when in doubt, omit it.
2. Skip: greetings, religious text, social chat, motivational content, reactions.
3. Course codes (IT484, CS001, STAT201, etc.) signal academic importance.
4. Confidence scoring — 0.9+: explicitly stated with full details; 0.7–0.9: clearly implied; below 0.7: omit.
5. An empty items array is a valid and often correct answer — never fabricate items.
6. Write content in the language of the source message (Arabic if Arabic, English if English).
7. due_date: ISO format YYYY-MM-DD only if a specific date is clearly stated, otherwise null.
8. course_code: extract the code if present (e.g. "IT484"), otherwise null.

REQUIRED output schema — every item must have ALL of these exact fields:
{
  "context_summary": "one sentence describing this chat window",
  "items": [
    {
      "type": "task|deadline|decision|risk|follow_up",
      "content": "exact text description of the item",
      "confidence": 0.85,
      "source_indices": [0, 2],
      "due_date": "2026-05-30 or null",
      "course_code": "IT484 or null"
    }
  ]
}

Field rules:
- "content" (NOT "description"): required string, never omit
- "confidence": required float 0.0–1.0, never omit — use 0.75 if unsure
- "source_indices": list of message indices from the input, never omit

Return ONLY a valid JSON object matching this schema. No text, explanation, or markdown outside the JSON.\
"""

USER_PROMPT_TEMPLATE = """\
Messages from Telegram chat "{chat_name}" — {count} messages, window: {window}

{messages}

Extract operational items from these messages.\
"""

TYPE_LABELS = {
    "task": "Tasks / مهام",
    "deadline": "Deadlines / مواعيد",
    "decision": "Decisions / قرارات",
    "risk": "Risks / مخاطر",
    "follow_up": "Follow-ups / متابعات",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(event: str, **kwargs):
    parts = [event] + [f"{k}={v}" for k, v in kwargs.items()]
    print(" | ".join(parts), flush=True)


async def check_schema(http: httpx.AsyncClient) -> bool:
    ok = True
    for table in ["brief_runs", "extracted_items"]:
        r = await http.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=HEADERS,
            params=[("limit", "0")],
        )
        if r.status_code == 404:
            log("SCHEMA_MISSING", table=table)
            ok = False
        else:
            log("SCHEMA_OK", table=table, status=r.status_code)
    return ok


async def fetch_messages(
    http: httpx.AsyncClient,
    window_start: datetime,
    window_end: datetime,
    chat_name: Optional[str],
) -> list[dict]:
    params = [
        ("select", "id,chat_name,platform_chat_id,sender_name,message_text,message_date"),
        ("message_type", "eq.text"),
        ("message_date", f"gte.{window_start.isoformat()}"),
        ("message_date", f"lte.{window_end.isoformat()}"),
        ("order", "message_date.asc"),
        ("limit", "2000"),
    ]
    if chat_name:
        params.append(("chat_name", f"eq.{chat_name}"))

    r = await http.get(f"{SUPABASE_URL}/rest/v1/messages", headers=HEADERS, params=params)
    if r.status_code >= 400:
        log("FETCH_ERROR", status=r.status_code, error=r.text[:120])
        return []

    return [
        m for m in r.json()
        if m.get("message_text", "").strip() and m.get("chat_name")
    ]


async def create_brief_run(http: httpx.AsyncClient, payload: dict) -> Optional[str]:
    r = await http.post(f"{SUPABASE_URL}/rest/v1/brief_runs", headers=HEADERS, json=payload)
    if r.status_code >= 400:
        log("BRIEF_RUN_CREATE_ERROR", status=r.status_code, error=r.text[:200])
        return None
    rows = r.json()
    return rows[0]["id"] if rows else None


async def update_brief_run(http: httpx.AsyncClient, run_id: str, update: dict):
    r = await http.patch(
        f"{SUPABASE_URL}/rest/v1/brief_runs",
        headers=HEADERS,
        params=[("id", f"eq.{run_id}")],
        json=update,
    )
    if r.status_code >= 400:
        log("BRIEF_RUN_UPDATE_ERROR", run_id=run_id, status=r.status_code)


async def store_items(
    http: httpx.AsyncClient,
    items: list[dict],
    chat_name: str,
    message_ids: list[str],
    run_id: str,
) -> int:
    rows = []
    for item in items:
        if item.get("confidence", 0) < CONFIDENCE_THRESHOLD:
            continue

        indices = item.get("source_indices", [])
        item_source_ids = [
            message_ids[i] for i in indices if 0 <= i < len(message_ids)
        ] or message_ids[:5]

        content = item.get("content") or item.get("description", "")
        if not content:
            continue

        rows.append({
            "tenant_id": "default",
            "item_type": item["type"],
            "content": content,
            "due_date": item.get("due_date"),
            "course_code": item.get("course_code"),
            "validity_status": "machine_asserted",
            "confidence": float(item["confidence"]),
            "chat_name": chat_name,
            "source_message_ids": item_source_ids,
            "brief_run_id": run_id,
        })

    if not rows:
        return 0

    r = await http.post(f"{SUPABASE_URL}/rest/v1/extracted_items", headers=HEADERS, json=rows)
    if r.status_code >= 400:
        log("ITEMS_STORE_ERROR", status=r.status_code, error=r.text[:200])
        return 0
    return len(r.json())


def format_messages_for_prompt(messages: list[dict]) -> str:
    lines = []
    for i, m in enumerate(messages):
        sender = m.get("sender_name") or "—"
        text = (m.get("message_text") or "").strip()
        date = str(m.get("message_date") or "")[:10]
        lines.append(f"[{i}] {sender} ({date}): {text}")
    return "\n".join(lines)


def format_brief(results: list[dict]) -> str:
    lines = [
        "",
        "━" * 56,
        "  RUMMAN DAILY BRIEF",
        "━" * 56,
        "",
    ]

    all_items = [item for r in results for item in r.get("items", [])]
    if not all_items:
        lines += ["  No operational items found in this window.", ""]
        lines.append("━" * 56)
        return "\n".join(lines)

    for result in results:
        items = result.get("items", [])
        if not items:
            continue

        lines.append(f"  ▸ {result['chat_name']}")
        if result.get("context_summary"):
            lines.append(f"    {result['context_summary']}")
        lines.append("")

        by_type: dict[str, list] = defaultdict(list)
        for item in items:
            by_type[item["type"]].append(item)

        for itype, label in TYPE_LABELS.items():
            if itype not in by_type:
                continue
            lines.append(f"    {label}")
            for item in by_type[itype]:
                parts = [f"    • {item['content']}"]
                if item.get("course_code"):
                    parts.append(f"[{item['course_code']}]")
                if item.get("due_date"):
                    parts.append(f"→ {item['due_date']}")
                parts.append(f"({int(item['confidence'] * 100)}%)")
                lines.append(" ".join(parts))
        lines.append("")

    lines.append("━" * 56)
    return "\n".join(lines)


async def process_chat(
    http: httpx.AsyncClient,
    ai: AsyncOpenAI,
    chat_name: str,
    messages: list[dict],
    window_start: datetime,
    window_end: datetime,
    dry_run: bool,
) -> dict:
    log("CHAT_START", chat=chat_name, messages=len(messages))

    message_ids = [m["id"] for m in messages]
    window_str = f"{window_start.strftime('%Y-%m-%d')} to {window_end.strftime('%Y-%m-%d')}"
    formatted = format_messages_for_prompt(messages)

    user_content = USER_PROMPT_TEMPLATE.format(
        chat_name=chat_name,
        count=len(messages),
        window=window_str,
        messages=formatted,
    )

    run_id = None
    if not dry_run:
        run_id = await create_brief_run(http, {
            "tenant_id": "default",
            "worker": WORKER,
            "prompt_version": PROMPT_VERSION,
            "model": MODEL,
            "chat_name": chat_name,
            "platform_chat_id": messages[0].get("platform_chat_id"),
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "message_count": len(messages),
            "source_message_ids": message_ids,
            "status": "running",
            "started_at": utc_now(),
        })
        if not run_id:
            return {"chat_name": chat_name, "items": [], "error": "run_create_failed"}

    try:
        response = await ai.chat.completions.create(
            model=MODEL,
            temperature=0.1,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )

        raw_output = response.choices[0].message.content
        parsed = json.loads(raw_output)

        items = parsed.get("items", [])
        # Normalize: inject default confidence if model omits it (prompt violation, not a valid omission)
        for item in items:
            if "confidence" not in item:
                item["confidence"] = 0.75
        summary = parsed.get("context_summary", "")
        above_threshold = [i for i in items if i.get("confidence", 0) >= CONFIDENCE_THRESHOLD]

        input_tokens = response.usage.prompt_tokens
        output_tokens = response.usage.completion_tokens
        # gpt-4o-mini pricing: $0.15/1M input, $0.60/1M output
        cost_usd = (input_tokens * 0.000_000_150) + (output_tokens * 0.000_000_600)

        log(
            "CHAT_DONE",
            chat=chat_name,
            extracted=len(above_threshold),
            raw_items=len(items),
            tokens=f"{input_tokens}+{output_tokens}",
            cost_usd=f"${cost_usd:.4f}",
        )

        if not dry_run and run_id:
            stored = await store_items(http, items, chat_name, message_ids, run_id)
            await update_brief_run(http, run_id, {
                "status": "completed",
                "raw_output": parsed,
                "context_summary": summary,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost_usd,
                "completed_at": utc_now(),
            })
            log("ITEMS_STORED", chat=chat_name, count=stored, run_id=run_id)

        return {
            "chat_name": chat_name,
            "context_summary": summary,
            "items": above_threshold,
        }

    except Exception as e:
        log("CHAT_ERROR", chat=chat_name, error=str(e))
        if not dry_run and run_id:
            await update_brief_run(http, run_id, {
                "status": "failed",
                "error": str(e),
                "completed_at": utc_now(),
            })
        return {"chat_name": chat_name, "items": [], "error": str(e)}


async def main():
    parser = argparse.ArgumentParser(description="RUMMAN Daily Brief")
    parser.add_argument("--hours", type=int, default=24,
                        help="Lookback window in hours (default: 24; use 168 for 7 days)")
    parser.add_argument("--chat", type=str, default=None,
                        help="Process a specific chat by name only")
    parser.add_argument("--dry-run", action="store_true",
                        help="No DB writes — print brief to stdout only")
    parser.add_argument("--check-schema", action="store_true",
                        help="Verify required tables exist then exit")
    args = parser.parse_args()

    log("DAILY_BRIEF_START", hours=args.hours, chat=args.chat or "all", dry_run=args.dry_run)

    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(hours=args.hours)

    ai = AsyncOpenAI(api_key=OPENAI_API_KEY)

    async with httpx.AsyncClient(timeout=60) as http:
        if args.check_schema:
            await check_schema(http)
            return

        if not args.dry_run:
            if not await check_schema(http):
                log("ABORT", reason="Run supabase/migrations/001_daily_brief_tables.sql first")
                return

        messages = await fetch_messages(http, window_start, window_end, args.chat)
        log("MESSAGES_FETCHED", count=len(messages))

        if not messages:
            log("NO_MESSAGES", hint="Try --hours 168 for 7 days of history")
            return

        by_chat: dict[str, list[dict]] = defaultdict(list)
        for m in messages:
            by_chat[m["chat_name"]].append(m)

        log("CHATS_FOUND", count=len(by_chat), chats=list(by_chat.keys()))

        results = []
        for chat_name, chat_messages in sorted(by_chat.items()):
            if len(chat_messages) < MIN_MESSAGES_PER_CHAT:
                log("CHAT_SKIPPED", chat=chat_name, messages=len(chat_messages), reason="below_minimum")
                continue
            result = await process_chat(
                http, ai, chat_name, chat_messages,
                window_start, window_end, args.dry_run,
            )
            results.append(result)

        print(format_brief(results))

        total_items = sum(len(r.get("items", [])) for r in results)
        log("DAILY_BRIEF_DONE", chats_processed=len(results), total_items=total_items)


if __name__ == "__main__":
    asyncio.run(main())
