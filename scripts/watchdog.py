#!/usr/bin/env python3
"""
watchdog.py — RUMMAN reliability watchdog.

Runs every 30 minutes. Checks four conditions and sends a Telegram alert
to the ops channel if any service appears stuck or silent.

Checks:
  1. Worker heartbeats (worker_heartbeats table) — alerts if any worker
     has not checked in for more than STALE_WORKER_MINUTES.
  2. Stale backfill lease — alerts if a job shows status=running but the
     lease has expired (worker held the lock and then died).
  3. Message insertion rate — alerts if no messages have been inserted for
     STALE_MSG_MINUTES while backfill jobs are active/pending.
  4. Stuck processing jobs — alerts if telegram_media / audio_transcribe
     jobs have been in 'processing' state for more than STALE_PROC_MINUTES
     (these are invisible to the media worker and will never auto-retry).

Required env vars:
  SUPABASE_URL, SUPABASE_KEY

Optional (alerts are printed to stdout if not set):
  TELEGRAM_BOT_TOKEN   — from Railway 'bot' service (set: 8801288133:...)
  RUMMAN_OPS_CHAT_ID   — Telegram chat_id for the ops channel or personal DM.
                         To find: add @RummanSEUBot to a group, send any message,
                         then check https://api.telegram.org/bot<TOKEN>/getUpdates.

To run continuously (add to Procfile):
  watchdog: python3 scripts/watchdog.py

To run as a one-off health check:
  python3 scripts/watchdog.py --once
"""

import os
import sys
import asyncio
from datetime import datetime, timezone, timedelta

import httpx
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OPS_CHAT_ID  = os.getenv("RUMMAN_OPS_CHAT_ID", "").strip()

CHECK_INTERVAL_SECONDS = 1800   # 30 minutes between full check cycles
STALE_WORKER_MINUTES   = 20     # alert if any worker heartbeat is older than this
STALE_MSG_MINUTES      = 60     # alert if no messages inserted for this long with active jobs
STALE_PROC_MINUTES     = 30     # alert if processing jobs stuck longer than this
STALE_LEASE_MINUTES    = 15     # alert if a running job's lease expired this long ago

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}

# Workers that must check in regularly (matches worker_id in worker_heartbeats)
MONITORED_WORKERS = {
    "backfill_worker": "Backfill",
    "media_worker":    "Media",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def send_alert(http: httpx.AsyncClient, text: str) -> None:
    print(f"WATCHDOG_ALERT | {utc_now().isoformat()}\n{text}", flush=True)
    if not BOT_TOKEN or not OPS_CHAT_ID:
        print(
            "WATCHDOG_NO_CHANNEL | Set TELEGRAM_BOT_TOKEN and RUMMAN_OPS_CHAT_ID "
            "in Railway to receive Telegram alerts.",
            flush=True,
        )
        return
    try:
        r = await http.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": OPS_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        if r.status_code != 200:
            print(f"WATCHDOG_SEND_FAILED | status={r.status_code} | {r.text[:120]}", flush=True)
    except Exception as e:
        print(f"WATCHDOG_SEND_ERROR | {e}", flush=True)


# ── Check 1: Worker heartbeats ────────────────────────────────────────────────

async def check_worker_heartbeats(http: httpx.AsyncClient) -> list[str]:
    """Alert if any monitored worker has not sent a heartbeat recently."""
    cutoff = (utc_now() - timedelta(minutes=STALE_WORKER_MINUTES)).isoformat()
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/worker_heartbeats",
        headers=HEADERS,
        params={
            "worker_id": f"in.({','.join(MONITORED_WORKERS.keys())})",
            "select":    "worker_id,service_name,status,last_seen_at",
        },
        timeout=15,
    )
    if r.status_code >= 400:
        return [f"⚠️ <b>WATCHDOG DB ERROR</b>\nCould not read worker_heartbeats (HTTP {r.status_code})"]

    rows = {row["worker_id"]: row for row in r.json()}
    alerts = []

    for worker_id, label in MONITORED_WORKERS.items():
        row = rows.get(worker_id)
        if not row:
            alerts.append(
                f"🔴 <b>{label.upper()} WORKER MISSING</b>\n"
                f"No heartbeat row found for {worker_id}.\n"
                f"Worker may never have started or table is missing."
            )
            continue
        last_seen = row.get("last_seen_at", "")
        if last_seen < cutoff:
            delta_min = int((utc_now() - datetime.fromisoformat(last_seen.replace("Z", "+00:00"))).total_seconds() / 60)
            alerts.append(
                f"🔴 <b>{label.upper()} WORKER SILENT</b>\n"
                f"Last heartbeat: {delta_min} min ago ({last_seen})\n"
                f"Status at last beat: {row.get('status', '?')}\n"
                f"Worker may be crashed, stuck, or Railway container is down."
            )

    return alerts


# ── Check 2: Stale backfill lease ─────────────────────────────────────────────

