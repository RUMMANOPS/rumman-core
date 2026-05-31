#!/usr/bin/env python3
"""
backfill_tenant_id_033.py — One-time script to apply migration 033.

Backfills tenant_id = SEU_TENANT_ID for all NULL rows in:
  - document_chunks  (~93K rows)
  - source_documents (~7K rows)
  - messages         (0 rows — already clean)

Strategy: cursor-based primary key scan.
  - Scan the table in 200-row pages using `id > cursor` (uses PK index — fast).
  - Filter NULL tenant_id rows client-side from each page.
  - PATCH only the NULL rows in that page.
  - Advance cursor to last id in page.
  - No full-table-scan per batch → each fetch takes ~100ms instead of 3.5s.

Estimated runtime: ~30-40 minutes.

Usage:
    python3 scripts/backfill_tenant_id_033.py 2>&1 | tee /tmp/migration_033.log
    nohup python3 scripts/backfill_tenant_id_033.py > /tmp/migration_033.log 2>&1 &
"""

import os
import sys
import time
import httpx
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL  = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY  = os.environ["SUPABASE_KEY"]
SEU_TENANT_ID = "00000000-0000-0000-0000-000000000001"
PAGE_SIZE     = 200   # rows to scan per page (PK-ordered)
PATCH_LIMIT   = 50    # max NULL IDs to patch per PATCH call (50 rows ≈ 1.5s, well under 9s timeout)

BASE_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}


def log(msg: str):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def count_nulls(http: httpx.Client, table: str) -> int:
    r = http.head(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={**BASE_HEADERS, "Prefer": "count=exact"},
        params={"tenant_id": "is.null"},
        timeout=20,
    )
    cr = r.headers.get("content-range", "0-0/0")
    try:
        return int(cr.split("/")[1])
    except Exception:
        return -1


def count_total(http: httpx.Client, table: str) -> int:
    r = http.head(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={**BASE_HEADERS, "Prefer": "count=exact"},
        timeout=20,
    )
    cr = r.headers.get("content-range", "0-0/0")
    try:
        return int(cr.split("/")[1])
    except Exception:
        return -1


def patch_ids(http: httpx.Client, table: str, ids: list) -> int:
    """PATCH a list of specific IDs. Returns count patched, or -1 on timeout."""
    id_list = ",".join(ids)
    r = http.patch(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={**BASE_HEADERS, "Prefer": "count=exact,return=minimal"},
        params={"id": f"in.({id_list})"},
        json={"tenant_id": SEU_TENANT_ID},
        timeout=20,
    )
    if r.status_code >= 400:
        if "57014" in r.text:
            return -1
        log(f"  PATCH ERROR {r.status_code}: {r.text[:200]}")
        return -2
    return len(ids)


def backfill_table(table: str) -> int:
    """Cursor-based full-table scan, patching NULL tenant_id rows along the way."""
    with httpx.Client() as http:
        null_count = count_nulls(http, table)
        total_rows = count_total(http, table)

    log(f"{table}: {null_count:,} NULL rows / {total_rows:,} total")
    if null_count == 0:
        log(f"{table}: already clean — skipping.")
        return 0

    total_patched = 0
    pages_scanned = 0
    cursor = "00000000-0000-0000-0000-000000000000"  # start before first UUID
    errors = 0
    start = time.time()

    with httpx.Client() as http:
        while True:
            # Fetch next page ordered by primary key
            try:
                r = http.get(
                    f"{SUPABASE_URL}/rest/v1/{table}",
                    headers={**BASE_HEADERS, "Prefer": ""},
                    params={
                        "id":    f"gt.{cursor}",
                        "select": "id,tenant_id",
                        "order":  "id.asc",
                        "limit":  str(PAGE_SIZE),
                    },
                    timeout=15,
                )
            except Exception as exc:
                errors += 1
                if errors > 20:
                    log(f"  Too many fetch errors — aborting")
                    break
                time.sleep(2)
                continue

            if r.status_code >= 400:
                errors += 1
                log(f"  FETCH ERROR {r.status_code}: {r.text[:120]}")
                time.sleep(2)
                continue

            page = r.json()
            if not page:
                log(f"  Reached end of table — done scanning.")
                break

            cursor = page[-1]["id"]
            pages_scanned += 1

            # Find NULL tenant_id rows in this page
            null_ids = [row["id"] for row in page if row.get("tenant_id") is None]

            if null_ids:
                # PATCH in sub-batches of PATCH_LIMIT to stay under statement timeout.
                # On timeout: retry the whole sub_batch split into halves — cursor scan
                # is one-pass, so skipped rows would never be revisited.
                for i in range(0, len(null_ids), PATCH_LIMIT):
                    sub_batch = null_ids[i:i + PATCH_LIMIT]
                    result = patch_ids(http, table, sub_batch)
                    if result == -1:
                        # Timeout — split the FULL sub_batch into 25-row chunks and retry each
                        retry_size = max(10, PATCH_LIMIT // 2)
                        for j in range(0, len(sub_batch), retry_size):
                            chunk = sub_batch[j:j + retry_size]
                            result2 = patch_ids(http, table, chunk)
                            if result2 > 0:
                                total_patched += result2
                                errors = 0
                            elif result2 == -1:
                                errors += 1
                                time.sleep(1)
                            else:
                                errors += 1
                    elif result == -2:
                        errors += 1
                    else:
                        total_patched += result
                        errors = 0

            if errors > 20:
                log(f"  Too many patch errors — aborting")
                break

            # Progress log every 500 pages
            if pages_scanned % 500 == 0:
                elapsed = time.time() - start
                pct = pages_scanned * PAGE_SIZE / max(total_rows, 1) * 100
                rate = total_patched / elapsed if elapsed > 0 else 0
                remaining_patches = null_count - total_patched
                eta = remaining_patches / rate if rate > 0 else 0
                log(
                    f"  {table} | {pct:.0f}% scanned | "
                    f"patched {total_patched:,}/{null_count:,} | "
                    f"{rate:.0f} rows/s | ETA {eta/60:.1f} min"
                )

    # Final verification
    with httpx.Client() as http:
        remaining = count_nulls(http, table)

    elapsed = time.time() - start
    log(
        f"{table}: DONE — {total_patched:,} patched | "
        f"remaining NULL: {remaining} | "
        f"{elapsed:.0f}s ({pages_scanned} pages)"
    )
    return total_patched


def main():
    log("=== migration 033 tenant_id backfill (cursor-based) ===")
    log(f"Target: {SEU_TENANT_ID}")
    log(f"Strategy: PK cursor scan, {PAGE_SIZE} rows/page, patch NULL rows in-place")
    log("")

    grand_total = 0
    for table in ["document_chunks", "source_documents", "messages"]:
        log("")
        grand_total += backfill_table(table)

    elapsed = time.time()
    log("")
    log(f"=== ALL DONE — {grand_total:,} total rows backfilled ===")
    log("Next: python3 scripts/refresh_course_profiles.py")
    log("Next: python3 scripts/extract_exam_signals.py")


if __name__ == "__main__":
    main()
