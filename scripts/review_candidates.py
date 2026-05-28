#!/usr/bin/env python3
"""
review_candidates.py — Human gate for improvement_candidates.

Fetches pending candidates from the database, prompts for y/n/edit/skip,
and on approval writes the term into data/normalization_dict.json +
marks the DB row as approved.

Usage:
    python3 scripts/review_candidates.py
    python3 scripts/review_candidates.py --source zero_result
    python3 scripts/review_candidates.py --limit 20

Nothing is auto-promoted. Every change to normalization_dict.json
must pass through this script or a deliberate manual edit.
"""

import os
import sys
import json
import asyncio
import argparse
import httpx
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}
DICT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "normalization_dict.json")


async def fetch_pending(source: str | None, limit: int) -> list[dict]:
    params = {
        "status":   "eq.pending",
        "order":    "frequency.desc,created_at.asc",
        "limit":    str(limit),
        "select":   "id,surface_form,canonical_form,category,source,frequency,example_query",
    }
    if source:
        params["source"] = f"eq.{source}"

    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.get(
            f"{SUPABASE_URL}/rest/v1/improvement_candidates",
            headers=HEADERS, params=params,
        )
        r.raise_for_status()
        return r.json()


async def mark_candidate(candidate_id: str, status: str) -> None:
    async with httpx.AsyncClient(timeout=10) as http:
        await http.patch(
            f"{SUPABASE_URL}/rest/v1/improvement_candidates?id=eq.{candidate_id}",
            headers=HEADERS,
            json={
                "status":      status,
                "reviewed_by": "human",
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
                **({"promoted_at": datetime.now(timezone.utc).isoformat()} if status == "approved" else {}),
            },
        )


def load_dict() -> dict:
    with open(DICT_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_dict(d: dict) -> None:
    with open(DICT_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def promote_to_dict(d: dict, surface: str, canonical: str, source: str) -> None:
    """Add approved term to words section with provenance tag."""
    if not canonical:
        return
    # Phrases (multi-word) go into phrases section
    if " " in surface:
        d.setdefault("phrases", {})[surface] = canonical
    else:
        d.setdefault("words", {})[surface] = canonical


async def main(source: str | None, limit: int) -> None:
    candidates = await fetch_pending(source, limit)
    if not candidates:
        print("No pending candidates found.")
        return

    print(f"\n{len(candidates)} pending candidates to review.")
    print("Commands: [y]es / [n]o / [e]dit canonical / [s]kip / [q]uit\n")

    norm_dict = load_dict()
    promoted = 0
    rejected = 0

    for i, c in enumerate(candidates, 1):
        surface   = c["surface_form"]
        canonical = c["canonical_form"] or ""
        category  = c.get("category", "")
        src       = c.get("source", "")
        freq      = c.get("frequency", 1)
        example   = c.get("example_query") or ""

        print(f"[{i}/{len(candidates)}] {src} | freq={freq} | cat={category}")
        print(f"  surface:   {surface}")
        print(f"  canonical: {canonical}")
        if example:
            print(f"  context:   {example[:100]}")

        while True:
            choice = input("  > ").strip().lower()

            if choice in ("y", "yes"):
                promote_to_dict(norm_dict, surface, canonical, src)
                await mark_candidate(c["id"], "approved")
                promoted += 1
                break

            elif choice in ("n", "no"):
                await mark_candidate(c["id"], "rejected")
                rejected += 1
                break

            elif choice in ("e", "edit"):
                new_canonical = input("  new canonical: ").strip()
                if new_canonical:
                    promote_to_dict(norm_dict, surface, new_canonical, src)
                    await mark_candidate(c["id"], "approved")
                    promoted += 1
                break

            elif choice in ("s", "skip"):
                break

            elif choice in ("q", "quit"):
                print(f"\nStopped. Promoted={promoted} Rejected={rejected}")
                save_dict(norm_dict)
                return

            else:
                print("  Invalid. Enter y/n/e/s/q")

        print()

    save_dict(norm_dict)
    print(f"Done. Promoted={promoted} Rejected={rejected}")
    print(f"normalization_dict.json updated. Commit and deploy to activate.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=None,
                        help="Filter by source: generated | corpus | zero_result | low_confidence")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    asyncio.run(main(args.source, args.limit))
