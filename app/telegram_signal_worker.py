#!/usr/bin/env python3
"""
telegram_signal_worker.py — Extract typed academic signals from Telegram messages.

WHY:
  2M+ raw Telegram messages are a data asset that cannot yet be queried.
  This worker converts them into typed academic signals (topic_mention,
  confusion, exam_emphasis, answer_sharing) stored in telegram_signals.

COURSE CODE ATTRIBUTION:
  SEU Telegram groups are organised by college/level, NOT by course code.
  Chat names look like "SEU | Level 3 | قانون", not "IT353".
  Course codes appear INSIDE the message text when students write
  "ما فاهم chapter 4 في IT353" or "وين تجميعات MGT425".
  The model extracts course_code from message content.
  Signals with no extractable course_code are dropped (guardrail).

PILOT MODE (always start here):
  TELEGRAM_SIGNAL_PILOT_COURSES=IT353,MGT425,ACCT101
  TELEGRAM_SIGNAL_MAX_MESSAGES=1000          # total across all chats in pilot
  TELEGRAM_SIGNAL_SINCE_DAYS=30
  TELEGRAM_SIGNAL_DRY_RUN=false
  Only signals whose model-extracted course_code is in the whitelist are stored.

FULL BACKFILL:
  Leave PILOT_COURSES empty. Cost ≈ $12 for 2.3M messages. Never run without
  pilot quality approval.

GUARDRAILS (always enforced):
  - Signals with null/empty course_code  → dropped
  - Signals with empty extracted_topic   → dropped
  - Signals with confidence < 0.55       → dropped
  - More than 3 signals per message      → keep top 3 by confidence
  - raw_text                             → capped at 200 chars
  - Pilot mode: course_code not in whitelist → dropped

ENABLE: TELEGRAM_SIGNAL_WORKER_ENABLED=true
"""
from __future__ import annotations

import os
import re
import json
import asyncio
from datetime import date, datetime, timezone, timedelta
from typing import Optional

