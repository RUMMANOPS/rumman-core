#!/usr/bin/env python3
"""
banner_live_sync.py — BANNER-SYNC-1

Full anonymous sync of SEU Banner summer sections -> term_sections
(one row per (term_code, crn); meetings inside class_meetings JSONB).

Shared Banner logic (session, fetch, normalize, hash) lives in app/banner_client.py.
Default = dry-run (no DB writes). With --apply, performs ONE guarded sync:
all guards must pass before any write; missing sections -> sync_status='not_seen' (never deleted).
"""
import argparse, json, sys, hashlib, os
from datetime import datetime, timezone, timedelta
from pathlib import Path
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import banner_client
from app.banner_client import (BannerUnavailable, discover_terms, bind_term,
                               fetch_all_sections, normalize_section)

TENANT_ID = "00000000-0000-0000-0000-000000000001"
TERM_CODE = "202550"

# Completeness is judged by Banner's OWN totalCount, not a blind floor. A pull is complete
# iff we fetched every page Banner advertised (fetched == totalCount == distinct CRNs) and
# totalCount > 0. A legitimately shrinking term (e.g. summer: 243 sections) must pass.
# The old absolute MIN_ROWS=350 floor wrongly rejected the real summer term — removed as a
# blocker. A large drop vs the last successful sync is surfaced as a NON-BLOCKING warning so
# a human notices a possible Banner glitch, without freezing the data.
BASELINE_DROP_WARN_PCT = 40   # warn (do not block) if section count falls >=40% vs last good sync


def log(m): print(m, flush=True)
def now_iso(): return datetime.now(timezone.utc).isoformat()


def load_supabase_env():
    # Prefer process env (Railway injects vars; there is NO .env file in the deployed container).
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if url and key:
        return url, key
    # Fallback: local .env file (developer machine only)
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("SUPABASE_URL=") and not url:
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
            elif "service_role" in line.lower() and "=" in line and not key:
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    return url, key


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


def latest_successful_total(url, key):
    """source_total_count of the most recent COMPLETED sync run for this term — the baseline
    for drop detection. Returns None if there is no prior successful run."""
    with httpx.Client(timeout=30, headers=sb_headers(key)) as c:
        r = c.get(f"{url}/rest/v1/banner_sync_runs", params={
            "select": "source_total_count,finished_at", "term_code": f"eq.{TERM_CODE}",
            "status": "eq.completed", "order": "finished_at.desc", "limit": "1"})
        if r.status_code == 200 and r.json():
            return r.json()[0].get("source_total_count")
    return None


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
    return s, e


def run_apply(url, key, normalized, supa_current, total, http_ok, trigger="manual"):
    now = now_iso()
    live = {n["crn"] for n in normalized}
    with httpx.Client(timeout=30, headers=sb_headers(key, "return=representation")) as c:
        r = c.post(f"{url}/rest/v1/banner_sync_runs", json={
            "tenant_id": TENANT_ID, "term_code": TERM_CODE, "status": "running",
            "trigger": trigger, "source_total_count": total, "started_at": now})
        r.raise_for_status(); run_id = r.json()[0]["id"]
    log(f"  sync_run_id = {run_id}")

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

    crn_csv = ",".join(sorted(live))
    with httpx.Client(timeout=60, headers=sb_headers(key, "return=representation")) as c:
        # NOTE: term_sections has NO `updated_at` column. Writing it made PostgREST reject the
        # whole PATCH (400, error 42703) -> reconciliation silently no-op'd (not_seen=0) on every
        # run, which is why phantom sections accumulated as active. Use last_checked_at (exists).
        r = c.patch(f"{url}/rest/v1/term_sections",
                    params={"term_code": f"eq.{TERM_CODE}", "sync_status": "eq.active", "crn": f"not.in.({crn_csv})"},
                    json={"sync_status": "not_seen", "is_active": False, "last_checked_at": now})
        if r.status_code not in (200, 206):
            raise SystemExit(f"STOP: reconciliation PATCH {r.status_code}: {r.text[:200]}")
        not_seen = len(r.json())

    s, e = parse_envelope(normalized)
    cfg = {"instruction_dates_source": "banner_sync", "instruction_dates_verified_at": now,
           "last_banner_discovery_at": now, "active_term_status": "verified", "updated_at": now,
           "last_imported_at": now}
    if s: cfg["instruction_start_date"] = s[0].isoformat(); cfg["instruction_start_raw"] = s[1]
    if e: cfg["instruction_end_date"]   = e[0].isoformat(); cfg["instruction_end_raw"]   = e[1]
    with httpx.Client(timeout=30, headers=sb_headers(key, "return=minimal")) as c:
        c.patch(f"{url}/rest/v1/app_term_config", params={"tenant_id": f"eq.{TENANT_ID}"}, json=cfg)

    with httpx.Client(timeout=30, headers=sb_headers(key, "return=minimal")) as c:
        c.patch(f"{url}/rest/v1/banner_sync_runs", params={"id": f"eq.{run_id}"}, json={
            "status": "completed", "finished_at": now_iso(), "sections_seen": len(normalized),
            "sections_added": added, "sections_updated": updated, "sections_not_seen": not_seen,
            "http_ok_count": http_ok, "http_error_count": 0,
            "notes": "raw snapshot stored locally only in this run (/tmp); banner-raw bucket deferred to SYNC-1b"})
    return run_id, added, updated, not_seen, (s, e)


