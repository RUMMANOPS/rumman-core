#!/usr/bin/env python3
"""
fix_failed_backfill_jobs.py — Triage and annotate 22 failed telegram_backfill_jobs.

Category A (7 jobs): Permanently unrecoverable — mark PERMANENTLY_SKIPPED + retry_count=999
Category B (15 jobs): راوي needs channel access — mark REQUIRES_JOIN

Usage:
    python3 scripts/fix_failed_backfill_jobs.py

Requires: SUPABASE_URL, SUPABASE_KEY in .env
"""

import os
import sys
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

# ─── Category A: Permanently unrecoverable ────────────────────────────────────

# Sub-group 1: PeerUser entries (not channels — wrong type entirely)
PEER_USER_IDS = [
    ("8774187148",  "RUMMAN Payment Test"),
    ("8801288133",  "Rumman bot"),
    ("93372553",    "BotFather"),
    ("5793249812",  "Saudi Electronic University user"),
]

# Sub-group 2: Positive-format duplicate IDs (correct negative-format entry exists)
POSITIVE_FORMAT_DUPLICATES = [
    ("2836390758",  "لمّاح | SEU"),
    ("1800630384",  "لمّاح | SEU"),
]

# Sub-group 3: Negative-format entry that is itself a duplicate of the above
NEGATIVE_FORMAT_UNRESOLVABLE = [
    ("-1001800630384", "لمّاح | SEU"),
]

# All Category A IDs in one set (used later to identify Category B)
CATEGORY_A_IDS = (
    {cid for cid, _ in PEER_USER_IDS}
    | {cid for cid, _ in POSITIVE_FORMAT_DUPLICATES}
    | {cid for cid, _ in NEGATIVE_FORMAT_UNRESOLVABLE}
)

# ─── Error messages ────────────────────────────────────────────────────────────

MSG_PEER_USER     = "PERMANENTLY_SKIPPED: not a channel (user/bot account, wrong entry type)"
MSG_POS_DUPLICATE = "PERMANENTLY_SKIPPED: duplicate entry with wrong ID format"
MSG_NEG_UNRESOLVABLE = "PERMANENTLY_SKIPPED: same channel as positive-format entry, both unresolvable"
MSG_REQUIRES_JOIN = "REQUIRES_JOIN: راوي is not a member of this channel — add راوي then reset to pending"


def patch_job(client: httpx.Client, chat_id: str, payload: dict) -> dict:
    """PATCH a single backfill job row filtered by platform_chat_id."""
    r = client.patch(
        f"{SUPABASE_URL}/rest/v1/telegram_backfill_jobs",
        headers=HEADERS,
        params={"platform_chat_id": f"eq.{chat_id}"},
        json=payload,
    )
    if r.status_code >= 400:
        print(f"  ERROR patching {chat_id}: HTTP {r.status_code} — {r.text[:120]}", flush=True)
        return {}
    rows = r.json()
    return rows[0] if rows else {}


def fetch_failed_jobs(client: httpx.Client) -> list[dict]:
    """Fetch all rows still in failed status."""
    r = client.get(
        f"{SUPABASE_URL}/rest/v1/telegram_backfill_jobs",
        headers=HEADERS,
        params={
            "status": "eq.failed",
            "select": "id,platform_chat_id,chat_name,error_message,retry_count",
            "limit": "200",
            "order": "platform_chat_id.asc",
        },
    )
    if r.status_code >= 400:
        print(f"FETCH_ERROR: HTTP {r.status_code} — {r.text[:120]}", flush=True)
        return []
    return r.json()


