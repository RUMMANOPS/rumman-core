#!/usr/bin/env python3
"""
import_summer_sections.py  —  Build 1A / Phase A (DRAFT, dry-run by default)

Imports the verified local Banner snapshot (Summer Term 2025-2026 = 202550,
256 sections) into Supabase `term_sections`, completing the partial 158/256 import.

SAFETY:
  * DRY-RUN IS THE DEFAULT. Nothing is written unless --apply is passed explicitly.
  * Idempotent: upsert on (term_code, crn). Re-running does not duplicate.
  * Sections present in Supabase for this term but ABSENT from the snapshot are only
    REPORTED (and optionally flagged is_active=false with --apply); never deleted.

USAGE:
  python3 scripts/import_summer_sections.py              # dry-run: report only (DEFAULT)
  python3 scripts/import_summer_sections.py --apply      # actually upsert (DO NOT run in Phase A)

Reads SUPABASE_URL + service-role key from rumman-core/.env (direct PostgREST, no ORM).
Follows repo conventions: httpx + PostgREST, single-line structured logs, flush=True.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ── Config ───────────────────────────────────────────────────────────────────
TENANT_ID   = "00000000-0000-0000-0000-000000000001"      # SEU default tenant
TERM_CODE   = "202550"
TERM_LABEL  = "Summer Term 2025-2026"
SOURCE_URL  = "https://bannservices.seu.edu.sa/StudentRegistrationSsb/ssb/term/termSelection?mode=search"
IMPORT_VERSION = 1

SNAPSHOT = Path(
    "/Users/ibrahim../Projects/0-RUMMAN/rumman-mobile/Semesters sections/"
    "Banner_sections_summer_2025_2026/02_Banner_Processed_Outputs/"
    "banner_sections_summer_2025_2026.json"
)


def log(event: str, **kw):
    parts = " | ".join(f"{k}={v}" for k, v in kw.items())
    print(f"{event} | {parts}".rstrip(" |"), flush=True)


def load_env():
    """Parse rumman-core/.env without printing secrets. Handles the tatweel in the key name."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    url = key = None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("SUPABASE_URL="):
            url = line.split("=", 1)[1].strip().strip('"').strip("'")
        elif "service_role" in line.lower() and "=" in line and key is None:
            key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not url or not key:
        log("ERROR", msg="SUPABASE_URL or service-role key not found in .env")
        sys.exit(1)
    return url, key


# ── Field mapping: Banner snapshot section → term_sections row ────────────────
def map_gender(g):
    g = (g or "").lower()
    if "female" in g:
        return "F"
    if "male" in g:
        return "M"
    return "U"


def map_delivery(scheduletype, meetings):
    types = " ".join((m.get("meetingType") or "") for m in (meetings or [])).lower()
    if "virtual" in types and "class" in types:
        return "blended"
    if "virtual" in types:
        return "virtual"
    return (scheduletype or "").lower() or "in_person"


def normalize_meetings(meetings):
    out = []
    for m in meetings or []:
        out.append({
            "day":        m.get("day"),
            "start_time": m.get("startTime"),
            "end_time":   m.get("endTime"),
            "building":   None if m.get("building") in (None, "Not shown") else m.get("building"),
            "room":       None if m.get("room") in (None, "Not shown") else m.get("room"),
            "type":       m.get("meetingType"),
        })
    return out


def to_row(s, now_iso):
    cap = s.get("capacity") or {}
    return {
        "tenant_id":         TENANT_ID,
        "term_code":         s.get("term") or TERM_CODE,
        "crn":               str(s.get("crn")),
        "section_number":    s.get("section"),            # Banner section seq, e.g. '0' (preserves section identity)
        "subject_course":    s.get("courseCode"),         # raw Banner code, e.g. ACCT101
        "course_name":       s.get("courseTitle"),
        "credit_hours":      s.get("creditHours"),
        "campus":            s.get("campusDescription"),
        # NOTE: term_sections.gender is a GENERATED column (derived from campus) — must NOT be written.
        "delivery_mode":     map_delivery(s.get("scheduleType"), s.get("meetings")),
        "capacity":          cap.get("maximumEnrollment"),
        "enrolled":          cap.get("enrollment"),
        "remaining_seats":   cap.get("seatsAvailable"),
        "open_section":      cap.get("openSection"),
        "class_meetings":    normalize_meetings(s.get("meetings")),
        # governance
        "import_version":    IMPORT_VERSION,
        "source_url":        SOURCE_URL,
        "source_term_label": TERM_LABEL,
        "is_active":         True,
        "last_imported_at":  now_iso,
    }


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="Actually write to Supabase (default: dry-run, report only)")
    args = ap.parse_args()
    apply = args.apply

    if not SNAPSHOT.exists():
        log("ERROR", msg="snapshot not found", path=str(SNAPSHOT))
        sys.exit(1)

    sections = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    local_crns = {str(s.get("crn")) for s in sections}
    log("SNAPSHOT_LOADED", local_count=len(sections), unique_crns=len(local_crns))

    url, key = load_env()
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    with httpx.Client(timeout=30, headers=headers) as db:
        # Existing CRNs for this term in Supabase
        r = db.get(f"{url}/rest/v1/term_sections",
                   params={"select": "crn", "term_code": f"eq.{TERM_CODE}", "limit": "1000"})
        r.raise_for_status()
        supa_crns = {str(x["crn"]) for x in r.json()}

    to_add    = local_crns - supa_crns
    to_update = local_crns & supa_crns
    in_supa_not_local = supa_crns - local_crns   # candidates for is_active=false (reported only)

    log("PLAN",
        local=len(local_crns),
        supabase=len(supa_crns),
        will_add=len(to_add),
        will_update=len(to_update),
        in_supabase_not_in_snapshot=len(in_supa_not_local))

    if not apply:
        log("DRY_RUN", msg="no writes performed. Re-run with --apply (after approval) to upsert.")
        if to_add:
            log("SAMPLE_ADD", crns=sorted(list(to_add))[:10])
        return

    # ── --apply path (NOT used in Phase A) ──
    now_iso = datetime.now(timezone.utc).isoformat()
    rows = [to_row(s, now_iso) for s in sections]
    added = updated = 0
    with httpx.Client(timeout=60, headers={**headers, "Prefer": "resolution=merge-duplicates"}) as db:
        for i in range(0, len(rows), 50):
            batch = rows[i:i + 50]
            resp = db.post(f"{url}/rest/v1/term_sections?on_conflict=term_code,crn", json=batch)
            if resp.status_code >= 400:
                log("BATCH_ERROR", status=resp.status_code, body=resp.text[:200])
                continue
            added += len(batch)
    log("APPLIED", upserted=added, note="counts are upsert totals; verify with count=exact query")


if __name__ == "__main__":
    main()
