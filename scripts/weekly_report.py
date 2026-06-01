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
SEU_TENANT_ID = "00000000-0000-0000-0000-000000000001"

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}

WEEK_AGO = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()


async def _count(http: httpx.AsyncClient, table: str, params: dict,
                 count_col: str = "id") -> int:
    """Return exact count for a PostgREST query."""
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={**HEADERS, "Prefer": "count=exact"},
        params={**params, "select": count_col, "limit": "1"},
    )
    cr = r.headers.get("content-range", "?")
    return int(cr.split("/")[-1]) if "/" in cr and cr.split("/")[-1].isdigit() else 0


async def _fetch_all(http: httpx.AsyncClient, table: str, params: dict, select: str,
                     since: str = None, order_col: str = "id.asc") -> list[dict]:
    """Paginate all rows matching params since given ISO timestamp."""
    rows: list[dict] = []
    offset = 0
    PAGE = 1000
    base = {**params, "select": select, "limit": str(PAGE), "order": order_col}
    if since:
        base["occurred_at"] = f"gte.{since}"
    while True:
        r = await http.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=HEADERS,
            params={**base, "offset": str(offset)},
        )
        page = r.json() if isinstance(r.json(), list) else []
        rows.extend(page)
        offset += len(page)
        if len(page) < PAGE:
            break
    return rows


