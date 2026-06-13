#!/usr/bin/env python3
"""
concept_confusion_worker.py — Builds concept_confusion_registry from learning_events failures.

WHY:
  When a student asks about "agency_theory" in MGT401 and gets grounded=false (no answer),
  that failure is a signal: RUMMAN doesn't have enough material on this concept for this course.
  One failure is noise. 50 failures is a content gap. 50 failures on a concept that appears
  frequently in exams is a critical gap.

WHAT THIS WORKER DOES:
  1. Reads learning_events WHERE grounded=false AND concept_tags != '{}'
     over a rolling LOOKBACK window (default 90 days)
  2. Counts failures per (concept_tag × course_code) pair
  3. Looks up exam_frequency from concept_temporal_trajectory (how exam-heavy is this concept?)
  4. UPSERTs into concept_confusion_registry:
       confusion_score  = total failure count (replaces prior value — full re-aggregate)
       exam_frequency   = how many exam questions mention this concept × course
       college_canon_code = resolved from concept_temporal_trajectory or exam_questions
     The GENERATED column critical_intersection (confusion_score >= 50 AND exam_frequency >= 2)
     auto-updates on every UPSERT.

ENABLE: CONCEPT_CONFUSION_WORKER_ENABLED=true
PERIOD: CONFUSION_SLEEP_SECONDS     (default 300)
WINDOW: CONFUSION_LOOKBACK_DAYS     (default 90, set 0 for all-time)
"""
from __future__ import annotations

import os
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL  = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY  = os.environ.get("SUPABASE_KEY", "")
TENANT_ID     = "00000000-0000-0000-0000-000000000001"

_ENABLED      = os.getenv("CONCEPT_CONFUSION_WORKER_ENABLED", "").strip().lower() == "true"
SLEEP_SECONDS = int(os.getenv("CONFUSION_SLEEP_SECONDS", "300"))
LOOKBACK_DAYS = int(os.getenv("CONFUSION_LOOKBACK_DAYS", "90"))
UPSERT_BATCH  = 100

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}


def log(event: str, **kwargs):
    parts = [event] + [f"{k}={v}" for k, v in kwargs.items()]
    print(" | ".join(parts), flush=True)


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

async def _get_all(
    http: httpx.AsyncClient,
    table: str,
    params: dict,
    page_size: int = 1000,
) -> list[dict]:
    """Paginated GET from a Supabase table. Returns all matching rows."""
    results: list[dict] = []
    offset = 0
    while True:
        r = await http.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=HEADERS,
            params={**params, "limit": str(page_size), "offset": str(offset)},
        )
        if r.status_code != 200:
            log("FETCH_ERROR", table=table, status=r.status_code, body=r.text[:200])
            break
        batch = r.json()
        if not isinstance(batch, list):
            break
        results.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return results


async def _fetch_failed_events(
    http: httpx.AsyncClient,
    since: Optional[str],
) -> list[dict]:
    """Return failed learning_events with concept_tags since the given ISO timestamp."""
    params: dict = {
        "select":   "concept_tags,course_codes",
        "grounded": "eq.false",
    }
    if since:
        params["occurred_at"] = f"gte.{since}"
    return await _get_all(http, "learning_events", params)


async def _fetch_trajectory_data(
    http: httpx.AsyncClient,
) -> tuple[dict[tuple[str, str], int], dict[str, str]]:
    """
    Fetch concept_temporal_trajectory and derive two dicts:
      exam_freq:   {(concept_name, course_code): total_exam_appearances}
      college_map: {course_code: college_canon_code}
    """
    rows = await _get_all(http, "concept_temporal_trajectory", {
        "select":    "concept_name,course_code,college_canon_code,exam_appearances",
        "tenant_id": f"eq.{TENANT_ID}",
    })

    exam_freq:   dict[tuple[str, str], int] = defaultdict(int)
    college_map: dict[str, str] = {}

    for row in rows:
        concept = row.get("concept_name")
        course  = row.get("course_code")
        college = row.get("college_canon_code")
        n       = row.get("exam_appearances") or 0

        if concept and course:
            # Normalize to underscore format — intent classifier uses underscore,
            # concept_temporal_trajectory seeds from topic_tags which use spaces.
            concept_key = concept.lower().replace(" ", "_")
            exam_freq[(concept_key, course)] += n

        if course and college and course not in college_map:
            college_map[course] = college

    return dict(exam_freq), college_map