async def check_stale_backfill_lease(http: httpx.AsyncClient) -> list[str]:
    """Alert if a backfill job is stuck in running state with an expired lease."""
    cutoff = (utc_now() - timedelta(minutes=STALE_LEASE_MINUTES)).isoformat()
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/telegram_backfill_jobs",
        headers=HEADERS,
        params={
            "status":           "eq.running",
            "lease_expires_at": f"lt.{cutoff}",
            "select":           "id,chat_name,heartbeat_at,lease_expires_at,worker_id",
        },
        timeout=15,
    )
    if r.status_code >= 400:
        return []

    alerts = []
    for job in r.json():
        alerts.append(
            f"🔴 <b>BACKFILL LEASE EXPIRED</b>\n"
            f"Job #{job['id']} — {job.get('chat_name', '?')}\n"
            f"Lease expired: {job.get('lease_expires_at', '?')}\n"
            f"Last heartbeat: {job.get('heartbeat_at', '?')}\n"
            f"Worker: {job.get('worker_id', '?')}\n"
            f"Job is stuck. Restart the backfill service to auto-reclaim."
        )
    return alerts


# ── Check 3: Message insertion rate ───────────────────────────────────────────

async def check_message_insertion_rate(http: httpx.AsyncClient) -> list[str]:
    """Alert if no messages inserted recently while backfill is active."""
    since = (utc_now() - timedelta(minutes=STALE_MSG_MINUTES)).isoformat()

    r_msg = await http.get(
        f"{SUPABASE_URL}/rest/v1/messages",
        headers=HEADERS,
        params={"select": "id", "created_at": f"gte.{since}", "limit": "1"},
        timeout=15,
    )
    if r_msg.status_code >= 400 or r_msg.json():
        return []  # messages flowing or DB error — no alert

    # No recent messages — check for active jobs before alarming
    r_jobs = await http.get(
        f"{SUPABASE_URL}/rest/v1/telegram_backfill_jobs",
        headers=HEADERS,
        params={"status": "in.(running,pending)", "select": "id", "limit": "1"},
        timeout=15,
    )
    if r_jobs.status_code >= 400 or not r_jobs.json():
        return []  # no active jobs — silence is expected

    return [
        f"🟠 <b>BACKFILL SILENT</b>\n"
        f"No messages inserted in the last {STALE_MSG_MINUTES} min.\n"
        f"Active/pending backfill jobs exist.\n"
        f"Worker may be stalled, disconnected, or hitting FloodWait."
    ]


# ── Check 4: Stuck processing jobs ────────────────────────────────────────────

async def check_stuck_processing_jobs(http: httpx.AsyncClient) -> list[str]:
    """Alert if media/audio jobs are stuck in 'processing' state."""
    cutoff = (utc_now() - timedelta(minutes=STALE_PROC_MINUTES)).isoformat()
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/processing_jobs",
        headers=HEADERS,
        params={
            "status":     "eq.processing",
            "job_type":   "in.(audio_transcribe,telegram_media)",
            "updated_at": f"lt.{cutoff}",
            "select":     "id,job_type,updated_at",
            "limit":      "20",
        },
        timeout=15,
    )
    if r.status_code >= 400:
        return []

    stuck = r.json()
    if not stuck:
        return []

    return [
        f"🟡 <b>STUCK MEDIA JOBS</b>\n"
        f"{len(stuck)} job(s) stuck in 'processing' for >{STALE_PROC_MINUTES} min.\n"
        f"Media worker may have crashed mid-job.\n"
        f"These will be auto-reclaimed on next media worker restart."
    ]


# ── Main loop ─────────────────────────────────────────────────────────────────

async def run_checks(http: httpx.AsyncClient) -> int:
    """Run all checks. Returns number of alerts fired."""
    print(f"WATCHDOG_CHECK | {utc_now().isoformat()}", flush=True)

    alerts: list[str] = []
    alerts.extend(await check_worker_heartbeats(http))
    alerts.extend(await check_stale_backfill_lease(http))
    alerts.extend(await check_message_insertion_rate(http))
    alerts.extend(await check_stuck_processing_jobs(http))

    if not alerts:
        print("WATCHDOG_OK | all checks passed", flush=True)
        return 0

    for alert in alerts:
        await send_alert(http, alert)

    return len(alerts)


async def main(once: bool = False) -> None:
    print(
        f"WATCHDOG_STARTING | interval={CHECK_INTERVAL_SECONDS}s "
        f"| alert_channel={'configured' if OPS_CHAT_ID else 'NOT SET — stdout only'}",
        flush=True,
    )

    async with httpx.AsyncClient(timeout=30) as http:
        while True:
            try:
                await run_checks(http)
            except Exception as e:
                print(f"WATCHDOG_ERROR | {e}", flush=True)

            if once:
                break
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    once = "--once" in sys.argv
    asyncio.run(main(once=once))