import httpx
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL   = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY   = os.environ.get("SUPABASE_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
TENANT_ID      = "00000000-0000-0000-0000-000000000001"

_ENABLED     = os.getenv("TELEGRAM_SIGNAL_WORKER_ENABLED", "").strip().lower() == "true"
BATCH_SIZE   = int(os.getenv("TELEGRAM_SIGNAL_BATCH_SIZE", "50"))
SLEEP_IDLE   = int(os.getenv("TELEGRAM_SIGNAL_SLEEP_SECONDS", "300"))
SIGNAL_MODEL = "gpt-4o-mini"

# ---------------------------------------------------------------------------
# Pilot config
# ---------------------------------------------------------------------------

_raw_pilot = os.getenv("TELEGRAM_SIGNAL_PILOT_COURSES", "").strip()
PILOT_COURSES: frozenset[str] = frozenset(
    c.strip().upper() for c in _raw_pilot.split(",") if c.strip()
) if _raw_pilot else frozenset()

MAX_MESSAGES = int(os.getenv("TELEGRAM_SIGNAL_MAX_MESSAGES", "0"))   # total cap, 0=unlimited
SINCE_DAYS   = int(os.getenv("TELEGRAM_SIGNAL_SINCE_DAYS", "0"))
DRY_RUN      = os.getenv("TELEGRAM_SIGNAL_DRY_RUN", "").strip().lower() == "true"

# Hard guardrail constants
_MIN_CONFIDENCE      = 0.55
_MAX_SIGNALS_PER_MSG = 3
_MAX_RAW_TEXT        = 200

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

_COURSE_CODE_RE = re.compile(r'\b([A-Z]{2,6}\d{3,4})\b', re.IGNORECASE)

# ---------------------------------------------------------------------------
# GPT prompt — now asks model to extract course_code from message content
# ---------------------------------------------------------------------------

_SIGNAL_SYSTEM = """\
You analyse Saudi university (SEU) Telegram messages to extract academic signals.

INPUT: a JSON array of messages, each with:
  "i"    — message index
  "text" — message text (Arabic, English, or mixed)

EXTRACT signals of these types ONLY:
  topic_mention   — a specific academic concept or topic is named
  confusion       — student expresses confusion or asks for help with a concept
  exam_emphasis   — a topic is flagged as likely on the exam, or professor emphasised it
  answer_sharing  — someone shares an answer, solution, or worked example

For EACH signal you extract, you MUST provide:
  "course_code"     — the SEU course code (e.g. "IT353", "MGT425"). Set to null if truly
                      unidentifiable from the message text. Signals with null course_code
                      are worthless — try hard to find it.
  "signal_type"     — one of the four types above
  "extracted_topic" — the specific academic concept (e.g. "SQL Joins", "نظرية ناش").
                      NOT vague labels like "المادة", "الفصل", "الموضوع".
  "confidence"      — 0.55–1.0. Omit signals below 0.55.
  "snippet"         — the 100-char excerpt from the message that triggered the signal

STRICT RULES:
  - Most messages (>70%) produce NO signal — skip social chat, greetings, logistics
  - One signal type per message (pick the strongest)
  - extracted_topic must name a real academic concept, not a placeholder
  - course_code must be an SEU code pattern like IT353, MGT425, CS241, FIN101, ACCT101

Respond ONLY with a JSON array ([] if no signals):
[
  {
    "message_index": 3,
    "course_code": "IT353",
    "signal_type": "confusion",
    "extracted_topic": "Use Case Diagrams",
    "confidence": 0.82,
    "snippet": "ما فاهم Use Case Diagram في chapter 5 IT353 كيف ارسمه"
  }
]"""


def log(event: str, **kwargs):
    parts = [event] + [f"{k}={v}" for k, v in kwargs.items()]
    print(" | ".join(parts), flush=True)


def _week_of(dt: Optional[str]) -> date:
    if dt:
        try:
            d = datetime.fromisoformat(dt.replace("Z", "+00:00")).date()
            return d - timedelta(days=d.weekday())
        except Exception:
            pass
    today = date.today()
    return today - timedelta(days=today.weekday())


def _since_cutoff() -> Optional[str]:
    if not SINCE_DAYS:
        return None
    return (datetime.now(timezone.utc) - timedelta(days=SINCE_DAYS)).isoformat()


# ---------------------------------------------------------------------------
# Chat discovery — returns ALL chats with messages in the time window
# ---------------------------------------------------------------------------

async def _discover_chats(http: httpx.AsyncClient) -> tuple[list[dict], dict]:
    """
    Return all distinct chats that have text messages (in the time window if
    SINCE_DAYS is set). Does NOT filter by course code — course attribution
    happens at the signal level via GPT.

    Returns (chats, skip_log).
    """
    # No time filter here — sent_at is empty for many rows, any gte filter
    # would drop them at discovery time. Time filtering via since_days is
    # applied per-chat inside _fetch_messages() using the cursor's last_sent_at.
    params: dict = {
        "select":       "platform_chat_id,chat_name",
        "message_type": "eq.text",
        "message_text": "not.is.null",
        "limit":        "5000",
    }
    resp = await http.get(f"{SUPABASE_URL}/rest/v1/messages", headers=HEADERS, params=params)
    if resp.status_code != 200:
        return [], {}

    seen: dict[str, dict] = {}
    for row in resp.json():
        cid = row.get("platform_chat_id")
        if cid and cid not in seen:
            seen[cid] = {
                "platform_chat_id": cid,
                "chat_name":        row.get("chat_name") or "",
            }

    skip_log = {"no_messages_in_window": 0}
    return list(seen.values()), skip_log


# ---------------------------------------------------------------------------
# Cursor
# ---------------------------------------------------------------------------

async def _get_cursor(http: httpx.AsyncClient, platform_chat_id: str) -> Optional[dict]:
    resp = await http.get(
        f"{SUPABASE_URL}/rest/v1/telegram_signal_cursors",
        headers=HEADERS,
        params={"platform_chat_id": f"eq.{platform_chat_id}", "limit": "1"},
    )
    if resp.status_code == 200:
        rows = resp.json()
        return rows[0] if rows else None
    return None


async def _upsert_cursor(
    http: httpx.AsyncClient,
    platform_chat_id: str,
    chat_name: str,
    last_message_id: str,
    last_sent_at: Optional[str],
    processed_count: int,
    signal_count: int,
):
    if DRY_RUN:
        return
    await http.post(
        f"{SUPABASE_URL}/rest/v1/telegram_signal_cursors"
        "?on_conflict=platform_chat_id",
        headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
        content=json.dumps({
            "platform_chat_id": platform_chat_id,
            "chat_name":        chat_name,
            "course_code":      None,   # populated by signals, not at cursor level
            "last_message_id":  last_message_id,
            "last_sent_at":     last_sent_at,
            "processed_count":  processed_count,
            "signal_count":     signal_count,
            "last_run_at":      datetime.now(timezone.utc).isoformat(),
        }),
    )


# ---------------------------------------------------------------------------
# Message fetching
# ---------------------------------------------------------------------------

async def _fetch_messages(
    http: httpx.AsyncClient,
    platform_chat_id: str,
    after_platform_message_id: Optional[str],
    batch_limit: int,
) -> list[dict]:
    # sent_at is empty for most rows (backfilled without timestamps).
    # Use platform_message_id (Telegram's sequential integer) for ordering
    # and cursor-based pagination within a chat.
    params: dict = {
        "select":           "id,message_text,sent_at,platform_message_id",
        "platform_chat_id": f"eq.{platform_chat_id}",
        "message_type":     "eq.text",
        "message_text":     "not.is.null",
        "order":            "platform_message_id.asc",
        "limit":            str(batch_limit),
    }
    if after_platform_message_id:
        params["platform_message_id"] = f"gt.{after_platform_message_id}"

    resp = await http.get(f"{SUPABASE_URL}/rest/v1/messages", headers=HEADERS, params=params)
    if resp.status_code == 200:
        return resp.json()
    return []


# ---------------------------------------------------------------------------
# Signal extraction
# ---------------------------------------------------------------------------

async def _extract_signals(
    client: AsyncOpenAI,
    messages: list[dict],
) -> list[dict]:
    batch = [
        {"i": i, "text": (m.get("message_text") or "")[:300]}
        for i, m in enumerate(messages)
        if (m.get("message_text") or "").strip()
    ]
    if not batch:
        return []

    try:
        resp = await client.chat.completions.create(
            model=SIGNAL_MODEL,
            messages=[
                {"role": "system", "content": _SIGNAL_SYSTEM},
                {"role": "user",   "content":
                    f"Messages:\n{json.dumps(batch, ensure_ascii=False)}"},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
            max_tokens=1024,
        )
        raw    = resp.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed.get("signals") or parsed.get("results") or []
        if isinstance(parsed, list):
            return parsed
        return []
    except Exception as e:
        log("EXTRACT_ERROR", error=str(e)[:100])
        return []


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------

def _apply_guardrails(signals: list[dict]) -> tuple[list[dict], dict]:
    """Apply hard guardrails. Returns (clean, drop_log)."""
    drop_log: dict[str, int] = {
        "null_course_code": 0,
        "pilot_filter":     0,
        "empty_topic":      0,
        "low_confidence":   0,
        "per_msg_cap":      0,
    }

    # Group by message_index
    by_msg: dict[int, list[dict]] = {}
    for sig in signals:
        idx = sig.get("message_index")
        if idx is None:
            continue
        by_msg.setdefault(idx, []).append(sig)

    clean: list[dict] = []
    for idx, sigs in by_msg.items():
        sigs.sort(key=lambda s: float(s.get("confidence", 0)), reverse=True)
        kept = 0
        for sig in sigs:
            code       = (sig.get("course_code") or "").strip().upper() or None
            topic      = (sig.get("extracted_topic") or "").strip()
            confidence = float(sig.get("confidence", 0))

            if not code:
                drop_log["null_course_code"] += 1
                continue
            if PILOT_COURSES and code not in PILOT_COURSES:
                drop_log["pilot_filter"] += 1
                continue
            if not topic:
                drop_log["empty_topic"] += 1
                continue
            if confidence < _MIN_CONFIDENCE:
                drop_log["low_confidence"] += 1
                continue
            if kept >= _MAX_SIGNALS_PER_MSG:
                drop_log["per_msg_cap"] += 1
                continue

            sig["course_code"] = code
            clean.append(sig)
            kept += 1

    return clean, drop_log


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

async def _store_signals(
    http: httpx.AsyncClient,
    signals: list[dict],
    messages: list[dict],
    platform_chat_id: str,
    chat_name: str,
) -> int:
    if not signals:
        return 0

    rows = []
    for sig in signals:
        idx = sig.get("message_index")
        if idx is None or idx >= len(messages):
            continue
        msg = messages[idx]
        rows.append({
            "tenant_id":         TENANT_ID,
            "source_message_id": msg["id"],
            "platform_chat_id":  platform_chat_id,
            "chat_name":         chat_name,
            "course_code":       sig["course_code"],
            "signal_type":       sig.get("signal_type", "topic_mention"),
            "extracted_topic":   sig["extracted_topic"][:200],
            "raw_text":          (sig.get("snippet") or "")[:_MAX_RAW_TEXT],
            "confidence":        round(min(1.0, max(0.0, float(sig.get("confidence", 0.75)))), 3),
            "week_of":           _week_of(msg.get("sent_at")).isoformat(),
            "message_sent_at":   msg.get("sent_at"),
        })

    if not rows:
        return 0

    if DRY_RUN:
        for r in rows[:3]:
            log("DRY_RUN_SIGNAL",
                course=r["course_code"],
                type=r["signal_type"],
                topic=r["extracted_topic"][:50],
                confidence=r["confidence"],
                snippet=r["raw_text"][:80])
        if len(rows) > 3:
            log("DRY_RUN_SIGNAL", note=f"...and {len(rows)-3} more")
        return len(rows)

    resp = await http.post(
        f"{SUPABASE_URL}/rest/v1/telegram_signals"
        "?on_conflict=source_message_id,signal_type",
        headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
        content=json.dumps(rows),
    )
    if resp.status_code in (200, 201, 204):
        return len(rows)
    log("STORE_ERROR", status=resp.status_code, body=resp.text[:150])
    return 0


# ---------------------------------------------------------------------------
# Per-chat processing
# ---------------------------------------------------------------------------

async def process_chat(
    client: AsyncOpenAI,
    http: httpx.AsyncClient,
    chat: dict,
    remaining_budget: int,
) -> dict:
    """Process one batch for one chat. Returns stats dict."""
    platform_chat_id = chat["platform_chat_id"]
    chat_name        = chat["chat_name"]

    cursor       = await _get_cursor(http, platform_chat_id)
    prev_count   = cursor["processed_count"] if cursor else 0
    prev_signals = cursor["signal_count"] if cursor else 0

    # Resolve last platform_message_id from the stored UUID cursor.
    # The migration stores last_message_id (UUID); we need the Telegram
    # sequential int to paginate _fetch_messages correctly.
    last_platform_message_id = None
    if cursor and cursor.get("last_message_id"):
        r = await http.get(
            f"{SUPABASE_URL}/rest/v1/messages",
            headers=HEADERS,
            params={"id": f"eq.{cursor['last_message_id']}", "select": "platform_message_id", "limit": "1"},
        )
        if r.status_code == 200 and r.json():
            last_platform_message_id = r.json()[0].get("platform_message_id")

    batch_limit = min(BATCH_SIZE, remaining_budget) if remaining_budget > 0 else BATCH_SIZE
    messages    = await _fetch_messages(http, platform_chat_id, last_platform_message_id, batch_limit)
    if not messages:
        return {"messages": 0, "raw": 0, "stored": 0, "drops": {}}

    raw_signals             = await _extract_signals(client, messages)
    clean_signals, drop_log = _apply_guardrails(raw_signals)
    stored                  = await _store_signals(
        http, clean_signals, messages, platform_chat_id, chat_name
    )

    await _upsert_cursor(
        http,
        platform_chat_id = platform_chat_id,
        chat_name        = chat_name,
        last_message_id  = messages[-1]["id"],
        last_sent_at     = messages[-1].get("sent_at") or None,
        processed_count  = prev_count + len(messages),
        signal_count     = prev_signals + stored,
    )

    if raw_signals or stored:
        drop_detail = ",".join(f"{k}={v}" for k, v in drop_log.items() if v)
        log("BATCH",
            chat=chat_name[:35],
            msgs=len(messages),
            raw=len(raw_signals),
            clean=len(clean_signals),
            stored=stored,
            drops=f"({drop_detail})" if drop_detail else "0")

    return {
        "messages": len(messages),
        "raw":      len(raw_signals),
        "stored":   stored,
        "drops":    drop_log,
    }


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def main():
    if not _ENABLED:
        print("DISABLED — set TELEGRAM_SIGNAL_WORKER_ENABLED=true", flush=True)
        return

    log("START",
        model=SIGNAL_MODEL,
        batch_size=BATCH_SIZE,
        pilot=",".join(sorted(PILOT_COURSES)) if PILOT_COURSES else "off",
        max_messages=MAX_MESSAGES or "unlimited",
        since_days=SINCE_DAYS or "unlimited",
        dry_run=DRY_RUN)

    client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    async with httpx.AsyncClient(timeout=30) as http:
        total_processed = 0

        while True:
            chats, skip_log = await _discover_chats(http)
            log("DISCOVERY", chats=len(chats), **skip_log)

            if not chats:
                log("IDLE", reason="no_chats", sleep=SLEEP_IDLE)
                await asyncio.sleep(SLEEP_IDLE)
                continue

            cycle_msgs = cycle_raw = cycle_stored = cycle_drops = 0

            for chat in chats:
                remaining = (MAX_MESSAGES - total_processed) if MAX_MESSAGES else 0
                if MAX_MESSAGES and remaining <= 0:
                    log("MAX_MESSAGES_REACHED", total=total_processed)
                    return

                try:
                    stats           = await process_chat(client, http, chat, remaining)
                    cycle_msgs     += stats["messages"]
                    cycle_raw      += stats["raw"]
                    cycle_stored   += stats["stored"]
                    cycle_drops    += sum(stats["drops"].values())
                    total_processed += stats["messages"]
                except Exception as e:
                    log("CHAT_ERROR", chat=chat.get("chat_name", "?")[:35], error=str(e)[:100])

            log("CYCLE_DONE",
                chats=len(chats),
                messages=cycle_msgs,
                raw_signals=cycle_raw,
                stored=cycle_stored,
                dropped=cycle_drops,
                total_processed=total_processed,
                dry_run=DRY_RUN)

            if cycle_msgs == 0:
                await asyncio.sleep(SLEEP_IDLE)


if __name__ == "__main__":
    asyncio.run(main())