def main():
    patched_a = 0
    patched_b = 0
    errors = 0

    with httpx.Client(timeout=30) as client:

        # ── Step 1: Patch Category A — permanently skipped ────────────────────
        print("=" * 70)
        print("STEP 1: Patching Category A — permanently unrecoverable (7 jobs)")
        print("=" * 70)

        groups = [
            (PEER_USER_IDS,               MSG_PEER_USER,          "PeerUser entries"),
            (POSITIVE_FORMAT_DUPLICATES,  MSG_POS_DUPLICATE,      "Positive-format duplicates"),
            (NEGATIVE_FORMAT_UNRESOLVABLE, MSG_NEG_UNRESOLVABLE,  "Negative-format unresolvable"),
        ]

        for entries, msg, label in groups:
            print(f"\n  [{label}]")
            for chat_id, name in entries:
                row = patch_job(client, chat_id, {
                    "error_message": msg,
                    "retry_count": 999,
                })
                if row:
                    patched_a += 1
                    print(f"  OK  {chat_id:25}  {name}")
                else:
                    errors += 1
                    print(f"  ERR {chat_id:25}  {name}")

        # ── Step 2: Patch Category B — REQUIRES_JOIN ──────────────────────────
        print()
        print("=" * 70)
        print("STEP 2: Patching Category B — راوي needs channel access (remaining failed)")
        print("=" * 70)

        # Fetch all still-failed jobs that are NOT in Category A
        current_failed = fetch_failed_jobs(client)
        category_b = [j for j in current_failed if j["platform_chat_id"] not in CATEGORY_A_IDS]

        if not category_b:
            print("  (none found — Category A patch may have already covered all)")
        else:
            for job in category_b:
                chat_id = job["platform_chat_id"]
                name = job["chat_name"] or "(unnamed)"
                row = patch_job(client, chat_id, {"error_message": MSG_REQUIRES_JOIN})
                if row:
                    patched_b += 1
                    print(f"  OK  {chat_id:25}  {name}")
                else:
                    errors += 1
                    print(f"  ERR {chat_id:25}  {name}")

        # ── Step 3: Verification report ───────────────────────────────────────
        print()
        print("=" * 70)
        print("STEP 3: Verification — fetching all failed jobs post-patch")
        print("=" * 70)

        all_failed = fetch_failed_jobs(client)

        permanently_skipped = [
            j for j in all_failed
            if (j.get("error_message") or "").startswith("PERMANENTLY_SKIPPED")
        ]
        requires_join = [
            j for j in all_failed
            if (j.get("error_message") or "").startswith("REQUIRES_JOIN")
        ]
        other_failed = [
            j for j in all_failed
            if not (j.get("error_message") or "").startswith(("PERMANENTLY_SKIPPED", "REQUIRES_JOIN"))
        ]

        print()
        print(f"  PERMANENTLY_SKIPPED  : {len(permanently_skipped):3}  (retry_count=999, Cockpit ignores)")
        for j in permanently_skipped:
            print(f"    - {j['platform_chat_id']:25}  {j['chat_name'] or '(unnamed)'}")

        print()
        print(f"  REQUIRES_JOIN (راوي)  : {len(requires_join):3}  (actionable — add راوي then reset to pending)")
        for j in requires_join:
            print(f"    - {j['platform_chat_id']:25}  {j['chat_name'] or '(unnamed)'}")

        if other_failed:
            print()
            print(f"  OTHER FAILED (unpatched): {len(other_failed)}")
            for j in other_failed:
                print(f"    - {j['platform_chat_id']:25}  err: {(j.get('error_message') or '')[:60]}")

        print()
        print("=" * 70)
        print(f"SUMMARY")
        print(f"  Patched Category A (PERMANENTLY_SKIPPED) : {patched_a}")
        print(f"  Patched Category B (REQUIRES_JOIN)       : {patched_b}")
        print(f"  Errors during patch                      : {errors}")
        print(f"  Total failed rows remaining              : {len(all_failed)}")
        print(f"    └─ PERMANENTLY_SKIPPED                 : {len(permanently_skipped)}")
        print(f"    └─ REQUIRES_JOIN                       : {len(requires_join)}")
        print(f"    └─ Other (unexpected)                  : {len(other_failed)}")
        print("=" * 70)

        if errors:
            sys.exit(1)


if __name__ == "__main__":
    main()
