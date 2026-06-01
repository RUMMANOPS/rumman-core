#!/usr/bin/env python3
"""
refresh_course_profiles.py — Build/refresh course_intelligence_profiles from document_chunks.

Pure SQL aggregation — no LLM calls, no OpenAI cost.
Run after any significant ingestion batch, or daily during exam season.

Usage:
    python3 scripts/refresh_course_profiles.py [--dry-run] [--course IT362]

Output:
    Upserts course_intelligence_profiles for all courses with chunks.
    Prints a summary table sorted by coverage level.
"""

import os
import sys
import httpx
import argparse
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
SUPABASE_PAT = os.environ.get("SUPABASE_PAT", "")  # management API for SQL aggregates
SUPABASE_REF = "yriavgczteuirigsvedu"
SEU_TENANT_ID = "00000000-0000-0000-0000-000000000001"

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal",
}

MGMT_HEADERS = {
    "Authorization": f"Bearer {SUPABASE_PAT}",
    "Content-Type":  "application/json",
    "User-Agent":    "Mozilla/5.0",
}

# Coverage thresholds
COVERAGE_STRONG   = 30   # 30+ exam chunks OR 60+ total chunks → strong
COVERAGE_MODERATE = 10   # 10+ exam chunks OR 20+ total chunks → moderate
COVERAGE_THIN     = 1    # any chunks → thin
# otherwise: none


def coverage_level(total: int, exam: int) -> str:
    if exam >= COVERAGE_STRONG or total >= 60:
        return "strong"
    if exam >= COVERAGE_MODERATE or total >= 20:
        return "moderate"
    if total >= COVERAGE_THIN:
        return "thin"
    return "none"


def fetch_chunk_aggregates(http: httpx.Client, course_code=None) -> list[dict]:
    """
    Fetch per-course chunk counts grouped by source_type.

    Primary: Supabase management API (single aggregated SQL query).
    Fallback: paginated PostgREST when SUPABASE_PAT is not set — fetches
              course_code + source_type + ingested_at in pages of 10K and
              aggregates in Python. Slower but requires no management token.

    Returns list of {course_code, source_type, chunk_count, last_indexed_at}.
    """
    if SUPABASE_PAT:
        course_filter = f"AND course_code = '{course_code}'" if course_code else ""
        sql = f"""
            SELECT
                course_code,
                source_type,
                COUNT(*) AS chunk_count,
                MAX(ingested_at) AS last_indexed_at
            FROM document_chunks
            WHERE (tenant_id = '{SEU_TENANT_ID}' OR tenant_id IS NULL)
                AND course_code IS NOT NULL
                {course_filter}
            GROUP BY course_code, source_type
            ORDER BY course_code, source_type;
        """
        r = http.post(
            f"https://api.supabase.com/v1/projects/{SUPABASE_REF}/database/query",
            headers=MGMT_HEADERS,
            json={"query": sql},
            timeout=60,
        )
        if r.status_code >= 400:
            print(f"ERROR running aggregate query: {r.status_code} {r.text[:300]}")
            sys.exit(1)
        return r.json()

    # PostgREST fallback — paginate in batches, aggregate in Python.
    # Supabase caps each response at 1,000 rows; iterate until an empty page.
    print("  SUPABASE_PAT not set — using PostgREST pagination fallback (slower)")
    PAGE_SIZE = 1000
    offset = 0
    raw_chunks: list[dict] = []
    params_base: dict = {
        "course_code": "not.is.null",
        "select": "course_code,source_type,ingested_at",
        "limit": str(PAGE_SIZE),
        "order": "course_code.asc,chunk_index.asc",
    }
    if course_code:
        params_base["course_code"] = f"eq.{course_code}"

    tenant_headers = {**HEADERS}
    while True:
        params = {**params_base, "offset": str(offset)}
        r = http.get(
            f"{SUPABASE_URL}/rest/v1/document_chunks",
            headers=tenant_headers,
            params=params,
            timeout=60,
        )
        if r.status_code >= 400:
            print(f"ERROR fetching chunks page at offset={offset}: {r.status_code} {r.text[:200]}")
            sys.exit(1)
        page = r.json()
        if not page:
            break
        raw_chunks.extend(page)
        offset += len(page)
        if offset % 10000 == 0:
            print(f"  Fetched {offset:,} chunks so far...")
        if len(page) < PAGE_SIZE:
            break

    # Convert to the same shape as the management API response
    from collections import defaultdict
    agg: dict[tuple, dict] = defaultdict(lambda: {"chunk_count": 0, "last_indexed_at": ""})
    for chunk in raw_chunks:
        key = (chunk.get("course_code"), chunk.get("source_type"))
        agg[key]["chunk_count"] += 1
        ts = chunk.get("ingested_at") or ""
        if ts > agg[key]["last_indexed_at"]:
            agg[key]["last_indexed_at"] = ts

    return [
        {"course_code": cc, "source_type": st, **vals}
        for (cc, st), vals in agg.items()
    ]


