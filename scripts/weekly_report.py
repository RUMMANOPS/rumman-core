#!/usr/bin/env python3
"""
weekly_report.py — RUMMAN weekly ops + product health report.

Generates a structured summary of the past 7 days and sends it to a
Telegram ops channel. Designed to run as a weekly cron (Monday 08:00 AST).

What it reports:
  PIPELINE   — queue health, drain rates, failed job types
  PRODUCT    — query volume, zero-result rate by course, latency
  COVERAGE   — top unmet queries (zero-result, 3+ occurrences this week)
  COST       — estimated OpenAI spend (from learning_events.metadata)
  BACKFILL   — historical ingestion progress

Usage:
    python3 scripts/weekly_report.py [--dry-run]
    # --dry-run: print to stdout instead of sending to Telegram

Requires: SUPABASE_URL, SUPABASE_KEY, RUMMAN_OPS_CHAT_ID, TELEGRAM_BOT_TOKEN
  RUMMAN_OPS_CHAT_ID — Telegram chat_id of the ops channel (negative int for groups)
"""

import os
import sys
import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL  = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY  = os.environ["SUPABASE_KEY"]
BOT_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN", "")
OPS_CHAT_ID   = os.getenv("RUMMAN_OPS_CHAT_ID", "")

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}

WEEK_AGO = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()


