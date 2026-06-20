#!/usr/bin/env python3
"""
banner_live_sync.py — BANNER-SYNC-1

Anonymous, read-only fetch of SEU Banner summer sections -> upsert into RUMMAN's
term_sections (one row per (term_code, crn); meetings inside class_meetings JSONB).

Default = dry-run (no DB writes). With --apply, performs ONE guarded sync:
guards must ALL pass before any DB write; on guard failure it aborts with no writes.

SAFETY: anonymous (NO login/credentials); honest UA; timeout + bounded retry + backoff;
stops on 403/429 or login redirect; never deletes rows (missing -> sync_status='not_seen').
"""

import argparse, json, hashlib, sys, time
from datetime import datetime, timezone
from pathlib import Path
import httpx

TENANT_ID  = "00000000-0000-0000-0000-000000000001"
TERM_CODE  = "202550"
TERM_LABEL = "Summer Term 2025-2026"
BANNER     = "https://bannservices.seu.edu.sa/StudentRegistrationSsb/ssb"
SOURCE_URL = "https://bannservices.seu.edu.sa/StudentRegistrationSsb/ssb/classSearch/classSearch"
UA         = "RUMMAN-academic-tool/0.1 (student schedule helper; contact rumman.ops@gmail.com)"
PAGE_SIZE  = 300
MIN_ROWS   = 350
DAY_FLAGS  = [("sunday","Sunday"),("monday","Monday"),("tuesday","Tuesday"),
              ("wednesday","Wednesday"),("thursday","Thursday"),("friday","Friday"),("saturday","Saturday")]


def log(m): print(m, flush=True)
def now_iso(): return datetime.now(timezone.utc).isoformat()


def load_supabase_env():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    url = key = None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("SUPABASE_URL="):
            url = line.split("=", 1)[1].strip().strip('"').strip("'")
        elif "service_role" in line.lower() and "=" in line and key is None:
            key = line.split("=", 1)[1].strip().strip('"').strip("'")
    return url, key


# ── Anonymous Banner client ───────────────────────────────────────────────────
def banner_session():
    c = httpx.Client(timeout=30, headers={"User-Agent": UA}, follow_redirects=True)
    r = c.get(f"{BANNER}/classSearch/classSearch")
    if r.status_code in (403, 429):
        raise SystemExit(f"STOP: classSearch {r.status_code} (blocked).")
    return c


def get_guard(c, url, **kw):
    for attempt in range(3):
        r = c.get(url, **kw)
        if r.status_code in (403, 429):
            raise SystemExit(f"STOP: {r.status_code} on {url} (anonymous access blocked).")
        if r.status_code == 200:
            return r
        time.sleep(1.5 * (attempt + 1))
    raise SystemExit(f"STOP: repeated non-200 on {url} (last={r.status_code}).")


def discover_terms(c):
    return get_guard(c, f"{BANNER}/classSearch/getTerms",
                     params={"searchTerm": "", "offset": 1, "max": 20}).json()


def bind_term(c, usid):
    r = c.post(f"{BANNER}/term/search", params={"mode": "search"},
               data={"term": TERM_CODE, "studyPath": "", "studyPathText": "",
                     "startDatepicker": "", "endDatepicker": "", "uniqueSessionId": usid})
    if r.status_code in (403, 429):
        raise SystemExit(f"STOP: term/search {r.status_code}")


def fetch_all_sections(c, usid):
    rows, total, ok = [], None, 0
    offset = 0
    while True:
        r = get_guard(c, f"{BANNER}/searchResults/searchResults", params={
            "txt_term": TERM_CODE, "startDatepicker": "", "endDatepicker": "",
            "uniqueSessionId": usid, "pageOffset": offset, "pageMaxSize": PAGE_SIZE,
            "sortColumn": "subjectDescription", "sortDirection": "asc"})
        if "json" not in r.headers.get("content-type", ""):
            raise SystemExit("STOP: searchResults not JSON (login/session redirect?).")
        ok += 1
        d = r.json()
        total = d.get("totalCount") if total is None else total
        batch = d.get("data") or []
        rows.extend(batch)
        offset += PAGE_SIZE
        if not batch or offset >= (total or 0):
            break
        time.sleep(1.0)
    return rows, total, ok


# ── Normalization ─────────────────────────────────────────────────────────────
def fmt_time(t):
    if not t or len(str(t)) < 3: return None
    s = str(t).zfill(4); return f"{s[:2]}:{s[2:]}"


def build_class_meetings(section):
    out = []
    for mf in (section.get("meetingsFaculty") or []):
        mt = mf.get("meetingTime") or {}
        days = [name for flag, name in DAY_FLAGS if mt.get(flag)] or [None]
        for day in days:
            out.append({
                "day": day, "start_time": fmt_time(mt.get("beginTime")), "end_time": fmt_time(mt.get("endTime")),
                "type": mt.get("meetingTypeDescription"), "room": mt.get("room"),
                "building": mt.get("buildingDescription"), "campus": mt.get("campusDescription"),
                "start_date": mt.get("startDate"), "end_date": mt.get("endDate"),
            })
    return out