def aggregate(rows: list[dict]) -> dict[str, dict]:
    """
    Build per-course profile dicts from pre-aggregated SQL rows.
    Each row is {course_code, source_type, chunk_count, last_indexed_at}.
    """
    OFFICIAL_TYPES  = {"study_plan", "regulation", "course_description"}
    SUMMARY_TYPES   = {"upload", "telegram_export"}

    profiles: dict[str, dict] = {}
    for row in rows:
        code  = row.get("course_code")
        if not code:
            continue
        stype = row.get("source_type") or "unknown"
        count = int(row.get("chunk_count") or 0)
        ts    = row.get("last_indexed_at") or ""

        if code not in profiles:
            profiles[code] = {
                "course_code":      code,
                "total_chunks":     0,
                "exam_chunks":      0,
                "official_chunks":  0,
                "summary_chunks":   0,
                "community_chunks": 0,
                "last_indexed_at":  ts,
            }
        p = profiles[code]
        p["total_chunks"] += count
        if stype == "exam":
            p["exam_chunks"] += count
        elif stype in OFFICIAL_TYPES:
            p["official_chunks"] += count
        elif stype in SUMMARY_TYPES:
            p["summary_chunks"] += count
        else:
            p["community_chunks"] += count
        if ts and ts > p["last_indexed_at"]:
            p["last_indexed_at"] = ts

    return profiles


def build_profile_rows(profiles: dict[str, dict]) -> list[dict]:
    rows = []
    for code, p in profiles.items():
        cov = coverage_level(p["total_chunks"], p["exam_chunks"])
        rows.append({
            "course_code":      code,
            "tenant_id":        SEU_TENANT_ID,
            "total_chunks":     p["total_chunks"],
            "exam_chunks":      p["exam_chunks"],
            "official_chunks":  p["official_chunks"],
            "summary_chunks":   p["summary_chunks"],
            "community_chunks": p["community_chunks"],
            "has_exam_archives": p["exam_chunks"] > 0,
            "has_official_docs": p["official_chunks"] > 0,
            "has_summaries":     p["summary_chunks"] > 0,
            "coverage_level":   cov,
            "last_indexed_at":  p["last_indexed_at"] or None,
            "refreshed_at":     "now()",
        })
    return rows


def upsert_profiles(http: httpx.Client, rows: list[dict], dry_run: bool) -> None:
    if not rows:
        print("No profiles to upsert.")
        return
    if dry_run:
        print(f"[DRY RUN] Would upsert {len(rows)} course profiles.")
        return

    # Upsert in batches of 100
    BATCH = 100
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i+BATCH]
        r = http.post(
            f"{SUPABASE_URL}/rest/v1/course_intelligence_profiles",
            headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": "course_code,tenant_id"},
            json=batch,
            timeout=30,
        )
        if r.status_code >= 400:
            print(f"ERROR upserting batch {i//BATCH}: {r.status_code} {r.text[:200]}")
            sys.exit(1)
        print(f"  Upserted {i + len(batch)}/{len(rows)} profiles...")


def print_summary(rows: list[dict]) -> None:
    by_coverage: dict[str, list] = {"strong": [], "moderate": [], "thin": [], "none": []}
    for r in rows:
        by_coverage[r["coverage_level"]].append(r)

    print(f"\n{'='*70}")
    print(f"COURSE INTELLIGENCE PROFILES — {len(rows)} courses")
    print(f"{'='*70}")
    for level in ("strong", "moderate", "thin", "none"):
        courses = by_coverage[level]
        if not courses:
            continue
        print(f"\n[{level.upper()}] ({len(courses)} courses)")
        for c in sorted(courses, key=lambda x: -x["total_chunks"]):
            icons = []
            if c["has_exam_archives"]: icons.append("📝 exams")
            if c["has_official_docs"]: icons.append("📋 official")
            if c["has_summaries"]:     icons.append("📚 summaries")
            icon_str = " | ".join(icons) if icons else "—"
            print(f"  {c['course_code']:12s} {c['total_chunks']:4d} chunks  {icon_str}")


def main():
    parser = argparse.ArgumentParser(description="Refresh course intelligence profiles")
    parser.add_argument("--dry-run", action="store_true", help="Print summary without writing")
    parser.add_argument("--course",  type=str,  default=None, help="Refresh only one course code")
    args = parser.parse_args()

    print(f"Fetching chunk aggregates{'  for ' + args.course if args.course else ''}...")
    with httpx.Client() as http:
        raw_rows = fetch_chunk_aggregates(http, args.course)
        print(f"  {len(raw_rows):,} raw chunk rows fetched.")

        profiles = aggregate(raw_rows)
        rows = build_profile_rows(profiles)
        print(f"  {len(rows)} course profiles computed.")

        print_summary(rows)

        if not args.dry_run:
            print(f"\nUpserting {len(rows)} profiles to Supabase...")
            upsert_profiles(http, rows, dry_run=False)
            print("Done.")
        else:
            print("\n[DRY RUN] No writes performed.")


if __name__ == "__main__":
    main()
