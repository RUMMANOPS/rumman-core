#!/usr/bin/env python3
"""
reset_media_jobs.py — Reset failed telegram_media jobs back to pending.

Run ONLY after confirming TELEGRAM_MEDIA_IBRAHIM_SESSION (canonical) or
TELEGRAM_BAYAN_SESSION (fallback) is set in Railway and the media_worker is
successfully connecting (check Railway logs for DOWNLOAD_WORKER_READY).

Usage:
    python3 scripts/reset_media_jobs.py --dry-run   # count only
    python3 scripts/reset_media_jobs.py             # reset all failed jobs

Background:
    11,357 telegram_media jobs failed with "Cannot send requests while
    disconnected" because the media session env var was not set in Railway.
    The worker reads TELEGRAM_MEDIA_IBRAHIM_SESSION first (canonical),
    then falls back to TELEGRAM_BAYAN_SESSION.

    After deploying the fix in telegram_download_worker.py and confirming the
    worker connects successfully, run this script to re-queue all failed jobs.
    The media_worker will process them at ~3-5s each → ~15-20 hours to drain.
"""

import os
import sys
import httpx
import argparse
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal",
}


def count_failed(http: httpx.Client) -> int:
    r = http.get(
        f"{SUPABASE_URL}/rest/v1/processing_jobs",
        headers={**HEADERS, "Prefer": "count=exact"},
        params={"job_type": "eq.telegram_media", "status": "eq.failed", "select": "id", "limit": "1"},
        timeout=15,
    )
    cr = r.headers.get("content-range", "?")
    return int(cr.split("/")[-1]) if "/" in cr and cr.split("/")[-1].isdigit() else 0


def fetch_failed_ids(http: httpx.Client) -> list[str]:
    ids = []
    offset = 0
    while True:
        r = http.get(
            f"{SUPABASE_URL}/rest/v1/processing_jobs",
            headers=HEADERS,
            params={"job_type": "eq.telegram_media", "status": "eq.failed", "select": "id", "limit": "1000", "offset": str(offset)},
            timeout=30,
        )
        page = r.json() if isinstance(r.json(), list) else []
        ids.extend(row["id"] for row in page)
        offset += len(page)
        if len(page) < 1000:
            break
    return ids


def reset_batch(http: httpx.Client, ids: list[str]) -> int:
    ids_filter = "(" + ",".join(ids) + ")"
    r = http.patch(
        f"{SUPABASE_URL}/rest/v1/processing_jobs",
        headers=HEADERS,
        params={"id": f"in.{ids_filter}"},
        json={"status": "pending", "retry_count": 0, "error": None},
        timeout=30,
    )
    if r.status_code not in (200, 204):
        print(f"  ERROR resetting batch: {r.status_code} {r.text[:200]}")
        return 0
    return len(ids)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=50)
    args = parser.parse_args()

    with httpx.Client() as http:
        total = count_failed(http)
        print(f"Failed telegram_media jobs: {total:,}")

        if args.dry_run:
            print("[DRY RUN] Would reset all to pending. Run without --dry-run to proceed.")
            return

        if total == 0:
            print("Nothing to reset.")
            return

        print(f"Fetching {total:,} job IDs...")
        ids = fetch_failed_ids(http)
        print(f"  Fetched {len(ids):,} IDs. Resetting in batches of {args.batch_size}...")

        reset_count = 0
        for i in range(0, len(ids), args.batch_size):
            batch = ids[i:i + args.batch_size]
            reset_count += reset_batch(http, batch)
            if reset_count % 500 == 0 or reset_count == len(ids):
                print(f"  Reset {reset_count:,}/{len(ids):,}...")

        print(f"Done. {reset_count:,} jobs reset to pending.")
        print("The media_worker on Railway will now drain the queue (~15-20 hours).")


if __name__ == "__main__":
    main()