async def gather_metrics(http: httpx.AsyncClient) -> dict:
    m = {}

    # ── Pipeline health ───────────────────────────────────────────────────────
    pending: dict = {}
    failed: dict  = {}
    for jtype in ("telegram_media", "audio_transcribe", "embed_chunk", "pdf_extract"):
        pending[jtype] = await _count(http, "processing_jobs",
                                      {"job_type": f"eq.{jtype}", "status": "eq.pending"})
        failed[jtype]  = await _count(http, "processing_jobs",
                                      {"job_type": f"eq.{jtype}", "status": "eq.failed"})
    m["pending"] = pending
    m["failed"]  = failed

    # ── Backfill progress ─────────────────────────────────────────────────────
    bf: dict = {}
    for status in ("pending", "running", "completed"):
        bf[status] = await _count(http, "telegram_backfill_jobs", {"status": f"eq.{status}"})
    m["backfill"] = bf

    # ── Messages ingested this week ───────────────────────────────────────────
    m["messages_this_week"] = await _count(http, "messages",
                                           {"created_at": f"gte.{WEEK_AGO}"})

    # ── Query volume + grounding ──────────────────────────────────────────────
    synth_events = await _fetch_all(
        http, "learning_events",
        {"event_type": "eq.synthesis"},
        "latency_ms,metadata",
        since=WEEK_AGO,
    )
    zero_events = await _fetch_all(
        http, "learning_events",
        {"event_type": "eq.zero_result"},
        "query_raw,course_codes",
        since=WEEK_AGO,
    )

    latencies = [e["latency_ms"] for e in synth_events if e.get("latency_ms")]
    avg_latency = int(sum(latencies) / len(latencies)) if latencies else 0
    p95_latency = 0
    if latencies:
        latencies_sorted = sorted(latencies)
        p95_idx = int(len(latencies_sorted) * 0.95)
        p95_latency = latencies_sorted[min(p95_idx, len(latencies_sorted) - 1)]

    mini_tokens = premium_tokens = cache_hits = 0
    for e in synth_events:
        meta = e.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        toks = int(meta.get("synthesis_tokens") or 0)
        model = meta.get("synthesis_model") or ""
        if "mini" in model:
            mini_tokens += toks
        elif "gpt-4o" in model:
            premium_tokens += toks
        if meta.get("cache_hit") == "true" or meta.get("cache_hit") is True:
            cache_hits += 1

    synth_calls = len(synth_events)
    intent_cost  = synth_calls * 400 * 0.30 / 1_000_000
    mini_cost    = mini_tokens    * 0.30 / 1_000_000
    premium_cost = premium_tokens * 5.00 / 1_000_000

    m["queries"]        = synth_calls + len(zero_events)
    m["zero_results"]   = len(zero_events)
    m["avg_latency"]    = avg_latency
    m["p95_latency"]    = int(p95_latency)
    m["est_cost_usd"]   = round(intent_cost + mini_cost + premium_cost, 4)
    m["synth_calls"]    = synth_calls
    m["cache_hits"]     = cache_hits
    m["mini_tokens"]    = mini_tokens
    m["premium_tokens"] = premium_tokens

    # ── Zero-result rate by course ────────────────────────────────────────────
    from collections import Counter
    course_counts: Counter = Counter()
    for e in zero_events:
        codes = e.get("course_codes") or []
        for c in codes:
            if c:
                course_counts[c] += 1
    m["zero_by_course"] = course_counts.most_common(5)

    # ── Top unmet queries (zero-result, recurring) ────────────────────────────
    query_counts: Counter = Counter()
    for e in zero_events:
        q = (e.get("query_raw") or "").strip()
        if q:
            query_counts[q] += 1
    m["unmet_queries"] = [(q, n) for q, n in query_counts.most_common(8) if n >= 2]

    # ── Corpus coverage ───────────────────────────────────────────────────────
    m["total_chunks"]        = await _count(http, "document_chunks", {})
    m["embedded_chunks"]     = await _count(http, "document_chunks", {"embedding": "not.is.null"})
    m["unattributed_chunks"] = await _count(http, "document_chunks", {"course_code": "is.null"})

    # ── Course intelligence profiles ──────────────────────────────────────────
    cov_rows = await _fetch_all(http, "course_intelligence_profiles", {}, "coverage_level",
                                order_col="course_code.asc")
    cov: dict = {}
    for row in cov_rows:
        level = row.get("coverage_level") or "none"
        cov[level] = cov.get(level, 0) + 1
    m["course_coverage"] = cov
    m["exam_signals"]    = await _count(http, "exam_intelligence",
                                        {"tenant_id": f"eq.{SEU_TENANT_ID}"})

    # ── Message signals ───────────────────────────────────────────────────────
    sig_rows = await _fetch_all(http, "message_signals",
                                {"tenant_id": f"eq.{SEU_TENANT_ID}"}, "signal_type,is_current_semester")
    sig_by_type: dict = {}
    sig_current = 0
    for row in sig_rows:
        t = row.get("signal_type") or "unknown"
        sig_by_type[t] = sig_by_type.get(t, 0) + 1
        if row.get("is_current_semester"):
            sig_current += 1
    m["msg_signals_by_type"] = sig_by_type
    m["msg_signals_current"] = sig_current

    # ── Worker heartbeat health ───────────────────────────────────────────────
    now_utc = datetime.now(timezone.utc)
    hb_rows = await _fetch_all(http, "worker_heartbeats", {}, "worker_id,service_name,status,last_seen_at",
                               order_col="last_seen_at.desc")
    stale_workers = []
    healthy_workers = []
    for row in hb_rows:
        ts = row.get("last_seen_at") or ""
        try:
            last_seen = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            age_min = (now_utc - last_seen).total_seconds() / 60
        except Exception:
            age_min = 9999
        wid = row.get("worker_id") or row.get("service_name") or "?"
        if age_min > 120:
            stale_workers.append((wid, age_min))
        else:
            healthy_workers.append((wid, row.get("status", "?")))
    m["stale_workers"]   = stale_workers
    m["healthy_workers"] = healthy_workers

    # ── Stale backfill jobs (running but lease expired) ───────────────────────
    lease_cutoff = (now_utc - timedelta(hours=2)).isoformat()
    m["stale_backfill_jobs"] = await _count(
        http, "telegram_backfill_jobs",
        {"status": "eq.running", "lease_expires_at": f"lt.{lease_cutoff}"},
    )

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
    synth_calls   = m.get("synth_calls", 0)
    cache_hits    = m.get("cache_hits", 0)
    cache_pct     = round(100 * cache_hits / synth_calls, 1) if synth_calls > 0 else 0
    cost          = m.get("est_cost_usd", 0)
    mini_tokens   = m.get("mini_tokens", 0)
    premium_tokens= m.get("premium_tokens", 0)
    lines.append(f"\n<b>PRODUCT</b>")
    lines.append(f"  Queries this week: {queries}  ({synth_calls} with synthesis)")
    lines.append(f"  Zero-result rate:  {zero_pct}%  ({zero}/{queries})")
    lines.append(f"  Cache hit rate:    {cache_pct}%  ({cache_hits}/{synth_calls})")
    lines.append(f"  Latency:  avg {avg_lat}ms  |  p95 {p95_lat}ms")
    lines.append(f"  Mini tokens: {mini_tokens:,}  |  Premium tokens: {premium_tokens:,}")
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

    # Course intelligence
    cov = m.get("course_coverage", {})
    exam_sigs = m.get("exam_signals", 0)
    if cov:
        strong   = cov.get("strong",   0)
        moderate = cov.get("moderate", 0)
        thin     = cov.get("thin",     0)
        total_courses = sum(cov.values())
        lines.append(
            f"\n<b>COURSE INTELLIGENCE</b>  {total_courses} courses tracked  |  "
            f"{exam_sigs} exam signal entries"
        )
        lines.append(
            f"  Strong: {strong}  |  Moderate: {moderate}  |  Thin: {thin}"
        )

    # Message signals
    msg_by_type  = m.get("msg_signals_by_type", {})
    msg_current  = m.get("msg_signals_current", 0)
    msg_total    = sum(msg_by_type.values())
    if msg_total > 0:
        exam_emp = msg_by_type.get("exam_emphasis",     0)
        diff_sig = msg_by_type.get("difficulty",        0)
        prof_sig = msg_by_type.get("professor_note",    0)
        res_sig  = msg_by_type.get("resource_rec",      0)
        conf_sig = msg_by_type.get("confusion_cluster", 0)
        lines.append(
            f"\n<b>MESSAGE SIGNALS</b>  {msg_total} total  |  {msg_current} current semester"
        )
        lines.append(
            f"  Exam emphasis: {exam_emp}  |  Difficulty: {diff_sig}  |  "
            f"Professor: {prof_sig}  |  Resources: {res_sig}  |  Confusion: {conf_sig}"
        )

    # Unmet queries
    unmet = m.get("unmet_queries", [])
    if unmet:
        lines.append("\n<b>TOP UNMET QUERIES</b>  (zero-result, recurring)")
        for q, n in unmet:
            q_short = q[:60] + "..." if len(q) > 60 else q
            lines.append(f"  {n}x — {q_short}")

    # Worker health
    stale   = m.get("stale_workers", [])
    healthy = m.get("healthy_workers", [])
    stale_bf = m.get("stale_backfill_jobs", 0)
    lines.append("\n<b>WORKER HEALTH</b>")
    if not stale and not stale_bf:
        lines.append(f"  All {len(healthy)} workers healthy")
    else:
        for wid, age_min in stale:
            lines.append(f"  STALE: {wid}  (last seen {age_min:.0f} min ago)")
        for wid, status in healthy:
            lines.append(f"  OK:    {wid}  ({status})")
    if stale_bf:
        lines.append(f"  WARN: {stale_bf} backfill job(s) stuck (expired lease)")

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
