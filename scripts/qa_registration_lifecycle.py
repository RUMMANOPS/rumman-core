"""
qa_registration_lifecycle.py — local QA for the registration lifecycle endpoints.

Runs the REAL FastAPI endpoints (app.student_life_api) via TestClient against:
  - an in-memory PostgREST mock (httpx.MockTransport) for all DB tables, and
  - a mocked anonymous Banner client (banner_client.get_live_sections).

NO network, NO production DB, NO Supabase, NO deploy. Pure local verification.
Run:  python3 scripts/qa_registration_lifecycle.py
"""
from __future__ import annotations

import os, sys, json
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SUPABASE_URL", "http://test.local")
os.environ.setdefault("SUPABASE_KEY", "test-key")

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import banner_client
from app import student_life_api as sla


# ───────────────────────── in-memory PostgREST mock ──────────────────────────
TABLES: dict[str, list[dict]] = {}
_SEQ = {"n": 0}


def _next_created_at() -> str:
    _SEQ["n"] += 1
    # strictly increasing so created_at.desc ordering is deterministic
    return f"2026-01-01T00:00:{_SEQ['n']:02d}.000000+00:00"


def _matches(row: dict, key: str, raw: str) -> bool:
    if "." not in raw:
        return True
    op, _, val = raw.partition(".")
    cur = row.get(key)
    if op == "eq":
        return str(cur) == val
    if op == "neq":
        return str(cur) != val
    if op == "is":
        return cur is None if val == "null" else True
    if op in ("in", "not"):
        # in.(a,b)  /  not.in.(a,b)
        inner = raw
        if op == "not":
            inner = val  # "in.(a,b)"
            _, _, val = inner.partition(".")
        lst = val.strip()
        if lst.startswith("(") and lst.endswith(")"):
            lst = lst[1:-1]
        members = {x.strip() for x in lst.split(",") if x.strip() != ""}
        present = str(cur) in members
        return present if op == "in" else (not present)
    if op == "gte":
        return cur is not None and str(cur) >= val
    if op == "lte":
        return cur is not None and str(cur) <= val
    if op == "gt":
        return cur is not None and str(cur) > val
    if op == "lt":
        return cur is not None and str(cur) < val
    return True


_NON_FILTER = {"select", "order", "limit", "offset", "on_conflict"}


def _filter_rows(table: str, params) -> list[dict]:
    rows = TABLES.get(table, [])
    out = []
    for r in rows:
        ok = True
        for k, v in params.multi_items():
            if k in _NON_FILTER:
                continue
            if not _matches(r, k, v):
                ok = False
                break
        if ok:
            out.append(r)
    # ordering (single key supported)
    order = params.get("order")
    if order:
        key = order.split(".")[0]
        desc = ".desc" in order
        out = sorted(out, key=lambda x: (x.get(key) is None, x.get(key)), reverse=desc)
    limit = params.get("limit")
    if limit:
        try:
            out = out[: int(limit)]
        except Exception:
            pass
    return out


def _handler(request: httpx.Request) -> httpx.Response:
    table = request.url.path.rsplit("/", 1)[-1]
    params = request.url.params
    prefer = request.headers.get("Prefer", "")
    minimal = "return=minimal" in prefer
    TABLES.setdefault(table, [])

    if request.method == "GET":
        return httpx.Response(200, json=_filter_rows(table, params))

    if request.method == "POST":
        body = json.loads(request.content or b"[]")
        incoming = body if isinstance(body, list) else [body]
        on_conflict = params.get("on_conflict")
        result = []
        for rec in incoming:
            rec = dict(rec)
            if on_conflict:
                keys = on_conflict.split(",")
                existing = next(
                    (r for r in TABLES[table]
                     if all(str(r.get(k)) == str(rec.get(k)) for k in keys)),
                    None,
                )
                if existing:
                    existing.update(rec)
                    result.append(existing)
                    continue
            rec.setdefault("id", f"{table[:3]}-{len(TABLES[table]) + 1}-{_SEQ['n']}")
            rec.setdefault("created_at", _next_created_at())
            TABLES[table].append(rec)
            result.append(rec)
        return httpx.Response(201, json=[] if minimal else result)

    if request.method == "PATCH":
        body = json.loads(request.content or b"{}")
        updated = []
        for r in _filter_rows(table, params):
            r.update(body)
            updated.append(r)
        return httpx.Response(200, json=[] if minimal else updated)

    if request.method == "DELETE":
        keep = [r for r in TABLES[table] if r not in _filter_rows(table, params)]
        TABLES[table] = keep
        return httpx.Response(204)

    return httpx.Response(405, json={"detail": "method not allowed in mock"})


def _fake_db() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=f"{sla.SUPABASE_URL}/rest/v1",
        headers=sla._HEADERS,
        transport=httpx.MockTransport(_handler),
        timeout=5,
    )


