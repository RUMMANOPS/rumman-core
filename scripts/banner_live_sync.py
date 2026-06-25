#!/usr/bin/env python3
"""
banner_live_sync.py — BANNER-SYNC-1

Full anonymous sync of SEU Banner sections -> term_sections
(one row per (term_code, crn); meetings inside class_meetings JSONB).

Shared Banner logic (session, fetch, normalize, hash) lives in app/banner_client.py.
Default = dry-run (no DB writes). With --apply, performs ONE guarded sync:
all guards must pass before any write; missing sections -> sync_status='not_seen' (never deleted).

Active term is read dynamically from app_term_config.active_term_code (Supabase),
with BANNER_TERM_CODE env as fallback. No hardcoded term code.
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


def resolve_term_code(url, key):
    """Resolve the active Banner term code.

    Returns a dict — always one of:
      {"ok": True,  "term_code": "<code>", "source": "app_term_config"}
      {"ok": True,  "term_code": "<code>", "source": "env"}
      {"ok": False, "reason": "active_term_read_failed", "error": "<detail>"}
      {"ok": False, "reason": "no_active_term"}

    Env fallback (BANNER_TERM_CODE) is ONLY used when:
      - DB is reachable and the query succeeded, but active_term_code is not set, OR
      - No DB credentials available at all (local/dev environment).
    If the DB read fails for any reason (network, timeout, HTTP error, malformed JSON),
    we return active_term_read_failed and the caller MUST abort — we never fall through
    to a potentially stale env var.
    """
    if url and key:
        # --- attempt DB read ---
        try:
            with httpx.Client(timeout=15, headers=sb_headers(key)) as c:
                r = c.get(f"{url}/rest/v1/app_term_config",
                          params={"select": "active_term_code",
                                  "tenant_id": f"eq.{TENANT_ID}",
                                  "limit": "1"})
        except Exception as exc:
            return {"ok": False, "reason": "active_term_read_failed",
                    "error": f"network/timeout: {str(exc)[:160]}"}

        if r.status_code != 200:
            return {"ok": False, "reason": "active_term_read_failed",
                    "error": f"HTTP {r.status_code}: {r.text[:160]}"}

        try:
            rows = r.json()
        except Exception as exc:
            return {"ok": False, "reason": "active_term_read_failed",
                    "error": f"malformed response: {str(exc)[:160]}"}

        if rows and rows[0].get("active_term_code"):
            return {"ok": True, "term_code": str(rows[0]["active_term_code"]).strip(),
                    "source": "app_term_config"}

        # DB readable but active_term_code not set — env fallback allowed
        env_code = os.environ.get("BANNER_TERM_CODE", "").strip()
        if env_code:
            return {"ok": True, "term_code": env_code, "source": "env"}
        return {"ok": False, "reason": "no_active_term"}

    # No DB credentials — local/dev only; env fallback allowed
    env_code = os.environ.get("BANNER_TERM_CODE", "").strip()
    if env_code:
        return {"ok": True, "term_code": env_code, "source": "env"}
    return {"ok": False, "reason": "no_active_term"}


def sb_headers(key, prefer=None):
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer: h["Prefer"] = prefer
    return h


def fetch_supabase_current(url, key, term_code):
    with httpx.Client(timeout=30, headers=sb_headers(key)) as c:
        r = c.get(f"{url}/rest/v1/term_sections",
                  params={"select": "crn,sync_status,source_hash,raw_changed_at",
                          "term_code": f"eq.{term_code}", "limit": "2000"})
        r.raise_for_status()
        return {str(x["crn"]): x for x in r.json()}


def check_schema(url, key):
    with httpx.Client(timeout=30, headers=sb_headers(key)) as c:
        a = c.get(f"{url}/rest/v1/banner_sync_runs", params={"select": "id", "limit": "1"})
        b = c.get(f"{url}/rest/v1/term_sections", params={"select": "sync_status", "limit": "1"})
    return a.status_code == 200 and b.status_code == 200


def latest_successful_total(url, key, term_code):
    """source_total_count of the most recent COMPLETED sync run for this term — the baseline
    for drop detection. Returns None if there is no prior successful run."""
    with httpx.Client(timeout=30, headers=sb_headers(key)) as c:
        r = c.get(f"{url}/rest/v1/banner_sync_runs", params={
            "select": "source_total_count,finished_at", "term_code": f"eq.{term_code}",
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


def _record_sync_failure(url, key, term_code, trigger, reason, error_text, total=None):
    """Best-effort: write a failed banner_sync_runs row for pre-apply failures. Never raises."""
    try:
        now = now_iso()
        with httpx.Client(timeout=20, headers=sb_headers(key, "return=minimal")) as c:
            c.post(f"{url}/rest/v1/banner_sync_runs", json={
                "tenant_id": TENANT_ID, "term_code": term_code, "status": "failed",
                "trigger": trigger, "source_total_count": total,
                "started_at": now, "finished_at": now,
                "error": f"{reason}: {str(error_text)[:460]}"})
    except Exception:
        pass


def run_apply(url, key, normalized, supa_current, total, http_ok, trigger="manual", term_code=None):
    if not term_code:
        raise ValueError("run_apply: term_code is required")
    now = now_iso()
    live = {n["crn"] for n in normalized}
    with httpx.Client(timeout=30, headers=sb_headers(key, "return=representation")) as c:
        r = c.post(f"{url}/rest/v1/banner_sync_runs", json={
            "tenant_id": TENANT_ID, "term_code": term_code, "status": "running",
            "trigger": trigger, "source_total_count": total, "started_at": now})
        r.raise_for_status(); run_id = r.json()[0]["id"]
    log(f"  sync_run_id = {run_id}")

    def _mark_run_failed(error_msg):
        """Best-effort: flip the in-flight run to failed. Never raises."""
        try:
            with httpx.Client(timeout=20, headers=sb_headers(key, "return=minimal")) as c:
                c.patch(f"{url}/rest/v1/banner_sync_runs", params={"id": f"eq.{run_id}"},
                        json={"status": "failed", "finished_at": now_iso(),
                              "error": str(error_msg)[:500]})
        except Exception:
            pass

    s, e = parse_envelope(normalized)
    _completed = False  # flipped to True only after completion PATCH succeeds

    try:
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
                    # NOTE: term_sections has NO `updated_at` column. Writing it made PostgREST reject the
                    # whole PATCH (400, error 42703) -> reconciliation silently no-op'd (not_seen=0) on every
                    # run, which is why phantom sections accumulated as active. Use last_checked_at (exists).
                    raise RuntimeError(f"upsert failed: {r.status_code}: {r.text[:200]}")
        added   = len([n for n in normalized if n["crn"] not in supa_current])
        updated = len([n for n in normalized if n["crn"] in supa_current])

        crn_csv = ",".join(sorted(live))
        with httpx.Client(timeout=60, headers=sb_headers(key, "return=representation")) as c:
            r = c.patch(f"{url}/rest/v1/term_sections",
                        params={"term_code": f"eq.{term_code}", "sync_status": "eq.active", "crn": f"not.in.({crn_csv})"},
                        json={"sync_status": "not_seen", "is_active": False, "last_checked_at": now})
            if r.status_code not in (200, 206):
                raise RuntimeError(f"reconciliation failed: {r.status_code}: {r.text[:200]}")
            not_seen = len(r.json())

        # raise_for_status() ensures a 4xx/5xx triggers _mark_run_failed — no silent zombie runs.
        with httpx.Client(timeout=30, headers=sb_headers(key, "return=minimal")) as c:
            r = c.patch(f"{url}/rest/v1/banner_sync_runs", params={"id": f"eq.{run_id}"}, json={
                "status": "completed", "finished_at": now_iso(), "sections_seen": len(normalized),
                "sections_added": added, "sections_updated": updated, "sections_not_seen": not_seen,
                "http_ok_count": http_ok, "http_error_count": 0,
                "notes": "raw snapshot stored locally only in this run (/tmp); banner-raw bucket deferred to SYNC-1b"})
            r.raise_for_status()
        _completed = True

        return run_id, added, updated, not_seen, (s, e)

    except Exception as exc:
        _mark_run_failed(str(exc))
        raise

    finally:
        # app_term_config trust markers — best-effort, only after run is confirmed completed.
        # _completed=False means we are on the failure path — skip entirely.
        # Failure of this PATCH is a warning, not a sync failure: term_sections and the run
        # status are already committed. _mark_run_failed is NOT called from here.
        if _completed:
            cfg = {"instruction_dates_source": "banner_sync",
                   "instruction_dates_verified_at": now_iso(),
                   "last_banner_discovery_at": now_iso(), "active_term_status": "verified",
                   "updated_at": now_iso(), "last_imported_at": now_iso()}
            if s: cfg["instruction_start_date"] = s[0].isoformat(); cfg["instruction_start_raw"] = s[1]
            if e: cfg["instruction_end_date"]   = e[0].isoformat(); cfg["instruction_end_raw"]   = e[1]
            try:
                with httpx.Client(timeout=30, headers=sb_headers(key, "return=minimal")) as c:
                    c.patch(f"{url}/rest/v1/app_term_config",
                            params={"tenant_id": f"eq.{TENANT_ID}"}, json=cfg)
            except Exception as cfg_exc:
                log(f"WARN: app_term_config update failed after completed run {run_id}: {str(cfg_exc)[:120]}")


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
    Returns a result dict; never raises (Banner failure -> {'ok': False, 'reason': 'banner_unavailable'}).
    Active term is resolved via resolve_term_code — DB failure aborts immediately, no env fallback."""
    url, key = load_supabase_env()
    term_result = resolve_term_code(url, key)
    if not term_result["ok"]:
        return {"ok": False, "reason": term_result["reason"],
                "error": term_result.get("error", "No active Banner term configured")}
    term_code = term_result["term_code"]
    term_source = term_result["source"]

    usid = "rmnsync" + hashlib.md5(term_code.encode()).hexdigest()[:8]
    try:
        c = banner_client._open_session()
        try:
            terms = discover_terms(c)
            tmap = {t.get("code"): t.get("description") for t in terms}
            term_in_banner = term_code in tmap
            bind_term(c, term_code, usid)
            rows, total, http_ok = fetch_all_sections(c, term_code, usid)
        finally:
            c.close()
    except BannerUnavailable as exc:
        _record_sync_failure(url, key, term_code, trigger, "banner_unavailable", str(exc)[:160])
        return {"ok": False, "reason": "banner_unavailable", "error": str(exc)[:160]}

    crns = [str(r.get("courseReferenceNumber")) for r in rows]
    distinct = set(crns)
    dups = [x for x in distinct if crns.count(x) > 1]
    normalized = [normalize_section(s, default_term=term_code) for s in rows]
    missing = [n["crn"] for n in normalized if not n["subject_course"] or not n["course_name"] or n["capacity"] is None]
    supa = fetch_supabase_current(url, key, term_code)

    # Completeness CONTRACT (not a blind floor): the snapshot is trustworthy iff Banner's own
    # totalCount is positive and we fetched every advertised row with no duplicate CRNs.
    #   - fetched < totalCount  -> partial pull (pagination cut off / session redirect) -> REJECT
    #   - fetched == totalCount == distinct, totalCount > 0 -> COMPLETE
    complete_pull = (total is not None and total > 0 and total == len(rows) == len(distinct))
    guards = {
        "term_in_banner": term_in_banner,
        "complete_pull(rows==total==distinct, total>0)": complete_pull,
        "no_dup_crns": len(dups) == 0,
        "no_missing_critical_fields": len(missing) == 0,
        "schema_present": check_schema(url, key),
    }

    # Baseline drop is a WARNING, never a blocker — a real term shrink must still apply.
    baseline = latest_successful_total(url, key, term_code)
    warnings = []
    drop_pct = None
    if baseline and baseline > 0 and total is not None:
        drop_pct = round((baseline - total) / baseline * 100, 1)
        if drop_pct >= BASELINE_DROP_WARN_PCT:
            warnings.append(f"section_count_drop: baseline={baseline} -> total={total} ({drop_pct}% fewer)")

    would_not_seen = len([crn for crn in supa
                          if supa[crn].get("sync_status") == "active" and crn not in distinct])
    if not all(guards.values()):
        failed_guards = [k for k, v in guards.items() if not v]
        _record_sync_failure(url, key, term_code, trigger, "guard_failed",
                             f"failed_guards={failed_guards} total={total} rows={len(rows)}",
                             total=total)
        return {"ok": False, "reason": "guard_failed", "guards": guards,
                "total": total, "rows": len(rows), "warnings": warnings, "baseline": baseline,
                "term_code": term_code, "term_source": term_source}
    if not apply:
        return {"ok": True, "reason": "dry_run", "total": total, "rows": len(rows),
                "guards": guards, "warnings": warnings, "baseline": baseline, "drop_pct": drop_pct,
                "would_add": len([n for n in normalized if n["crn"] not in supa]),
                "would_update": len([n for n in normalized if n["crn"] in supa]),
                "would_mark_not_seen": would_not_seen,
                "term_code": term_code, "term_source": term_source}
    if _running_lock_held(url, key, term_code, stale_lock_seconds):
        return {"ok": False, "reason": "locked"}
    try:
        run_id, added, updated, not_seen, env = run_apply(url, key, normalized, supa, total, http_ok,
                                                          trigger=trigger, term_code=term_code)
    except Exception as exc:
        # run_apply already called _mark_run_failed before re-raising; just surface the error.
        return {"ok": False, "reason": "apply_exception", "error": str(exc)[:200],
                "term_code": term_code, "term_source": term_source}
    return {"ok": True, "reason": "applied", "run_id": run_id, "added": added, "updated": updated,
            "not_seen": not_seen, "total": total, "term_code": term_code, "term_source": term_source,
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
