"""
qa_d5_confirmed_schedule.py — local QA for D.5 (confirmed-schedule lecture events).

Runs the REAL /today and /calendar endpoints (app.student_life_api) via FastAPI
TestClient against an in-memory PostgREST mock. NO network, NO production DB, NO deploy.
Verifies lectures are generated ONLY from a confirmed plan, in local date/time (no UTC),
honoring meeting day/time + start/end dates, without breaking existing response keys.

Run:  python3 scripts/qa_d5_confirmed_schedule.py
"""
import os, sys, json
import datetime as _dt
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://test.local")
os.environ.setdefault("SUPABASE_KEY", "test-key")

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import student_life_api as sla

TABLES = {}


def _match(row, key, raw):
    if "." not in raw:
        return True
    op, _, val = raw.partition(".")
    cur = row.get(key)
    if op == "eq":  return str(cur) == val
    if op == "neq": return str(cur) != val
    if op == "is":  return cur is None if val == "null" else True
    if op in ("in", "not"):
        inner = raw
        if op == "not":
            inner = val; _, _, val = inner.partition(".")
        lst = val.strip().lstrip("(").rstrip(")")
        members = {x.strip() for x in lst.split(",") if x.strip()}
        present = str(cur) in members
        return present if op == "in" else (not present)
    if op == "gte": return cur is not None and str(cur) >= val
    if op == "lte": return cur is not None and str(cur) <= val
    return True


_SKIP = {"select", "order", "limit", "offset", "on_conflict"}


def _rows(table, params):
    out = [r for r in TABLES.get(table, [])
           if all(k in _SKIP or _match(r, k, v) for k, v in params.multi_items())]
    order = params.get("order")
    if order:
        k = order.split(".")[0]
        out = sorted(out, key=lambda x: (x.get(k) is None, x.get(k)), reverse=".desc" in order)
    if params.get("limit"):
        out = out[: int(params.get("limit"))]
    return out


def handler(request):
    table = request.url.path.rsplit("/", 1)[-1]
    TABLES.setdefault(table, [])
    if request.method == "GET":
        return httpx.Response(200, json=_rows(table, request.url.params))
    return httpx.Response(200, json=[])   # D.5 read paths only


sla._db = lambda: httpx.AsyncClient(
    base_url=f"{sla.SUPABASE_URL}/rest/v1", headers=sla._HEADERS,
    transport=httpx.MockTransport(handler), timeout=5)

app = FastAPI(); app.include_router(sla.router); client = TestClient(app)
TENANT = sla.TENANT_ID
PASS, FAIL = [], []


def ck(n, c, extra=""):
    (PASS if c else FAIL).append(n)
    print(f"  [{'PASS' if c else 'FAIL'}] {n}" + (f"  -- {extra}" if extra and not c else ""))


DAYNAME = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def reset(plan_status=None, meetings_by_section=None):
    TABLES.clear()
    TABLES["app_term_config"] = [{"id": "t1", "tenant_id": TENANT, "active_term_code": "202550",
        "active_term_label": "Summer", "instruction_start_date": "2026-01-01",
        "instruction_end_date": "2026-12-31", "last_imported_at": "2026-06-20T00:00:00+00:00"}]
    TABLES["student_registration_plans"] = []
    TABLES["student_registered_sections"] = []
    if plan_status:
        TABLES["student_registration_plans"] = [{"id": "plan1", "student_id": "s1", "tenant_id": TENANT,
            "term_code": "202550", "status": plan_status, "created_at": "2026-01-01T00:00:01+00:00",
            "crns": ["A", "B"]}]
        for i, (crn, course, meetings) in enumerate(meetings_by_section or []):
            TABLES["student_registered_sections"].append({
                "id": f"sec{i}", "student_id": "s1", "tenant_id": TENANT, "term_code": "202550",
                "crn": crn, "section_number": "D01", "course_code": course, "banner_course_code": course,
                "course_name": f"{course} course", "campus": "Main", "delivery_mode": "Blended",
                "status": "active", "plan_id": "plan1", "class_meetings": meetings})


def meet(day, s, e, sd="01/01/2026", ed="12/31/2026", mtype="Class", room="101"):
    return {"day": day, "start_time": s, "end_time": e, "type": mtype, "room": room,
            "building": "B", "campus": "Main", "start_date": sd, "end_date": ed}