sla._db = _fake_db  # inject the mock DB into every endpoint


# ───────────────────────── mocked Banner live client ─────────────────────────
TERM = "202550"


def _mk(crn, subj, meetings, *, open_=True, seats=10):
    return {
        "tenant_id": sla.TENANT_ID, "term_code": TERM, "crn": str(crn),
        "subject_course": subj, "section_number": "1", "course_name": f"{subj} course",
        "credit_hours": 3, "campus": "Main", "delivery_mode": "blended",
        "class_meetings": meetings, "open_section": open_, "remaining_seats": seats,
        "import_version": 1, "source_url": "x", "source_term_label": "Summer",
        "source_hash": f"h{crn}",
    }


def _meet(day, s, e, sd="01/01/2026", ed="05/01/2026"):
    return {"day": day, "start_time": s, "end_time": e, "type": "Class",
            "room": "1", "building": "B", "campus": "Main", "start_date": sd, "end_date": ed}


# 1001 Sun 08:00-09:00 ; 1002 Sun 08:30-09:30 (clashes 1001) ; 1003 Mon 10:00-11:00 ;
# 1004 online (no meetings -> warning) ; 1005 CLOSED
LIVE = {
    "1001": _mk("1001", "CS101", [_meet("Sunday", "08:00", "09:00")]),
    "1002": _mk("1002", "CS102", [_meet("Sunday", "08:30", "09:30")]),
    "1003": _mk("1003", "CS103", [_meet("Monday", "10:00", "11:00")]),
    "1004": _mk("1004", "CS104", []),
    "1005": _mk("1005", "CS105", [_meet("Tuesday", "12:00", "13:00")], open_=False, seats=0),
}

_BANNER_MODE = {"down": False}


def _fake_get_live_sections(term):
    if _BANNER_MODE["down"]:
        raise banner_client.BannerUnavailable("mock: banner down")
    return list(LIVE.values()), len(LIVE), 0.0, True


banner_client.get_live_sections = _fake_get_live_sections


# ───────────────────────────────── harness ───────────────────────────────────
app = FastAPI()
app.include_router(sla.router)
client = TestClient(app)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {extra}" if (extra and not cond) else ""))


def reset_db():
    TABLES.clear()
    # seed active term
    TABLES["app_term_config"] = [{
        "id": "term-1", "tenant_id": sla.TENANT_ID, "active_term_code": TERM,
        "active_term_label": "Summer Term 2025-2026", "source_url": "x",
        "source_term_label": "Summer", "import_version": 1,
        "last_imported_at": None, "last_verified_at": None,
    }]
    _BANNER_MODE["down"] = False


def pin(sid, crns):
    return client.post(f"/v1/student/{sid}/registration/pin", json={"crns": crns})


# ─── scenarios ────────────────────────────────────────────────────────────────
print("\n== registration lifecycle QA ==")

reset_db()
# 1. pin success
r = pin("s1", ["1001", "1003"])
check("1. pin success -> 200", r.status_code == 200, r.text)
body = r.json() if r.status_code == 200 else {}
check("1. plan_status=pinned", body.get("plan_status") == "pinned", json.dumps(body))
check("1. pinned_count=2", body.get("pinned_count") == 2)
check("1. plan_id present", bool(body.get("plan_id")))
check("1. sections are active", all(s["status"] == "active" for s in TABLES.get("student_registered_sections", [])))
check("1. sections linked to plan_id", all(s.get("plan_id") == body.get("plan_id") for s in TABLES["student_registered_sections"]))

# 2. duplicate CRNs -> 400
r = pin("s2", ["1001", "1001"])
check("2. duplicate CRNs -> 400", r.status_code == 400, r.text)

# 3. conflict -> 409
reset_db()
r = pin("s3", ["1001", "1002"])
check("3. conflict -> 409", r.status_code == 409, r.text)
check("3. reason=schedule_conflict", (r.json().get("detail") or {}).get("reason") == "schedule_conflict")

# 4. closed/full -> 409
reset_db()
r = pin("s4", ["1005"])
check("4. closed/full -> 409", r.status_code == 409, r.text)
# missing (not in Banner) -> 409 not_found
r = pin("s4b", ["7777"])
check("4. missing-from-Banner -> 409", r.status_code == 409, r.text)

# 5. Banner unavailable -> 503
reset_db()
_BANNER_MODE["down"] = True
r = pin("s5", ["1001"])
check("5. banner unavailable -> 503", r.status_code == 503, r.text)
_BANNER_MODE["down"] = False

# 6. confirm success
reset_db()
pin("s6", ["1001", "1003"])
r = client.post("/v1/student/s6/registration/confirm", json={"acknowledged": True})
check("6. confirm success -> 200", r.status_code == 200, r.text)
check("6. plan_status=confirmed", r.json().get("plan_status") == "confirmed")
check("6. confirmed_at set", bool(r.json().get("confirmed_at")))