async def _resolve_college_for_course(
    http: httpx.AsyncClient,
    course_code: str,
) -> Optional[str]:
    """
    Look up college_canon_code for a course_code not in concept_temporal_trajectory.
    Uses a single exam_questions row (cheapest available lookup — no RPC needed).
    """
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/exam_questions",
        headers=HEADERS,
        params={
            "select":            "college_canon_code",
            "course_code":       f"eq.{course_code}",
            "college_canon_code": "not.is.null",
            "limit":             "1",
        },
    )
    if r.status_code == 200:
        rows = r.json()
        if rows:
            return rows[0].get("college_canon_code")
    return None


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _build_failure_counts(events: list[dict]) -> dict[tuple[str, str], int]:
    """
    Cross-product: each concept_tag × each course_code from the same event.
    Only events that have BOTH concept_tags and course_codes contribute.
    """
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for ev in events:
        tags    = [t for t in (ev.get("concept_tags") or []) if t]
        courses = [c for c in (ev.get("course_codes") or []) if c]
        if not tags or not courses:
            continue
        for tag in tags:
            for course in courses:
                counts[(tag, course)] += 1
    return dict(counts)


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

async def _upsert_rows(http: httpx.AsyncClient, rows: list[dict]) -> int:
    """Batch upsert into concept_confusion_registry. Returns rows sent."""
    if not rows:
        return 0
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/concept_confusion_registry",
        headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
        params={"on_conflict": "concept_name,course_code,tenant_id"},
        json=rows,
    )
    if r.status_code not in (200, 201, 204):
        log("UPSERT_ERROR", status=r.status_code, body=r.text[:300])
        return 0
    return len(rows)


# ---------------------------------------------------------------------------
# One run
# ---------------------------------------------------------------------------

async def run_once(http: httpx.AsyncClient) -> dict:
    since: Optional[str] = None
    if LOOKBACK_DAYS > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
        since = cutoff.isoformat()

    # 1. Pull raw events
    events = await _fetch_failed_events(http, since)
    total_events = len(events)

    # Filter to events that have concept_tags (older events written before migration 057
    # will have concept_tags=[] — skip them gracefully)
    tagged = [e for e in events if (e.get("concept_tags") or [])]
    if not tagged:
        return {"events": total_events, "tagged": 0, "pairs": 0, "upserted": 0, "critical": 0}

    # 2. Build (concept × course) failure counts
    failure_counts = _build_failure_counts(tagged)
    if not failure_counts:
        return {"events": total_events, "tagged": len(tagged), "pairs": 0, "upserted": 0, "critical": 0}

    # 3. Pull exam frequency + college map from concept_temporal_trajectory
    exam_freq, college_map = await _fetch_trajectory_data(http)

    # 4. Resolve college for any course_code not yet in college_map
    unknown_courses = {course for _, course in failure_counts if course not in college_map}
    for course in unknown_courses:
        college = await _resolve_college_for_course(http, course)
        if college:
            college_map[course] = college

    # 5. Build upsert rows
    now_iso   = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    critical_predicted = 0

    for (concept, course), count in failure_counts.items():
        ef = exam_freq.get((concept, course), 0)
        # confusion_score: capped at 100 (CHECK constraint); failed_queries = raw count
        score  = min(100.0, float(count))
        is_crit = score >= 50.0 and ef >= 2
        rows.append({
            "tenant_id":             TENANT_ID,
            "concept_name":          concept,
            "course_code":           course,
            "college_canon_code":    college_map.get(course),
            "failed_queries":        count,
            "total_queries":         count,   # only failures tracked; floor estimate
            "confusion_score":       score,
            "exam_frequency":        ef,
            "critical_intersection": is_crit,
            "trend":                 "stable",
            "last_queried_at":       now_iso,
            "computed_at":           now_iso,
            # first_seen_at intentionally omitted — preserved on UPDATE, DEFAULT now() on INSERT
        })
        if is_crit:
            critical_predicted += 1

    # 6. Batch upsert
    total_upserted = 0
    for i in range(0, len(rows), UPSERT_BATCH):
        total_upserted += await _upsert_rows(http, rows[i:i + UPSERT_BATCH])

    return {
        "events":   total_events,
        "tagged":   len(tagged),
        "pairs":    len(failure_counts),
        "upserted": total_upserted,
        "critical": critical_predicted,
    }


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def main():
    if not _ENABLED:
        print("DISABLED — set CONCEPT_CONFUSION_WORKER_ENABLED=true to activate", flush=True)
        return

    log("START",
        lookback_days=LOOKBACK_DAYS if LOOKBACK_DAYS > 0 else "all-time",
        sleep_seconds=SLEEP_SECONDS,
        batch=UPSERT_BATCH)

    async with httpx.AsyncClient(timeout=30) as http:
        while True:
            try:
                result = await run_once(http)
                if result["pairs"] > 0:
                    log("RUN_DONE",
                        events=result["events"],
                        tagged=result["tagged"],
                        pairs=result["pairs"],
                        upserted=result["upserted"],
                        critical=result["critical"])
                else:
                    log("RUN_IDLE",
                        events=result["events"],
                        tagged=result["tagged"])
            except Exception as exc:
                log("RUN_ERROR", error=str(exc)[:200])

            await asyncio.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
