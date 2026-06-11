#!/usr/bin/env python3
"""
telegram_signal_report.py — Quality report for the telegram_signal_worker pilot.

Run after the pilot to decide:
  - Is signal quality high enough to expand?
  - Which signal types are most reliable?
  - Is the course_code extraction accurate?
  - What topics are emerging?

Usage:
    python3 scripts/telegram_signal_report.py
    python3 scripts/telegram_signal_report.py --course IT353
    python3 scripts/telegram_signal_report.py --json > report.json
"""
from __future__ import annotations

import os
import sys
import json
import argparse
import asyncio
from collections import Counter
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
TENANT_ID    = "00000000-0000-0000-0000-000000000001"

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}


async def fetch_all(http: httpx.AsyncClient, url: str, params: dict) -> list[dict]:
    """Paginate through all results."""
    results = []
    offset  = 0
    limit   = 1000
    while True:
        p = {**params, "limit": str(limit), "offset": str(offset)}
        r = await http.get(url, headers=HEADERS, params=p)
        if r.status_code != 200:
            break
        batch = r.json()
        results.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return results


async def build_report(course_filter: Optional[str] = None) -> dict:
    async with httpx.AsyncClient(timeout=30) as http:

        # ── 1. Processing cursors (what was processed) ──────────────────────
        cursor_params: dict = {"select": "platform_chat_id,chat_name,course_code,processed_count,signal_count,last_run_at"}
        if course_filter:
            cursor_params["course_code"] = f"eq.{course_filter.upper()}"
        cursors = await fetch_all(http, f"{SUPABASE_URL}/rest/v1/telegram_signal_cursors", cursor_params)

        total_messages_processed = sum(c.get("processed_count") or 0 for c in cursors)
        total_signals_stored     = sum(c.get("signal_count") or 0 for c in cursors)
        chats_processed          = len(cursors)
        chats_with_course        = sum(1 for c in cursors if c.get("course_code"))
        chats_without_course     = chats_processed - chats_with_course

        # ── 2. Signals ───────────────────────────────────────────────────────
        signal_params: dict = {
            "select": "signal_type,extracted_topic,course_code,confidence,raw_text,week_of",
            "order":  "created_at.desc",
        }
        if course_filter:
            signal_params["course_code"] = f"eq.{course_filter.upper()}"
        signals = await fetch_all(http, f"{SUPABASE_URL}/rest/v1/telegram_signals", signal_params)

        # ── 3. Compute distributions ─────────────────────────────────────────
        type_counts    = Counter(s["signal_type"] for s in signals)
        topic_counts   = Counter(
            (s.get("extracted_topic") or "").strip()
            for s in signals
            if (s.get("extracted_topic") or "").strip()
        )
        course_counts  = Counter(s.get("course_code") or "NULL" for s in signals)
        conf_values    = [float(s["confidence"]) for s in signals if s.get("confidence")]
        avg_confidence = round(sum(conf_values) / len(conf_values), 3) if conf_values else 0

        # No-signal rate: messages with 0 signals / total processed
        unique_messages_with_signal = len({s.get("source_message_id") for s in signals} if signals else set())

        # Sample raw_text per signal type
        samples: dict[str, list[str]] = {}
        for stype in ("topic_mention", "confusion", "exam_emphasis", "answer_sharing"):
            type_samples = [
                s.get("raw_text", "")
                for s in signals
                if s.get("signal_type") == stype and s.get("raw_text")
            ][:3]
            if type_samples:
                samples[stype] = type_samples

        # Chats skipped (no course code) — query distinct chat_names without course_code
        skipped_resp = await http.get(
            f"{SUPABASE_URL}/rest/v1/telegram_signal_cursors",
            headers=HEADERS,
            params={"course_code": "is.null", "select": "platform_chat_id,chat_name"},
        )
        skipped_chats = skipped_resp.json() if skipped_resp.status_code == 200 else []

    # ── 4. No-signal rate ────────────────────────────────────────────────────
    no_signal_rate = None
    if total_messages_processed > 0:
        no_signal_rate = round(
            (total_messages_processed - unique_messages_with_signal) / total_messages_processed * 100, 1
        )

    return {
        "summary": {
            "messages_processed":        total_messages_processed,
            "signals_stored":            total_signals_stored,
            "unique_messages_with_signal": unique_messages_with_signal,
            "no_signal_rate_pct":        no_signal_rate,
            "avg_confidence":            avg_confidence,
            "chats_processed":           chats_processed,
            "chats_with_course_code":    chats_with_course,
            "chats_without_course_code": chats_without_course,
        },
        "signal_type_distribution": dict(type_counts.most_common()),
        "top_20_topics":            [
            {"topic": t, "count": c}
            for t, c in topic_counts.most_common(20)
        ],
        "signals_by_course":        dict(course_counts.most_common()),
        "sample_signals_by_type":   samples,
        "skipped_chats_no_course": [
            c.get("chat_name", "?") for c in skipped_chats[:20]
        ],
    }


def _print_report(report: dict):
    s = report["summary"]
    print("=" * 60)
    print("TELEGRAM SIGNAL PILOT — QUALITY REPORT")
    print("=" * 60)

    print("\n── PROCESSING SUMMARY ──")
    print(f"  Messages processed:          {s['messages_processed']:,}")
    print(f"  Signals stored:              {s['signals_stored']:,}")
    print(f"  Messages with ≥1 signal:     {s['unique_messages_with_signal']:,}")
    if s["no_signal_rate_pct"] is not None:
        print(f"  No-signal rate:              {s['no_signal_rate_pct']}%  (higher = model is selective)")
    print(f"  Avg confidence:              {s['avg_confidence']}")
    print(f"  Chats processed:             {s['chats_processed']}")
    print(f"  Chats with course code:      {s['chats_with_course_code']}")
    print(f"  Chats without course code:   {s['chats_without_course_code']}  (skipped)")

    print("\n── SIGNAL TYPE DISTRIBUTION ──")
    for stype, count in report["signal_type_distribution"].items():
        pct = round(count / s["signals_stored"] * 100, 1) if s["signals_stored"] else 0
        print(f"  {stype:<20} {count:>6}  ({pct}%)")

    print("\n── SIGNALS BY COURSE ──")
    for course, count in report["signals_by_course"].items():
        print(f"  {course:<15} {count:>6}")

    print("\n── TOP 20 EXTRACTED TOPICS ──")
    for i, t in enumerate(report["top_20_topics"], 1):
        print(f"  {i:>2}. {t['topic']:<45} {t['count']}")

    print("\n── SAMPLE SIGNALS BY TYPE ──")
    for stype, samples in report["sample_signals_by_type"].items():
        print(f"\n  [{stype}]")
        for sample in samples:
            print(f"    • {sample[:120]}")

    skipped = report.get("skipped_chats_no_course") or []
    if skipped:
        print(f"\n── SKIPPED CHATS (no course code, first {len(skipped)}) ──")
        for name in skipped:
            print(f"    {name[:60]}")

    print("\n── DECISION CRITERIA ──")
    print("  Good pilot signal:")
    print("    • no_signal_rate > 60%  (model is selective, not hallucinating)")
    print("    • avg_confidence > 0.70")
    print("    • top topics are real academic concepts (not vague)")
    print("    • sample raw_text snippets look relevant and specific")
    print("")


async def main():
    parser = argparse.ArgumentParser(description="Telegram signal quality report")
    parser.add_argument("--course", help="Filter by course code (e.g. IT353)")
    parser.add_argument("--json",   action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    report = await build_report(course_filter=args.course)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)


if __name__ == "__main__":
    asyncio.run(main())