def _running_lock_held(url, key, term, stale_seconds):
    """Row-based mutex: True if a fresh banner_sync_runs row is still 'running' for this term.
    (A true pg_advisory_lock needs a direct PG session; our stack is PostgREST-only, so we use
    a DB-row mutex — sufficient for the single-instance serial worker.)"""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)).isoformat()
    with httpx.Client(timeout=20, headers=sb_headers(key)) as c:
        r = c.get(f"{url}/rest/v1/banner_sync_runs", params={
            "select": "id", "term_code": f"eq.{term}", "status": "eq.running",
            "started_at": f"gte.{cutoff}", "limit": "1"})
        return r.status_code == 200 and len(r.json()) > 0


def run_sync_once(apply=False, trigger="scheduled", stale_lock_seconds=600):
    """One guarded sync cycle. Shared by the CLI (--apply) and app/banner_sync_worker.py.
    Returns a result dict; never raises (Banner failure -> {'ok': False, 'reason': 'banner_unavailable'})."""
    usid = "rmnsync" + hashlib.md5(TERM_CODE.encode()).hexdigest()[:8]
    try:
        c = banner_client._open_session()
        try:
            terms = discover_terms(c)
            tmap = {t.get("code"): t.get("description") for t in terms}
            summer = tmap.get(TERM_CODE)
            term_ok = bool(summer and "summer" in summer.lower())
            bind_term(c, TERM_CODE, usid)
            rows, total, http_ok = fetch_all_sections(c, TERM_CODE, usid)
        finally:
            c.close()
    except BannerUnavailable as exc:
        return {"ok": False, "reason": "banner_unavailable", "error": str(exc)[:160]}

    crns = [str(r.get("courseReferenceNumber")) for r in rows]
    distinct = set(crns)
    dups = [x for x in distinct if crns.count(x) > 1]
    normalized = [normalize_section(s, default_term=TERM_CODE) for s in rows]
    missing = [n["crn"] for n in normalized if not n["subject_course"] or not n["course_name"] or n["capacity"] is None]
    url, key = load_supabase_env()
    supa = fetch_supabase_current(url, key)

    # Completeness CONTRACT (not a blind floor): the snapshot is trustworthy iff Banner's own
    # totalCount is positive and we fetched every advertised row with no duplicate CRNs.
    #   - fetched < totalCount  -> partial pull (pagination cut off / session redirect) -> REJECT
    #   - fetched == totalCount == distinct, totalCount > 0 -> COMPLETE (accept, even if 243)
    complete_pull = (total is not None and total > 0 and total == len(rows) == len(distinct))
    guards = {
        "term==202550": term_ok,
        "complete_pull(rows==total==distinct, total>0)": complete_pull,
        "no_dup_crns": len(dups) == 0,
        "no_missing_critical_fields": len(missing) == 0,
        "schema_present": check_schema(url, key),
    }

    # Baseline drop is a WARNING, never a blocker — a real term shrink must still apply.
    baseline = latest_successful_total(url, key)
    warnings = []
    drop_pct = None
    if baseline and baseline > 0 and total is not None:
        drop_pct = round((baseline - total) / baseline * 100, 1)
        if drop_pct >= BASELINE_DROP_WARN_PCT:
            warnings.append(f"section_count_drop: baseline={baseline} -> total={total} ({drop_pct}% fewer)")

    would_not_seen = len([crn for crn in supa
                          if supa[crn].get("sync_status") == "active" and crn not in distinct])
    if not all(guards.values()):
        return {"ok": False, "reason": "guard_failed", "guards": guards,
                "total": total, "rows": len(rows), "warnings": warnings, "baseline": baseline}
    if not apply:
        return {"ok": True, "reason": "dry_run", "total": total, "rows": len(rows),
                "guards": guards, "warnings": warnings, "baseline": baseline, "drop_pct": drop_pct,
                "would_add": len([n for n in normalized if n["crn"] not in supa]),
                "would_update": len([n for n in normalized if n["crn"] in supa]),
                "would_mark_not_seen": would_not_seen}
    if _running_lock_held(url, key, TERM_CODE, stale_lock_seconds):
        return {"ok": False, "reason": "locked"}
    run_id, added, updated, not_seen, env = run_apply(url, key, normalized, supa, total, http_ok, trigger=trigger)
    return {"ok": True, "reason": "applied", "run_id": run_id, "added": added, "updated": updated,
            "not_seen": not_seen, "total": total,
            "instruction_envelope": [env[0][1] if env[0] else None, env[1][1] if env[1] else None]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    res = run_sync_once(apply=args.apply, trigger="manual")
    log("GUARDS_OK" if res.get("reason") != "guard_failed" else "GUARD FAILURE -> NO WRITES.")
    log(json.dumps(res, ensure_ascii=False, default=str))
    if res.get("reason") == "dry_run":
        log("DRY-RUN ok. NO writes done.")


if __name__ == "__main__":
    main()