def derive_delivery(section, meetings):
    types = " ".join((m.get("type") or "") for m in meetings).lower()
    if "virtual" in types and "class" in types: return "blended"
    if "virtual" in types: return "virtual"
    return (section.get("instructionalMethodDescription") or section.get("scheduleTypeDescription") or "").strip() or None


def compute_hash(row):
    payload = {k: row[k] for k in row if k not in ("source_url", "source_term_label", "source_hash")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def normalize_section(section):
    meetings = build_class_meetings(section)
    row = {
        "tenant_id": TENANT_ID, "term_code": str(section.get("term") or TERM_CODE),
        "crn": str(section.get("courseReferenceNumber")),
        "subject_course": section.get("subjectCourse"), "course_name": section.get("courseTitle"),
        "credit_hours": section.get("creditHourLow") or section.get("creditHours") or section.get("creditHourHigh"),
        "section_number": section.get("sequenceNumber"),
        "capacity": section.get("maximumEnrollment"), "enrolled": section.get("enrollment"),
        # NOTE: remaining_seats may be a Banner sentinel (e.g. 9999 = uncapped/virtual) — store raw,
        # but downstream analytics & APPROVE-REVALIDATION must treat it as "practically available", not a literal count.
        "remaining_seats": section.get("seatsAvailable"), "open_section": section.get("openSection"),
        "wait_capacity": section.get("waitCapacity"), "wait_count": section.get("waitCount"),
        "wait_available": section.get("waitAvailable"), "campus": section.get("campusDescription"),
        "delivery_mode": derive_delivery(section, meetings), "part_of_term": section.get("partOfTerm"),
        "class_meetings": meetings, "source_url": SOURCE_URL, "source_term_label": TERM_LABEL,
    }
    row["source_hash"] = compute_hash(row)
    return row


# ── Supabase (read + write) ───────────────────────────────────────────────────
def sb_headers(key, prefer=None):
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer: h["Prefer"] = prefer
    return h


def fetch_supabase_current(url, key):
    with httpx.Client(timeout=30, headers=sb_headers(key)) as c:
        r = c.get(f"{url}/rest/v1/term_sections",
                  params={"select": "crn,sync_status,source_hash,raw_changed_at",
                          "term_code": f"eq.{TERM_CODE}", "limit": "2000"})
        r.raise_for_status()
        return {str(x["crn"]): x for x in r.json()}


def check_schema(url, key):
    with httpx.Client(timeout=30, headers=sb_headers(key)) as c:
        a = c.get(f"{url}/rest/v1/banner_sync_runs", params={"select": "id", "limit": "1"})
        b = c.get(f"{url}/rest/v1/term_sections", params={"select": "sync_status", "limit": "1"})
    return a.status_code == 200 and b.status_code == 200


def parse_envelope(normalized):
    starts, ends = [], []
    for n in normalized:
        for m in n["class_meetings"]:
            for raw, bucket in ((m.get("start_date"), starts), (m.get("end_date"), ends)):
                if raw:
                    try: bucket.append((datetime.strptime(raw, "%m/%d/%Y").date(), raw))
                    except Exception: pass
    s = min(starts, key=lambda x: x[0]) if starts else None
    e = max(ends,   key=lambda x: x[0]) if ends   else None
    return s, e  # each = (date, raw) or None


# ── Apply ─────────────────────────────────────────────────────────────────────
def run_apply(url, key, normalized, supa_current, total, http_ok):
    now = now_iso()
    live = {n["crn"] for n in normalized}

    # 1) sync run (running)
    with httpx.Client(timeout=30, headers=sb_headers(key, "return=representation")) as c:
        r = c.post(f"{url}/rest/v1/banner_sync_runs", json={
            "tenant_id": TENANT_ID, "term_code": TERM_CODE, "status": "running",
            "trigger": "manual", "source_total_count": total, "started_at": now})
        r.raise_for_status(); run_id = r.json()[0]["id"]
    log(f"  sync_run_id = {run_id}")

    # 2) upsert sections (uniform keys; raw_changed_at set on change, else preserve existing)
    payload = []
    for n in normalized:
        cur = supa_current.get(n["crn"])
        changed = (cur is None) or (cur.get("source_hash") != n["source_hash"])
        rec = dict(n)
        rec.update({"sync_status": "active", "is_active": True, "sync_run_id": run_id,
                    "last_checked_at": now, "last_seen_at": now,
                    "raw_changed_at": now if changed else (cur.get("raw_changed_at") if cur else now)})
        payload.append(rec)
    with httpx.Client(timeout=90, headers=sb_headers(key, "resolution=merge-duplicates,return=minimal")) as c:
        for i in range(0, len(payload), 50):
            r = c.post(f"{url}/rest/v1/term_sections?on_conflict=term_code,crn", json=payload[i:i+50])
            if r.status_code >= 400:
                raise SystemExit(f"STOP: upsert {r.status_code}: {r.text[:200]}")
    added   = len([n for n in normalized if n["crn"] not in supa_current])
    updated = len([n for n in normalized if n["crn"] in supa_current])

    # 3) not_seen for previously-active CRNs missing from live
    crn_csv = ",".join(sorted(live))
    with httpx.Client(timeout=60, headers=sb_headers(key, "return=representation")) as c:
        r = c.patch(f"{url}/rest/v1/term_sections",
                    params={"term_code": f"eq.{TERM_CODE}", "sync_status": "eq.active", "crn": f"not.in.({crn_csv})"},
                    json={"sync_status": "not_seen", "is_active": False, "updated_at": now})
        not_seen = len(r.json()) if r.status_code in (200, 206) else 0

    # 4) app_term_config envelope (instruction dates from live, Gregorian)
    s, e = parse_envelope(normalized)
    cfg = {"instruction_dates_source": "banner_sync", "instruction_dates_verified_at": now,
           "last_banner_discovery_at": now, "active_term_status": "verified", "updated_at": now,
           "last_imported_at": now}
    if s: cfg["instruction_start_date"] = s[0].isoformat(); cfg["instruction_start_raw"] = s[1]
    if e: cfg["instruction_end_date"]   = e[0].isoformat(); cfg["instruction_end_raw"]   = e[1]
    with httpx.Client(timeout=30, headers=sb_headers(key, "return=minimal")) as c:
        c.patch(f"{url}/rest/v1/app_term_config", params={"tenant_id": f"eq.{TENANT_ID}"}, json=cfg)

    # 5) finalize sync run
    with httpx.Client(timeout=30, headers=sb_headers(key, "return=minimal")) as c:
        c.patch(f"{url}/rest/v1/banner_sync_runs", params={"id": f"eq.{run_id}"}, json={
            "status": "completed", "finished_at": now_iso(), "sections_seen": len(normalized),
            "sections_added": added, "sections_updated": updated, "sections_not_seen": not_seen,
            "http_ok_count": http_ok, "http_error_count": 0,
            "notes": "raw snapshot stored locally only in this run (/tmp); banner-raw bucket deferred to SYNC-1b"})
    return run_id, added, updated, not_seen, (s, e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    usid = "rmnsync" + hashlib.md5(TERM_CODE.encode()).hexdigest()[:8]
    c = banner_session()
    terms = discover_terms(c)
    tmap = {t.get("code"): t.get("description") for t in terms}
    summer = tmap.get(TERM_CODE)
    term_ok = bool(summer and "summer" in summer.lower())
    log(f"term discovery: 202550 -> {summer!r} | confirmed={term_ok}")
    bind_term(c, usid)
    rows, total, http_ok = fetch_all_sections(c, usid)
    c.close()

    crns = [str(r.get("courseReferenceNumber")) for r in rows]
    distinct = set(crns)
    dups = [x for x in distinct if crns.count(x) > 1]
    normalized = [normalize_section(s) for s in rows]
    missing = [n["crn"] for n in normalized if not n["subject_course"] or not n["course_name"] or n["capacity"] is None]
    Path("/tmp/rmn_banner_snapshot_apply.json").write_text(json.dumps(rows[:2], ensure_ascii=False)[:4000], encoding="utf-8")

    url, key = load_supabase_env()
    supa = fetch_supabase_current(url, key)
    open_n = sum(1 for n in normalized if n["open_section"] is True)
    log(f"fetch: totalCount={total} rows={len(rows)} distinct={len(distinct)} dups={len(dups)} open={open_n} supabase_now={len(supa)}")

    # ── GUARDS ──
    guards = {
        "term==202550": term_ok,
        "total==rows==distinct": (total == len(rows) == len(distinct)),
        "rows>=350": len(rows) >= MIN_ROWS,
        "no_dup_crns": len(dups) == 0,
        "no_missing_critical_fields": len(missing) == 0,
        "schema_present": check_schema(url, key),
    }
    log("GUARDS: " + json.dumps(guards))
    if not all(guards.values()):
        log("GUARD FAILURE -> NO WRITES."); return

    if not args.apply:
        log("DRY-RUN ok. Re-run with --apply to perform the one guarded sync. NO writes done."); return

    log("=== APPLY (guarded) ===")
    run_id, added, updated, not_seen, env = run_apply(url, key, normalized, supa, total, http_ok)
    log(f"APPLIED run={run_id} added={added} updated={updated} not_seen={not_seen} "
        f"envelope={env[0][1] if env[0] else None}..{env[1][1] if env[1] else None}")


if __name__ == "__main__":
    main()