print("\n== D.5 confirmed-schedule lecture events QA ==")

# 1. no plan
reset(None)
t = client.get("/v1/student/s1/today").json()
c = client.get("/v1/student/s1/calendar").json()
ck("1. no plan -> today empty + status none", t.get("lectures_today") == [] and t.get("schedule_status") == "none")
ck("1. no plan -> calendar empty + status none", c.get("lectures") == [] and c.get("schedule_status") == "none")

# 2. pinned
reset("pinned", [("A", "MGT201", [meet("Sunday", "18:00", "20:00")])])
t = client.get("/v1/student/s1/today").json()
c = client.get("/v1/student/s1/calendar").json()
ck("2. pinned -> today empty + status pinned", t.get("lectures_today") == [] and t.get("schedule_status") == "pinned")
ck("2. pinned -> calendar empty + status pinned", c.get("lectures") == [] and c.get("schedule_status") == "pinned")

# 3-7. confirmed: multi-meeting + online(no-time) + out-of-range section
reset("confirmed", [
    ("A", "MGT201", [meet("Sunday", "18:00", "20:00"), meet("Tuesday", "18:00", "20:00")]),
    ("B", "CS101", [{"day": "Monday", "start_time": None, "end_time": None, "type": "Virtual"}]),
    ("C", "ACC110", [meet("Wednesday", "10:00", "11:00", sd="01/01/2026", ed="01/10/2026")]),
])
c = client.get("/v1/student/s1/calendar", params={"from_date": "2026-06-21", "days": 14}).json()
lec = c.get("lectures", [])
ck("3. confirmed -> lectures present", c.get("schedule_status") == "confirmed" and len(lec) > 0, f"n={len(lec)}")
win_lo, win_hi = date(2026, 6, 21), date(2026, 7, 5)
ck("4. all dates within window", all(win_lo <= date.fromisoformat(e["date"]) <= win_hi for e in lec))
ck("4b. weekday matches meeting day", all(DAYNAME[date.fromisoformat(e["date"]).weekday()] in ("Sunday", "Tuesday") for e in lec))
ck("5. out-of-window section C excluded", all(e["course_code"] != "ACC110" for e in lec))
ck("6. multi-meeting -> Sunday & Tuesday both present",
   any(DAYNAME[date.fromisoformat(e["date"]).weekday()] == "Sunday" for e in lec) and
   any(DAYNAME[date.fromisoformat(e["date"]).weekday()] == "Tuesday" for e in lec))
ck("7. online/no-time section excluded", all(e["course_code"] != "CS101" for e in lec))
e0 = lec[0]
ck("7b. event shape local date/time (no UTC)",
   {"event_type", "source", "date", "start_time", "end_time", "course_code", "crn", "meeting_type"}.issubset(e0.keys())
   and e0["start_time"] == "18:00" and "T" not in e0["date"] and "Z" not in e0["start_time"], json.dumps(e0))

# 8. existing keys intact
t = client.get("/v1/student/s1/today")
ck("8. /today 200 + old keys intact", t.status_code == 200 and all(k in t.json() for k in
   ["urgent", "tasks_due_today", "calendar_events", "academic_events", "active_courses"]))
c = client.get("/v1/student/s1/calendar")
ck("8. /calendar 200 + old keys intact", c.status_code == 200 and all(k in c.json() for k in ["from", "to", "personal", "official", "tasks"]))

# 9. today's lecture appears for a meeting on TODAY's weekday
today_name = DAYNAME[_dt.date.today().weekday()]
reset("confirmed", [("A", "MGT201", [meet(today_name, "18:00", "20:00")])])
t = client.get("/v1/student/s1/today").json()
ck("9. confirmed -> lectures_today has today's class",
   len(t.get("lectures_today", [])) >= 1 and t.get("schedule_status") == "confirmed",
   f"today={today_name} n={len(t.get('lectures_today', []))}")

print(f"\n== RESULT: {len(PASS)} passed, {len(FAIL)} failed ==")
if FAIL:
    print("FAILED:", FAIL); sys.exit(1)
print("ALL_PASS")