async def query(http: httpx.AsyncClient, sql: str) -> list[dict]:
    """Run a SQL query via Supabase PAT management API."""
    pat = os.getenv("SUPABASE_PAT", "")
    if not pat:
        # Fall back to PostgREST for simple queries — but management API is needed for SQL
        raise RuntimeError("SUPABASE_PAT required for weekly_report.py")
    r = await http.post(
        f"https://api.supabase.com/v1/projects/yriavgczteuirigsvedu/database/query",
        headers={
            "Authorization": f"Bearer {pat}",
            "Content-Type":  "application/json",
        },
        json={"query": sql},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


async def gather_metrics(http: httpx.AsyncClient) -> dict:
    m = {}

    # ── Pipeline health ───────────────────────────────────────────────────────

    rows = await query(http, """
        SELECT job_type, status, COUNT(*) as n
        FROM processing_jobs
        GROUP BY job_type, status
    """)
    pending, failed = {}, {}
    for r in rows:
        if r["status"] == "pending":
            pending[r["job_type"]] = pending.get(r["job_type"], 0) + int(r["n"])
        elif r["status"] == "failed":
            failed[r["job_type"]] = failed.get(r["job_type"], 0) + int(r["n"])
    m["pending"] = pending
    m["failed"]  = failed

    # ── Backfill progress ─────────────────────────────────────────────────────

    rows = await query(http, """
        SELECT status, COUNT(*) as n FROM telegram_backfill_jobs GROUP BY status
    """)
    m["backfill"] = {r["status"]: int(r["n"]) for r in rows}

    # ── Messages ingested this week ───────────────────────────────────────────

    rows = await query(http, f"""
        SELECT COUNT(*) as n FROM messages WHERE created_at >= '{WEEK_AGO}'
    """)
    m["messages_this_week"] = int(rows[0]["n"]) if rows else 0

    # ── Query volume + grounding ──────────────────────────────────────────────

    rows = await query(http, f"""
        SELECT
            COUNT(*) FILTER (WHERE event_type IN ('query','synthesis'))                      AS total_queries,
            COUNT(*) FILTER (WHERE event_type = 'zero_result')                               AS zero_results,
            ROUND(AVG(latency_ms) FILTER (WHERE event_type='synthesis')::numeric, 0)         AS avg_latency_ms,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)
                FILTER (WHERE event_type='synthesis' AND latency_ms IS NOT NULL)             AS p95_latency_ms
        FROM learning_events
        WHERE occurred_at >= '{WEEK_AGO}'
    """)
    r = rows[0] if rows else {}
    m["queries"]      = int(r.get("total_queries") or 0)
    m["zero_results"] = int(r.get("zero_results") or 0)
    m["avg_latency"]  = int(r.get("avg_latency_ms") or 0)
    m["p95_latency"]  = int(float(r.get("p95_latency_ms") or 0))

    # ── Zero-result rate by course ────────────────────────────────────────────

    rows = await query(http, f"""
        SELECT
            UNNEST(course_codes) AS course,
            COUNT(*) AS n
        FROM learning_events
        WHERE event_type = 'zero_result'
          AND occurred_at >= '{WEEK_AGO}'
          AND ARRAY_LENGTH(course_codes, 1) > 0
        GROUP BY course
        ORDER BY n DESC
        LIMIT 5
    """)
    m["zero_by_course"] = [(r["course"], int(r["n"])) for r in rows]

    # ── Top unmet queries (zero-result, recurring) ────────────────────────────

    rows = await query(http, f"""
        SELECT query_raw, COUNT(*) AS n
        FROM learning_events
        WHERE event_type = 'zero_result'
          AND occurred_at >= '{WEEK_AGO}'
          AND query_raw IS NOT NULL
        GROUP BY query_raw
        HAVING COUNT(*) >= 2
        ORDER BY n DESC
        LIMIT 8
    """)
    m["unmet_queries"] = [(r["query_raw"], int(r["n"])) for r in rows]

    # ── OpenAI cost estimate ──────────────────────────────────────────────────

    rows = await query(http, f"""
        SELECT
            SUM((metadata->>'synthesis_tokens')::int) AS synthesis_tokens,
            COUNT(*) FILTER (WHERE event_type='synthesis') AS synthesis_calls
        FROM learning_events
        WHERE occurred_at >= '{WEEK_AGO}'
    """)
    r = rows[0] if rows else {}
    synth_tokens = int(r.get("synthesis_tokens") or 0)
    synth_calls  = int(r.get("synthesis_calls") or 0)
    # gpt-4o: ~$2.50/1M input + $10/1M output ≈ $6/1M blended (rough)
    # intent calls (gpt-4o-mini): ~400 tokens each at $0.15/1M input
    intent_cost = synth_calls * 400 * 0.15 / 1_000_000
    synth_cost  = synth_tokens * 6.00 / 1_000_000
    m["est_cost_usd"] = round(intent_cost + synth_cost, 4)
    m["synth_calls"]  = synth_calls

    # ── Corpus coverage ───────────────────────────────────────────────────────

    rows = await query(http, """
        SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE embedding IS NOT NULL) AS embedded,
               COUNT(*) FILTER (WHERE course_code IS NULL) AS unattributed
        FROM document_chunks
    """)
    r = rows[0] if rows else {}
    m["total_chunks"]       = int(r.get("total") or 0)
    m["embedded_chunks"]    = int(r.get("embedded") or 0)
    m["unattributed_chunks"]= int(r.get("unattributed") or 0)

    return m


def format_report(m: dict) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"<b>🟢 RUMMAN Weekly Health</b> — {now}\n"]

    # Pipeline
    pending = m.get("pending", {})
    failed  = m.get("failed", {})
    lines.append("<b>PIPELINE</b>")
    media_p  = pending.get("telegram_media", 0)
    audio_p  = pending.get("audio_transcribe", 0)
    embed_p  = pending.get("embed_chunk", 0)
    media_f  = failed.get("telegram_media", 0)
    embed_f  = failed.get("embed_chunk", 0)
    lines.append(f"  Media queue:  {media_p:,} pending  |  {media_f} failed")
    lines.append(f"  Audio queue:  {audio_p:,} pending")
    lines.append(f"  Embed queue:  {embed_p:,} pending  |  {embed_f} failed")
    lines.append(f"  Messages ingested this week: {m.get('messages_this_week',0):,}")

    # Backfill
    bf = m.get("backfill", {})
    bf_done    = bf.get("completed", 0)
    bf_pending = bf.get("pending", 0)
    bf_total   = sum(bf.values())
    lines.append(f"\n<b>BACKFILL</b>  {bf_done}/{bf_total} groups complete  ({bf_pending} pending)")

    # Product
    queries      = m.get("queries", 0)
    zero         = m.get("zero_results", 0)
    zero_pct     = round(100 * zero / queries, 1) if queries > 0 else 0
    avg_lat      = m.get("avg_latency", 0)
    p95_lat      = m.get("p95_latency", 0)
    synth_calls  = m.get("synth_calls", 0)
    cost         = m.get("est_cost_usd", 0)
    lines.append(f"\n<b>PRODUCT</b>")
    lines.append(f"  Queries this week: {queries}  ({synth_calls} with synthesis)")
    lines.append(f"  Zero-result rate:  {zero_pct}%  ({zero}/{queries})")
    lines.append(f"  Latency:  avg {avg_lat}ms  |  p95 {p95_lat}ms")
    lines.append(f"  Est. OpenAI cost:  ${cost}")

    # Zero by course
    zbc = m.get("zero_by_course", [])
    if zbc:
        lines.append("\n<b>ZERO-RESULT BY COURSE</b>")
        for course, n in zbc:
            lines.append(f"  {course}: {n}x")

    # Corpus
    total  = m.get("total_chunks", 0)
    unatr  = m.get("unattributed_chunks", 0)
    unatr_pct = round(100 * unatr / total, 1) if total > 0 else 0
    lines.append(f"\n<b>CORPUS</b>  {total:,} chunks  |  {unatr:,} unattributed ({unatr_pct}%)")

    # Unmet queries
    unmet = m.get("unmet_queries", [])
    if unmet:
        lines.append("\n<b>TOP UNMET QUERIES</b>  (zero-result, recurring)")
        for q, n in unmet:
            q_short = q[:60] + "..." if len(q) > 60 else q
            lines.append(f"  {n}x — {q_short}")

    return "\n".join(lines)


async def send_telegram(http: httpx.AsyncClient, text: str) -> None:
    if not BOT_TOKEN or not OPS_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN or RUMMAN_OPS_CHAT_ID not set — printing only.")
        print(text)
        return
    r = await http.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": OPS_CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=15,
    )
    if r.status_code == 200:
        print("REPORT_SENT | ok")
    else:
        print(f"REPORT_SEND_FAILED | status={r.status_code} | body={r.text[:200]}")


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print to stdout, do not send to Telegram")
    args = parser.parse_args()

    async with httpx.AsyncClient(timeout=60) as http:
        print("GATHERING_METRICS...", flush=True)
        try:
            metrics = await gather_metrics(http)
        except Exception as e:
            print(f"METRICS_ERROR | {e}", file=sys.stderr)
            sys.exit(1)

        report = format_report(metrics)

        if args.dry_run:
            print(report)
        else:
            await send_telegram(http, report)


if __name__ == "__main__":
    asyncio.run(main())
