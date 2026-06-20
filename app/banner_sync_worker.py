#!/usr/bin/env python3
"""
banner_sync_worker.py — BANNER-SYNC-1b scheduler (gated Railway worker).

Periodically refreshes term_sections from Banner via the shared, guarded
run_sync_once(apply=True). Keeps generation/UI data fresh. It does NOT protect
approval — approve() runs its own LIVE re-check (APPROVE-REVALIDATION-1).

Gated by BANNER_SYNC_ENABLED (default off). NOT yet added to Procfile.
Run modes:
  BANNER_SYNC_ENABLED=true python3 app/banner_sync_worker.py            # loop
  BANNER_SYNC_ENABLED=true python3 app/banner_sync_worker.py --once     # single cycle (test)

Concurrency: run_sync_once uses a DB-row mutex (banner_sync_runs 'running') — a true
pg_advisory_lock needs a direct PG session, which our PostgREST-only stack doesn't use.
"""
import os, sys, json, time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import httpx
from banner_live_sync import run_sync_once, load_supabase_env, sb_headers   # shared logic

TENANT_ID = "00000000-0000-0000-0000-000000000001"
ENABLED   = os.getenv("BANNER_SYNC_ENABLED", "").strip().lower() == "true"
INTERVAL  = int(os.getenv("BANNER_SYNC_INTERVAL_SECONDS", "1800"))   # 30m default (pre-registration)
MAX_FAILS = int(os.getenv("BANNER_SYNC_MAX_CONSECUTIVE_FAILS", "5"))


def log(m): print(m, flush=True)
def _now_iso(): return datetime.now(timezone.utc).isoformat()


def heartbeat(status, meta=None):
    """Best-effort worker_heartbeats upsert — never blocks or fails the worker."""
    try:
        url, key = load_supabase_env()
        httpx.post(f"{url}/rest/v1/worker_heartbeats?on_conflict=worker_id",
                   headers=sb_headers(key, "resolution=merge-duplicates,return=minimal"),
                   json={"worker_id": "banner_sync", "service_name": "banner_sync",
                         "tenant_id": TENANT_ID, "last_seen_at": _now_iso(),
                         "status": status, "metadata": meta or {}},
                   timeout=15)
    except Exception:
        pass


def cycle():
    res = run_sync_once(apply=True, trigger="scheduled")
    log("BANNER_SYNC_CYCLE | " + json.dumps(res, ensure_ascii=False, default=str))
    return res


def main():
    once = ("--once" in sys.argv) or (os.getenv("BANNER_SYNC_ONCE", "").strip().lower() == "true")
    if not ENABLED:
        log("banner_sync DISABLED (set BANNER_SYNC_ENABLED=true to run)")
        return

    fails = 0
    while True:
        try:
            res = cycle()
            ok = bool(res.get("ok"))
        except Exception as exc:                      # defensive: a cycle must never crash the loop
            res = {"ok": False, "reason": "exception", "error": str(exc)[:160]}
            log("BANNER_SYNC_CYCLE | " + json.dumps(res)); ok = False

        # 'locked' = another run in flight -> not a failure
        if ok or res.get("reason") == "locked":
            fails = 0
            heartbeat("running", res)
        else:
            fails += 1
            heartbeat("error", {**res, "consecutive_fails": fails})
            if fails >= MAX_FAILS:
                log(f"banner_sync SELF-STOP after {fails} consecutive failures")
                heartbeat("stopped", {"consecutive_fails": fails})
                return

        if once:
            return
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