# 7. confirm without acknowledged -> 400
reset_db()
pin("s7", ["1001"])
r = client.post("/v1/student/s7/registration/confirm", json={"acknowledged": False})
check("7. confirm w/o acknowledged -> 400", r.status_code == 400, r.text)

# 8. confirm without pinned plan -> 409
r = client.post("/v1/student/s8_fresh/registration/confirm", json={"acknowledged": True})
check("8. confirm w/o pinned plan -> 409", r.status_code == 409, r.text)

# 9. mark-failed success + sections needs_review
reset_db()
pin("s9", ["1001", "1003"])
r = client.post("/v1/student/s9/registration/mark-failed", json={"reason": "section filled"})
check("9. mark-failed -> 200", r.status_code == 200, r.text)
check("9. plan_status=registration_failed", r.json().get("plan_status") == "registration_failed")
check("9. sections -> needs_review",
      all(s["status"] == "needs_review" for s in TABLES["student_registered_sections"]),
      json.dumps([s["status"] for s in TABLES["student_registered_sections"]]))

# 10. new pin allowed after registration_failed
r = pin("s9", ["1003"])
check("10. pin after failed -> 200", r.status_code == 200, r.text)
check("10. new plan pinned", r.json().get("plan_status") == "pinned")

# 11. pin with confirmed plan -> 409 (use replace)
reset_db()
pin("s11", ["1001"])
client.post("/v1/student/s11/registration/confirm", json={"acknowledged": True})
r = pin("s11", ["1003"])
check("11. pin w/ confirmed -> 409", r.status_code == 409, r.text)
check("11. reason=confirmed_plan_exists", (r.json().get("detail") or {}).get("reason") == "confirmed_plan_exists")

# 12. registered-sections returns plan metadata
reset_db()
pin("s12", ["1001", "1003"])
r = client.get("/v1/student/s12/registered-sections")
check("12. registered-sections -> 200", r.status_code == 200, r.text)
rs = r.json()
check("12. plan block present", isinstance(rs.get("plan"), dict))
check("12. plan.status=pinned", rs["plan"]["status"] == "pinned")
check("12. plan.confirmed=false", rs["plan"]["confirmed"] is False)
check("12. section.plan_status=pinned",
      all(s.get("plan_status") == "pinned" for s in rs["sections"]))

# 13. confirmed_only works
r = client.get("/v1/student/s12/registered-sections", params={"confirmed_only": "true"})
check("13. confirmed_only before confirm -> empty", r.json().get("has_schedule") is False, r.text)
client.post("/v1/student/s12/registration/confirm", json={"acknowledged": True})
r = client.get("/v1/student/s12/registered-sections", params={"confirmed_only": "true"})
check("13. confirmed_only after confirm -> schedule",
      r.json().get("has_schedule") is True and r.json()["section_count"] == 2, r.text)

# 13b. plan GET
r = client.get("/v1/student/s12/registration/plan")
check("13b. plan endpoint -> confirmed", r.status_code == 200 and r.json()["plan"]["status"] == "confirmed", r.text)

# 14. approve alias works as pin
reset_db()
r = client.post("/v1/student/s14/registration/approve", json={"crns": ["1001", "1003"]})
check("14. approve alias -> 200", r.status_code == 200, r.text)
ab = r.json()
check("14. approve returns approved_count (legacy)", ab.get("approved_count") == 2)
check("14. approve returns plan_id + plan_status", bool(ab.get("plan_id")) and ab.get("plan_status") == "pinned")
check("14. approve actually pinned a plan",
      any(p["status"] == "pinned" for p in TABLES.get("student_registration_plans", [])))

# 14b. warnings surfaced for online/no-time section
reset_db()
r = pin("s14b", ["1003", "1004"])
check("14b. pin with online section -> 200", r.status_code == 200, r.text)
check("14b. warning for no_meeting_times",
      any(w.get("crn") == "1004" and w.get("reason") == "no_meeting_times"
          for w in r.json().get("warnings", [])), r.text)

# 15. existing endpoints not broken
reset_db()
check("15. active-term -> 200", client.get("/v1/config/active-term").status_code == 200)
check("15. today -> 200", client.get("/v1/student/s15/today").status_code == 200)
check("15. calendar -> 200", client.get("/v1/student/s15/calendar").status_code == 200)
rv = client.post("/v1/student/s15/registration/validate", json={"crns": ["1001"]})
check("15. validate -> 200 ok", rv.status_code == 200 and rv.json().get("ok") is True, rv.text)

print(f"\n== RESULT: {len(PASS)} passed, {len(FAIL)} failed ==")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
print("ALL_PASS")
