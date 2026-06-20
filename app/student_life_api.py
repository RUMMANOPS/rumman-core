"""
student_life_api.py — RUMMAN Student Life OS API

Endpoints for the Student Life OS:
  GET  /v1/student/{student_id}/today              Home Command Center
  GET  /v1/student/{student_id}/calendar           Full calendar (events + tasks)
  POST /v1/student/{student_id}/tasks              Create task
  GET  /v1/student/{student_id}/tasks              List tasks
  PATCH /v1/student/{student_id}/tasks/{task_id}   Update task (done, snooze, etc.)
  DELETE /v1/student/{student_id}/tasks/{task_id}  Delete task
  GET  /v1/student/{student_id}/inbox              Ranked inbox (announcements)
  GET  /v1/student/{student_id}/requests           List requests
  POST /v1/student/{student_id}/requests/start     Start a new request flow
  PATCH /v1/student/{student_id}/requests/{req_id} Update request (submit, add fields)
  GET  /v1/request-types                           All available request types
  GET  /v1/student/{student_id}/notifications      Notification feed
  POST /v1/student/{student_id}/notifications/{id}/seen   Mark seen
  GET  /v1/courses/{course_code}/intelligence      Course Intelligence for student
  GET  /v1/founder/cockpit                         Founder platform health
"""
from __future__ import annotations

import os
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app import banner_client

load_dotenv()

SUPABASE_URL    = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY    = os.environ.get("SUPABASE_KEY", "")
TENANT_ID       = "00000000-0000-0000-0000-000000000001"
SEARCH_API_BASE = os.environ.get("SEARCH_API_URL", "https://search-production-8a18.up.railway.app").rstrip("/")

_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

router = APIRouter(prefix="/v1", tags=["student-life"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=f"{SUPABASE_URL}/rest/v1",
        headers=_HEADERS,
        timeout=15,
    )


async def _get(client: httpx.AsyncClient, table: str, params: dict) -> list[dict]:
    r = await client.get(f"/{table}", params=params)
    if r.status_code not in (200, 206):
        raise HTTPException(r.status_code, detail=r.text[:300])
    data = r.json()
    return data if isinstance(data, list) else []


async def _post(client: httpx.AsyncClient, table: str, payload: dict) -> dict:
    r = await client.post(f"/{table}", json=payload)
    if r.status_code not in (200, 201):
        raise HTTPException(r.status_code, detail=r.text[:300])
    data = r.json()
    return data[0] if isinstance(data, list) and data else data


async def _patch(client: httpx.AsyncClient, table: str, filters: dict, payload: dict) -> list[dict]:
    params = {k: f"eq.{v}" for k, v in filters.items()}
    r = await client.patch(f"/{table}", params=params, json=payload)
    if r.status_code not in (200, 204):
        raise HTTPException(r.status_code, detail=r.text[:300])
    data = r.json()
    return data if isinstance(data, list) else []


async def _delete(client: httpx.AsyncClient, table: str, filters: dict):
    params = {k: f"eq.{v}" for k, v in filters.items()}
    r = await client.delete(f"/{table}", params=params)
    if r.status_code not in (200, 204):
        raise HTTPException(r.status_code, detail=r.text[:300])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_from_now(n: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=n)).isoformat()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class TaskCreate(BaseModel):
    title:          str                = Field(..., min_length=1, max_length=500)
    task_type:      str                = "personal"
    priority:       int                = Field(default=2, ge=1, le=3)
    due_at:         Optional[str]      = None
    opens_at:       Optional[str]      = None
    closes_at:      Optional[str]      = None
    course_code:    Optional[str]      = None
    notes:          Optional[str]      = None
    remind_at:      Optional[str]      = None
    exam_date:      Optional[str]      = None


class TaskUpdate(BaseModel):
    status:         Optional[str]      = None
    title:          Optional[str]      = None
    priority:       Optional[int]      = None
    due_at:         Optional[str]      = None
    opens_at:       Optional[str]      = None
    closes_at:      Optional[str]      = None
    snoozed_until:  Optional[str]      = None
    notes:          Optional[str]      = None
    acted_on_at:    Optional[str]      = None


class RequestStart(BaseModel):
    request_type:   str
    course_code:    Optional[str]      = None


class RequestUpdate(BaseModel):
    status:             Optional[str]  = None
    collected_fields:   Optional[dict] = None
    body_final:         Optional[str]  = None
    target_name:        Optional[str]  = None
    conversation_turn:  Optional[dict] = None   # {role, content}


class RegistrationApprove(BaseModel):
    # Client sends ONLY the chosen CRNs. All section data is hydrated server-side
    # from term_sections (the source of truth) — mobile-sent fields are NOT trusted.
    crns:        list[str]          = Field(..., description="CRNs the student approved")
    term_code:   Optional[str]      = None   # defaults to app_term_config.active_term_code


class RegistrationPatch(BaseModel):
    status:      str                = Field(..., description="active|dropped|needs_review|approved|planned")


class ConfirmBody(BaseModel):
    # Student attests they completed the official Banner registration. RUMMAN never registers for them.
    plan_id:      Optional[str]     = None   # defaults to the active pinned plan for the term
    term_code:    Optional[str]     = None
    acknowledged: bool              = Field(default=False, description="must be true: student confirms they registered in Banner themselves")


class MarkFailedBody(BaseModel):
    plan_id:      Optional[str]     = None   # defaults to the active pinned plan for the term
    term_code:    Optional[str]     = None
    reason:       Optional[str]     = None


# ---------------------------------------------------------------------------
# Active Courses / Registration  (ACTIVE_COURSES_SOURCE = student_registered_sections)
#   Onboarding is NEVER the source of active courses — only the approved schedule.
# ---------------------------------------------------------------------------

_VALID_REG_STATUS = {"active", "approved", "planned", "dropped", "needs_review"}


async def _resolve_active_term(db: httpx.AsyncClient) -> Optional[str]:
    rows = await _get(db, "app_term_config", {
        "select":    "active_term_code",
        "tenant_id": f"eq.{TENANT_ID}",
        "limit":     "1",
    })
    return rows[0]["active_term_code"] if rows else None


@router.get("/config/active-term")
async def get_active_term():
    """The currently active term — mobile reads this instead of a hardcoded TERM_CODE."""
    async with _db() as db:
        rows = await _get(db, "app_term_config", {
            "select":    "active_term_code,active_term_label,source_url,source_term_label,"
                         "import_version,last_imported_at,last_verified_at",
            "tenant_id": f"eq.{TENANT_ID}",
            "limit":     "1",
        })
    if not rows:
        raise HTTPException(404, detail="no active term configured")
    return rows[0]


@router.get("/student/{student_id}/registered-sections")
async def registered_sections(
    student_id:      str,
    term:            Optional[str] = Query(default=None),
    include_dropped: bool          = Query(default=False),
    confirmed_only:  bool          = Query(default=False,
        description="When true, return ONLY sections of a CONFIRMED plan (source of truth for Courses/Today/Calendar)."),
):
    """
    The student's schedule + a plan-lifecycle summary. Returns raw sections (for
    lectures/calendar) AND sections grouped into courses (for the Courses screen).

    Lifecycle:
      - plan.status pinned   = pre-confirmed in RUMMAN (NOT yet official) — shown by the
        registration screen, but Courses/Today/Calendar must NOT treat it as registered.
      - plan.status confirmed = student registered in Banner — the source of truth.
    confirmed_only=true (used later by Courses/Today/Calendar) returns the schedule ONLY
    when a confirmed plan exists; otherwise has_schedule=false.
    Empty schedule -> has_schedule=false (Courses screen shows the Smart-Registration CTA).
    """
    async with _db() as db:
        if term is None:
            term = await _resolve_active_term(db)
        plan = await _get_active_plan(db, student_id, term) if term else None
        plan_confirmed = bool(plan and plan.get("status") == "confirmed")

        if confirmed_only and not plan_confirmed:
            return {
                "term_code":     term,
                "has_schedule":  False,
                "section_count": 0,
                "course_count":  0,
                "courses":       [],
                "sections":      [],
                "plan":          {"id": (plan["id"] if plan else None),
                                  "status": (plan["status"] if plan else None),
                                  "confirmed": False},
                "confirmed_only": True,
            }

        params: dict[str, Any] = {
            "select":     "*",
            "student_id": f"eq.{student_id}",
            "order":      "course_code.asc,crn.asc",
            "limit":      "500",
        }
        if term:
            params["term_code"] = f"eq.{term}"
        if not include_dropped:
            params["status"] = "neq.dropped"
        if confirmed_only and plan_confirmed:
            params["plan_id"] = f"eq.{plan['id']}"
        rows = await _get(db, "student_registered_sections", params)

    active_plan_id = plan["id"] if plan else None
    active_plan_status = plan["status"] if plan else None

    def _section_plan_status(r: dict) -> Optional[str]:
        # Only sections linked to the current active plan inherit its lifecycle status.
        return active_plan_status if (active_plan_id and r.get("plan_id") == active_plan_id) else None

    courses: dict[str, dict] = {}
    for r in rows:
        r["plan_status"] = _section_plan_status(r)
        cc = r.get("course_code") or r.get("banner_course_code")
        c = courses.setdefault(cc, {
            "course_code":        cc,
            "banner_course_code": r.get("banner_course_code"),
            "course_name":        r.get("course_name"),
            "credit_hours":       r.get("credit_hours"),
            "crns":               [],
            "sections":           [],
        })
        c["crns"].append(r.get("crn"))
        c["sections"].append({
            "crn":            r.get("crn"),
            "section_number": r.get("section_number"),
            "class_meetings": r.get("class_meetings"),
            "delivery_mode":  r.get("delivery_mode"),
            "campus":         r.get("campus"),
            "status":         r.get("status"),
            "plan_id":        r.get("plan_id"),
            "plan_status":    r.get("plan_status"),
        })

    return {
        "term_code":     term,
        "has_schedule":  len(rows) > 0,
        "section_count": len(rows),
        "course_count":  len(courses),
        "courses":       list(courses.values()),
        "sections":      rows,
        "plan": {
            "id":        active_plan_id,
            "status":    active_plan_status,
            "confirmed": plan_confirmed,
        },
        "confirmed_only": confirmed_only,
    }


# ── Live pre-approval re-check (APPROVE-REVALIDATION-1) ───────────────────────
# Banner does NOT support CRN-level filtering, so we fetch ALL sections (short-TTL
# cached) and filter the requested CRNs in memory. Fail-closed: no silent fallback.

async def _persist_pre_approval(db: httpx.AsyncClient, term: str, chosen_norm: list[dict], total):
    """Upsert the freshly re-checked CRNs into term_sections + record a pre_approval sync run."""
    if not chosen_norm:
        return
    now = _now_iso()
    run = await _post(db, "banner_sync_runs", {
        "tenant_id": TENANT_ID, "term_code": term, "status": "completed", "trigger": "pre_approval",
        "source_total_count": total, "sections_seen": len(chosen_norm),
        "started_at": now, "finished_at": now,
    })
    run_id = run.get("id") if isinstance(run, dict) else None
    rows = []
    for n in chosen_norm:
        rec = dict(n)
        rec.update({"sync_status": "active", "is_active": True, "sync_run_id": run_id,
                    "last_checked_at": now, "last_seen_at": now, "raw_changed_at": now})
        rows.append(rec)
    await db.post("/term_sections?on_conflict=term_code,crn", json=rows,
                  headers={"Prefer": "resolution=merge-duplicates,return=minimal"})


async def _revalidate_live(db: httpx.AsyncClient, term: str, crns: list[str], persist: bool = False) -> dict:
    """Live Banner re-check of the given CRNs. Never raises — returns a result dict.
    persist=False (default, used by /validate) => pure read, NO DB write.
    persist=True (used by /approve) => best-effort refresh of the chosen CRNs + pre_approval run."""
    try:
        normalized, total, cache_age, _did = await asyncio.to_thread(banner_client.get_live_sections, term)
    except banner_client.BannerUnavailable as exc:
        return {"live_checked": False, "ok": False, "term_code": term, "cache_age_seconds": None,
                "sections": [], "failed_crns": crns,
                "message_ar": "تعذّر التحقق اللحظي من توفّر الشعب. حاول مرة أخرى بعد قليل.",
                "error": str(exc)[:160]}
    live_map = {n["crn"]: n for n in normalized}
    ok, sections, failed = banner_client.evaluate_crns(crns, live_map)
    if persist:
        chosen = [live_map[c] for c in crns if c in live_map]
        try:
            await _persist_pre_approval(db, term, chosen, total)
        except Exception:
            pass  # persistence is best-effort; the safety verdict comes from live data, not the DB write
    return {"live_checked": True, "ok": ok, "term_code": term,
            "cache_age_seconds": round(cache_age, 1), "sections": sections, "failed_crns": failed,
            "message_ar": None if ok else "بعض الشعب امتلأت أو أُغلقت للتو. حدّث الشعب وأعد توليد الجدول."}


@router.post("/student/{student_id}/registration/validate")
async def validate_registration(student_id: str, body: RegistrationApprove):
    """Live availability check for a set of CRNs. Always returns 200; ok=false carries reasons.
    approve() calls this SAME logic internally — validation cannot be bypassed."""
    raw  = [str(c).strip() for c in (body.crns or []) if str(c).strip()]
    crns = list(dict.fromkeys(raw))
    if not crns:
        raise HTTPException(400, detail="no crns provided")
    if len(crns) != len(raw):
        raise HTTPException(400, detail="duplicate CRNs in request")
    async with _db() as db:
        term = body.term_code or await _resolve_active_term(db)
        if not term:
            raise HTTPException(400, detail="no active term configured")
        val = await _revalidate_live(db, term, crns)
    return {**val, "validated_at": _now_iso()}


# ── Conflict detection (pure, unit-testable) ──────────────────────────────────
# class_meetings entries look like:
#   {day, start_time "HH:MM", end_time "HH:MM", type, room, building, campus, start_date, end_date}
# Online/asynchronous meetings carry null times -> they are skipped for conflict and warned.

def _hhmm_to_min(t) -> Optional[int]:
    if not t or not isinstance(t, str) or ":" not in t:
        return None
    try:
        h, m = t.split(":")[:2]
        return int(h) * 60 + int(m)
    except Exception:
        return None


def _parse_meeting_date(s):
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y", "%m-%d-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            continue
    return None


def _date_ranges_overlap(a_start, a_end, b_start, b_end) -> bool:
    # Missing dates -> assume overlap (conservative: never hide a real time clash).
    if not (a_start and a_end and b_start and b_end):
        return True
    return a_start <= b_end and b_start <= a_end


def _timed_meetings(section: dict) -> list[dict]:
    """Meetings with a weekday AND both start/end times. Untimed/online meetings excluded."""
    out = []
    for m in (section.get("class_meetings") or []):
        day = m.get("day")
        s   = _hhmm_to_min(m.get("start_time"))
        e   = _hhmm_to_min(m.get("end_time"))
        if day and s is not None and e is not None and e > s:
            out.append({
                "day":        str(day).lower(),
                "start":      s, "end": e,
                "start_time": m.get("start_time"), "end_time": m.get("end_time"),
                "start_date": _parse_meeting_date(m.get("start_date")),
                "end_date":   _parse_meeting_date(m.get("end_date")),
            })
    return out


def _conflict_check(sections: list[dict]) -> tuple[list[dict], list[dict]]:
    """sections: hydrated term_sections rows (crn + class_meetings).
    Conflict iff two CRNs share a weekday AND overlapping date-range AND overlapping time.
    Returns (conflicts[], warnings[]). Sections with no timed meeting get a no_meeting_times warning."""
    timed: dict[str, list[dict]] = {}
    warnings: list[dict] = []
    for s in sections:
        crn = str(s.get("crn"))
        tm  = _timed_meetings(s)
        timed[crn] = tm
        if not tm:
            warnings.append({
                "crn": crn, "reason": "no_meeting_times",
                "message_ar": "تعذّر التحقق من تعارض هذه الشعبة (لا أوقات لقاء واضحة — قد تكون عن بُعد).",
            })
    conflicts: list[dict] = []
    crns = list(timed.keys())
    for i in range(len(crns)):
        for j in range(i + 1, len(crns)):
            for m1 in timed[crns[i]]:
                for m2 in timed[crns[j]]:
                    if m1["day"] != m2["day"]:
                        continue
                    if not _date_ranges_overlap(m1["start_date"], m1["end_date"],
                                                m2["start_date"], m2["end_date"]):
                        continue
                    if m1["start"] < m2["end"] and m2["start"] < m1["end"]:
                        conflicts.append({
                            "crn_a": crns[i], "crn_b": crns[j], "day": m1["day"],
                            "a": [m1["start_time"], m1["end_time"]],
                            "b": [m2["start_time"], m2["end_time"]],
                        })
    return conflicts, warnings


# ── Plan helpers ──────────────────────────────────────────────────────────────

async def _get_active_plan(db: httpx.AsyncClient, student_id: str, term: Optional[str]) -> Optional[dict]:
    """The single active plan (pinned OR confirmed) for the term — guaranteed unique by index."""
    if not term:
        return None
    rows = await _get(db, "student_registration_plans", {
        "select":     "*",
        "student_id": f"eq.{student_id}",
        "term_code":  f"eq.{term}",
        "status":     "in.(pinned,confirmed)",
        "order":      "created_at.desc",
        "limit":      "1",
    })
    return rows[0] if rows else None


async def _get_plan_by_id(db: httpx.AsyncClient, student_id: str, plan_id: str) -> Optional[dict]:
    rows = await _get(db, "student_registration_plans", {
        "select": "*", "id": f"eq.{plan_id}", "student_id": f"eq.{student_id}", "limit": "1",
    })
    return rows[0] if rows else None


async def _pin_core(db: httpx.AsyncClient, student_id: str, crns: list[str], term: str) -> dict:
    """
    Shared PIN logic — used by /registration/pin and the deprecated /registration/approve alias.
    PIN = pre-confirmation INSIDE RUMMAN (live-rechecked + conflict-free). NOT official registration.
    Order: live availability re-check (fail-closed) -> hydrate -> conflict-check ->
           active-plan policy -> supersede prior pinned -> create pinned plan -> link active sections.
    Raises HTTPException on any gate. ALL validation happens BEFORE any write.
    """
    crn_csv = ",".join(crns)

    # 1. LIVE availability re-check — fail-closed, no silent fallback to stale sync.
    val = await _revalidate_live(db, term, crns, persist=True)
    if not val.get("live_checked"):
        raise HTTPException(503, detail=val)
    if not val.get("ok"):
        raise HTTPException(409, detail=val)

    # 2. Hydrate from term_sections — the source of truth.
    secs = await _get(db, "term_sections", {
        "select":    "crn,subject_course,section_number,course_name,credit_hours,"
                     "campus,delivery_mode,class_meetings,import_version",
        "term_code": f"eq.{term}",
        "crn":       f"in.({crn_csv})",
        "limit":     "500",
    })
    found = {str(s["crn"]) for s in secs}
    missing = [c for c in crns if c not in found]
    if missing:
        raise HTTPException(400, detail=f"CRNs not found in term_sections for term {term}: {missing}")

    # 3. Conflict-check (time clash between chosen sections). Missing times -> warn, not block.
    conflicts, warnings = _conflict_check(secs)
    if conflicts:
        raise HTTPException(409, detail={
            "reason": "schedule_conflict", "conflicts": conflicts,
            "message_ar": "هناك تعارض في المواعيد بين الشعب المختارة. عدّل الاختيار وأعد المحاولة.",
        })

    # 4. Active-plan policy: never silently replace a CONFIRMED plan.
    active_plan = await _get_active_plan(db, student_id, term)
    if active_plan and active_plan.get("status") == "confirmed":
        raise HTTPException(409, detail={
            "reason": "confirmed_plan_exists", "plan_id": active_plan["id"],
            "message_ar": "لديك تسجيل مؤكَّد لهذا الفصل. لتعديله استخدم إعادة الجدولة لاحقًا (replace).",
        })

    now = _now_iso()

    # 5. Supersede a prior PINNED plan first (frees the partial-unique slot before insert).
    if active_plan and active_plan.get("status") == "pinned":
        await _patch(db, "student_registration_plans", {"id": active_plan["id"]},
                     {"status": "superseded", "superseded_at": now, "updated_at": now})

    # 6. Create the new pinned plan.
    plan = await _post(db, "student_registration_plans", {
        "student_id":      student_id,
        "tenant_id":       TENANT_ID,
        "term_code":       term,
        "status":          "pinned",
        "source":          "smart_registration",
        "crns":            crns,
        "prevalidated_at": now,
        "pinned_at":       now,
        "metadata":        {"warnings": warnings},
    })
    plan_id = plan.get("id")

    # 7. Upsert chosen sections as ACTIVE, linked to the plan.
    rows = [{
        "student_id":            student_id,
        "tenant_id":             TENANT_ID,
        "term_code":             term,
        "crn":                   str(s["crn"]),
        "section_number":        s.get("section_number"),
        "banner_course_code":    s.get("subject_course"),   # raw Banner code, never lost
        "canonical_course_code": None,                      # resolved later via course_aliases
        "course_code":           s.get("subject_course"),   # app-facing; temporarily = banner
        "course_name":           s.get("course_name"),
        "credit_hours":          s.get("credit_hours"),
        "campus":                s.get("campus"),
        "delivery_mode":         s.get("delivery_mode"),
        "class_meetings":        s.get("class_meetings"),
        "import_version":        s.get("import_version"),
        "status":                "active",
        "source":                "smart_registration",
        "plan_id":               plan_id,
        "approved_at":           now,
        "updated_at":            now,
    } for s in secs]
    ru = await db.post(
        "/student_registered_sections?on_conflict=student_id,term_code,crn",
        json=rows,
        headers={"Prefer": "resolution=merge-duplicates,return=representation"},
    )
    if ru.status_code not in (200, 201):
        raise HTTPException(ru.status_code, detail=ru.text[:300])

    # 8. Soft-drop previously-active sections no longer chosen (never hard-deleted).
    dropped = 0
    rd = await db.patch(
        "/student_registered_sections",
        params={
            "student_id": f"eq.{student_id}",
            "term_code":  f"eq.{term}",
            "status":     "eq.active",
            "crn":        f"not.in.({crn_csv})",
        },
        json={"status": "dropped", "updated_at": now},
    )
    if rd.status_code in (200, 204):
        try:
            dropped = len(rd.json())
        except Exception:
            dropped = 0

    # Best-effort lifecycle event (non-fatal).
    try:
        await _post(db, "student_history", {
            "student_id": student_id,
            "tenant_id":  TENANT_ID,
            "event_type": "registration_pinned",
            "event_data": {"term_code": term, "plan_id": plan_id,
                           "pinned_crns": crns, "dropped": dropped, "warnings": warnings},
        })
    except Exception:
        pass

    return {
        "term_code":    term,
        "plan_id":      plan_id,
        "plan_status":  "pinned",
        "pinned_count": len(rows),
        "pinned_crns":  crns,
        "dropped_count": dropped,
        "warnings":     warnings,
        "live":         {"checked": val.get("live_checked"),
                         "cache_age_seconds": val.get("cache_age_seconds")},
    }


@router.post("/student/{student_id}/registration/pin")
async def pin_registration(student_id: str, body: RegistrationApprove):
    """
    PIN a Smart-Registration plan: pre-confirmation INSIDE RUMMAN after a LIVE Banner
    re-check + conflict-check. This is NOT official registration — the student must still
    register in Banner, then call /confirm. Closed/full/missing -> 409, Banner down -> 503,
    schedule conflict -> 409. Client sends ONLY CRNs (server hydrates from term_sections).
    """
    raw  = [str(c).strip() for c in (body.crns or []) if str(c).strip()]
    crns = list(dict.fromkeys(raw))
    if not crns:
        raise HTTPException(400, detail="no crns provided")
    if len(crns) != len(raw):
        raise HTTPException(400, detail="duplicate CRNs in request")
    async with _db() as db:
        term = body.term_code or await _resolve_active_term(db)
        if not term:
            raise HTTPException(400, detail="no active term configured")
        result = await _pin_core(db, student_id, crns, term)
    return {**result, "pinned_at": _now_iso()}


@router.post("/student/{student_id}/registration/approve", deprecated=True)
async def approve_registration(student_id: str, body: RegistrationApprove):
    """
    DEPRECATED alias for /registration/pin. 'approve' historically meant "officially
    registered", which is WRONG — pinning is pre-confirmation, not Banner registration.
    Kept as a thin alias (single code path) so the current mobile build keeps working
    until it migrates to /pin + /confirm. Returns a superset: legacy approved_* keys
    plus the new plan_id / plan_status.
    """
    raw  = [str(c).strip() for c in (body.crns or []) if str(c).strip()]
    crns = list(dict.fromkeys(raw))
    if not crns:
        raise HTTPException(400, detail="no crns provided")
    if len(crns) != len(raw):
        raise HTTPException(400, detail="duplicate CRNs in request")
    async with _db() as db:
        term = body.term_code or await _resolve_active_term(db)
        if not term:
            raise HTTPException(400, detail="no active term configured")
        result = await _pin_core(db, student_id, crns, term)
    return {
        "term_code":      result["term_code"],
        # legacy keys (kept for the current mobile build)
        "approved_count": result["pinned_count"],
        "approved_crns":  result["pinned_crns"],
        "dropped_count":  result["dropped_count"],
        # new lifecycle keys
        "plan_id":        result["plan_id"],
        "plan_status":    result["plan_status"],
        "pinned_count":   result["pinned_count"],
        "pinned_crns":    result["pinned_crns"],
        "warnings":       result["warnings"],
        "deprecated":     "use /registration/pin then /registration/confirm",
    }


@router.post("/student/{student_id}/registration/confirm")
async def confirm_registration(student_id: str, body: ConfirmBody):
    """
    Confirm that the student completed the OFFICIAL Banner registration themselves.
    Requires acknowledged=true (RUMMAN never registers on their behalf). NO live rejection —
    the seat may already be theirs. Transitions the pinned plan -> confirmed. Idempotent if
    already confirmed. From this point the plan is the source of truth for Courses/Today/Calendar.
    """
    if not body.acknowledged:
        raise HTTPException(400, detail={
            "reason": "acknowledgment_required",
            "message_ar": "يجب أن تؤكّد أنك أكملت التسجيل في النظام الرسمي (Banner). رمان لا يسجّل نيابةً عنك.",
        })
    async with _db() as db:
        term = body.term_code or await _resolve_active_term(db)
        plan = (await _get_plan_by_id(db, student_id, body.plan_id)
                if body.plan_id else await _get_active_plan(db, student_id, term))
        if not plan:
            raise HTTPException(409, detail={
                "reason": "no_plan_to_confirm",
                "message_ar": "لا توجد خطة مثبّتة لتأكيدها. ثبّت جدولك أولًا.",
            })
        if plan.get("status") == "confirmed":
            return {"plan_id": plan["id"], "plan_status": "confirmed",
                    "confirmed_at": plan.get("confirmed_at"),
                    "term_code": plan.get("term_code"), "idempotent": True}
        if plan.get("status") != "pinned":
            raise HTTPException(409, detail={
                "reason": "plan_not_pinned", "current_status": plan.get("status"),
                "message_ar": "لا يمكن تأكيد هذه الخطة في حالتها الحالية.",
            })
        now = _now_iso()
        await _patch(db, "student_registration_plans", {"id": plan["id"]},
                     {"status": "confirmed", "confirmed_at": now, "updated_at": now})
        try:
            await _post(db, "student_history", {
                "student_id": student_id, "tenant_id": TENANT_ID,
                "event_type": "university_registration_confirmed",
                "event_data": {"term_code": plan.get("term_code"), "plan_id": plan["id"]},
            })
        except Exception:
            pass
    return {"plan_id": plan["id"], "plan_status": "confirmed", "confirmed_at": now,
            "term_code": plan.get("term_code"), "idempotent": False}


@router.post("/student/{student_id}/registration/mark-failed")
async def mark_registration_failed(student_id: str, body: MarkFailedBody):
    """
    The student tried to register in Banner and could NOT (e.g. a section filled before they
    finished). Transitions the pinned plan -> registration_failed, preserves its sections as
    needs_review (never deleted), and frees the active-plan slot so a fresh /pin is allowed.
    """
    async with _db() as db:
        term = body.term_code or await _resolve_active_term(db)
        plan = (await _get_plan_by_id(db, student_id, body.plan_id)
                if body.plan_id else await _get_active_plan(db, student_id, term))
        if not plan or plan.get("status") != "pinned":
            raise HTTPException(409, detail={
                "reason": "no_pinned_plan",
                "message_ar": "لا توجد خطة مثبّتة لتسجيل فشلها.",
            })
        now = _now_iso()
        meta = dict(plan.get("metadata") or {})
        if body.reason:
            meta["failure_reason"] = str(body.reason)[:500]
        await _patch(db, "student_registration_plans", {"id": plan["id"]},
                     {"status": "registration_failed", "failed_at": now,
                      "metadata": meta, "updated_at": now})
        # Preserve the sections, flag for review (never hard-deleted).
        reviewed = 0
        rr = await db.patch(
            "/student_registered_sections",
            params={"student_id": f"eq.{student_id}",
                    "plan_id":    f"eq.{plan['id']}",
                    "status":     "eq.active"},
            json={"status": "needs_review", "updated_at": now},
        )
        if rr.status_code in (200, 204):
            try:
                reviewed = len(rr.json())
            except Exception:
                reviewed = 0
        try:
            await _post(db, "student_history", {
                "student_id": student_id, "tenant_id": TENANT_ID,
                "event_type": "university_registration_failed",
                "event_data": {"term_code": plan.get("term_code"), "plan_id": plan["id"],
                               "reason": body.reason},
            })
        except Exception:
            pass
    return {"plan_id": plan["id"], "plan_status": "registration_failed", "failed_at": now,
            "sections_needs_review": reviewed, "term_code": plan.get("term_code")}


@router.get("/student/{student_id}/registration/plan")
async def get_registration_plan(
    student_id: str,
    term:    Optional[str] = Query(default=None),
    history: bool          = Query(default=False, description="include superseded/failed/abandoned plans"),
):
    """The student's current active registration plan (pinned OR confirmed) with its sections.
    Side states (superseded/registration_failed/abandoned) are hidden unless history=true."""
    async with _db() as db:
        if term is None:
            term = await _resolve_active_term(db)
        plan = await _get_active_plan(db, student_id, term) if term else None
        sections: list[dict] = []
        if plan:
            sections = await _get(db, "student_registered_sections", {
                "select":     "*",
                "student_id": f"eq.{student_id}",
                "plan_id":    f"eq.{plan['id']}",
                "status":     "neq.dropped",
                "order":      "course_code.asc,crn.asc",
                "limit":      "500",
            })
        result: dict[str, Any] = {
            "term_code": term,
            "has_plan":  plan is not None,
            "plan": ({
                "id":           plan["id"],
                "status":       plan["status"],
                "confirmed":    plan["status"] == "confirmed",
                "crns":         plan.get("crns"),
                "pinned_at":    plan.get("pinned_at"),
                "confirmed_at": plan.get("confirmed_at"),
                "warnings":     (plan.get("metadata") or {}).get("warnings", []),
            } if plan else None),
            "sections": sections,
        }
        if history:
            hist_params: dict[str, Any] = {
                "select":     "id,status,crns,pinned_at,confirmed_at,failed_at,superseded_at,abandoned_at,created_at",
                "student_id": f"eq.{student_id}",
                "status":     "in.(registration_failed,superseded,abandoned,needs_review)",
                "order":      "created_at.desc",
                "limit":      "50",
            }
            if term:
                hist_params["term_code"] = f"eq.{term}"
            result["history"] = await _get(db, "student_registration_plans", hist_params)
        return result


@router.patch("/student/{student_id}/registration/{crn}")
async def patch_registration(
    student_id: str,
    crn:        str,
    body:       RegistrationPatch,
    term_code:  Optional[str] = Query(default=None),
):
    """Change a single registered section's status. CRN is unique only within a term,
    so term_code is required (defaults to the active term)."""
    if body.status not in _VALID_REG_STATUS:
        raise HTTPException(400, detail=f"invalid status; must be one of {sorted(_VALID_REG_STATUS)}")
    async with _db() as db:
        if not term_code:
            term_code = await _resolve_active_term(db)
        filters = {"student_id": student_id, "crn": crn}
        if term_code:
            filters["term_code"] = term_code
        updated = await _patch(db, "student_registered_sections", filters,
                               {"status": body.status, "updated_at": _now_iso()})
    if not updated:
        raise HTTPException(404, detail="registered section not found")
    return updated[0]


# ---------------------------------------------------------------------------
# Home Command Center
# ---------------------------------------------------------------------------

@router.get("/student/{student_id}/today")
async def student_today(student_id: str):
    """
    Home Command Center — everything the student needs right now.
    Returns: urgent items, today's tasks, upcoming events (7 days),
             active courses, open requests, unread notification count.
    """
    now   = datetime.now(timezone.utc)
    today = now.date().isoformat()
    week  = _days_from_now(7)

    async with _db() as db:
        # Pending tasks due today or overdue
        tasks = await _get(db, "student_tasks", {
            "select":     "id,title,task_type,priority,due_at,course_code,status,auto_generated",
            "student_id": f"eq.{student_id}",
            "tenant_id":  f"eq.{TENANT_ID}",
            "status":     "eq.pending",
            "order":      "priority.asc,due_at.asc.nullslast",
        })
        due_today = [t for t in tasks if t.get("due_at") and t["due_at"][:10] <= today]
        upcoming  = [t for t in tasks if not t.get("due_at") or t["due_at"][:10] > today]

        # Calendar events next 7 days
        events = await _get(db, "student_calendar_events", {
            "select":     "id,title,event_type,course_code,starts_at,ends_at,all_day,source",
            "student_id": f"eq.{student_id}",
            "tenant_id":  f"eq.{TENANT_ID}",
            "is_hidden":  "eq.false",
            "starts_at":  f"gte.{now.isoformat()}",
            "order":      "starts_at.asc",
            "limit":      "20",
        })
        # Filter to next 7 days
        events = [e for e in events if e["starts_at"] <= week]

        # Open requests
        requests = await _get(db, "student_requests", {
            "select":     "id,title,request_type,status,deadline_at,created_at",
            "student_id": f"eq.{student_id}",
            "tenant_id":  f"eq.{TENANT_ID}",
            "status":     "in.(draft,ready,submitted,pending)",
            "order":      "created_at.desc",
            "limit":      "5",
        })

        # Unread notifications count
        notifs = await _get(db, "student_notifications", {
            "select":      "id,category,urgency,title,body,action_type,action_ref_id",
            "student_id":  f"eq.{student_id}",
            "tenant_id":   f"eq.{TENANT_ID}",
            "seen_at":     "is.null",
            "dismissed_at":"is.null",
            "order":       "urgency.asc,created_at.desc",
            "limit":       "10",
        })

        # Active courses (inferred from last 30 days of queries)
        le_rows = await _get(db, "learning_events", {
            "select":      "course_codes,occurred_at",
            "user_id":     f"eq.{student_id}",
            "tenant_id":   f"eq.{TENANT_ID}",
            "occurred_at": f"gte.{_days_from_now(-30)}",
            "limit":       "500",
        })
        course_hits: dict[str, int] = {}
        for ev in le_rows:
            for c in (ev.get("course_codes") or []):
                if c:
                    course_hits[c] = course_hits.get(c, 0) + 1
        active_courses = [
            {"course_code": c, "query_count": n}
            for c, n in sorted(course_hits.items(), key=lambda x: -x[1])
        ]

        # Official academic calendar — next 14 days (same horizon as Today screen)
        # CalendarScreen uses /calendar for broader academic view
        fourteen_days = _days_from_now(14)
        cal_rows = await _get(db, "academic_calendar", {
            "select":     "id,event_type,event_name_ar,event_name_en,start_date,end_date",
            "start_date": f"gte.{today}",
            "order":      "start_date.asc",
            "limit":      "50",
        })
        academic_events = [
            e for e in cal_rows
            if e.get("start_date", "")[:10] <= fourteen_days[:10]
        ]

        # exam_proximities kept for backward compat — exams within 14 days (max 3)
        exam_proximities = []
        for row in cal_rows:
            name = row.get("event_name_ar") or ""
            if "اختبار" in name or row.get("event_type") in ("midterm_exam", "final_exam", "exam"):
                try:
                    ed = datetime.fromisoformat(row["start_date"])
                    days_away = (ed.date() - now.date()).days
                    if 0 <= days_away <= 14:
                        exam_proximities.append({
                            "event_name": name,
                            "event_date": row["start_date"],
                            "days_away":  days_away,
                        })
                except Exception:
                    pass

        # Build urgency signals
        urgent = []
        for t in due_today:
            urgent.append({
                "type":     "task_due",
                "urgency":  "high" if t["priority"] == 1 else "normal",
                "title":    t["title"],
                "ref_id":   t["id"],
                "ref_type": "task",
                "course":   t.get("course_code"),
            })
        for req in requests:
            if req.get("deadline_at"):
                days_left = (
                    datetime.fromisoformat(req["deadline_at"].replace("Z", "+00:00")).date()
                    - now.date()
                ).days
                if days_left <= 3:
                    urgent.append({
                        "type":      "request_deadline",
                        "urgency":   "critical" if days_left <= 1 else "high",
                        "title":     f"موعد طلب قريب ({days_left} أيام): {req['title']}",
                        "ref_id":    req["id"],
                        "ref_type":  "request",
                        "days_left": days_left,
                    })
        for ep in exam_proximities[:3]:
            urgent.append({
                "type":     "exam_approaching",
                "urgency":  "critical" if ep["days_away"] <= 2 else "high",
                "title":    ep["event_name"],
                "days_away":ep["days_away"],
                "ref_type": "calendar",
            })
        for n in [x for x in notifs if x["urgency"] in ("critical", "high")]:
            urgent.append({
                "type":     "notification",
                "urgency":  n["urgency"],
                "title":    n["title"],
                "ref_id":   n["id"],
                "ref_type": "notification",
            })

        return {
            "generated_at":      _now_iso(),
            "urgent":            urgent,
            "tasks_due_today":   due_today,
            "tasks_upcoming":    upcoming[:10],
            "calendar_events":   events,
            "open_requests":     requests,
            "unread_notifications": len(notifs),
            "active_courses":    active_courses[:8],
            "exam_proximities":  exam_proximities,
            "academic_events":   academic_events,
        }


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------

@router.get("/student/{student_id}/calendar")
async def student_calendar(
    student_id: str,
    from_date: Optional[str] = Query(default=None, description="ISO date, default today"),
    days:      int        = Query(default=14, ge=1, le=90),
):
    """Full calendar view — personal events + official academic calendar."""
    start = from_date or datetime.now(timezone.utc).date().isoformat()
    end   = (datetime.fromisoformat(start) + timedelta(days=days)).isoformat()

    async with _db() as db:
        # Personal calendar events
        personal = await _get(db, "student_calendar_events", {
            "select":     "*",
            "student_id": f"eq.{student_id}",
            "tenant_id":  f"eq.{TENANT_ID}",
            "is_hidden":  "eq.false",
            "starts_at":  f"gte.{start}",
            "order":      "starts_at.asc",
        })
        personal = [e for e in personal if e["starts_at"][:10] <= end[:10]]

        # Official academic calendar (semester-wide events)
        official = await _get(db, "academic_calendar", {
            "select":     "id,event_type,event_name_ar,event_name_en,start_date,end_date",
            "start_date": f"gte.{start}",
            "order":      "start_date.asc",
        })
        official = [e for e in official if e.get("start_date", "")[:10] <= end[:10]]

        # Tasks with due dates in range
        tasks = await _get(db, "student_tasks", {
            "select":     "id,title,task_type,priority,due_at,opens_at,closes_at,course_code,status",
            "student_id": f"eq.{student_id}",
            "tenant_id":  f"eq.{TENANT_ID}",
            "status":     "neq.cancelled",
            "due_at":     f"gte.{start}",
            "order":      "due_at.asc",
        })
        tasks = [t for t in tasks if t.get("due_at", "")[:10] <= end[:10]]

        return {
            "from":     start,
            "to":       end,
            "personal": personal,
            "official": official,
            "tasks":    tasks,
        }


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@router.get("/student/{student_id}/tasks")
async def list_tasks(
    student_id:  str,
    status:      Optional[str] = Query(default=None),
    course_code: Optional[str] = Query(default=None),
    limit:       int        = Query(default=20, ge=1, le=100),
):
    params: dict[str, Any] = {
        "select":     "*",
        "student_id": f"eq.{student_id}",
        "tenant_id":  f"eq.{TENANT_ID}",
        "order":      "priority.asc,due_at.asc.nullslast",
        "limit":      str(limit),
    }
    if status:
        params["status"] = f"eq.{status}"
    if course_code:
        params["course_code"] = f"eq.{course_code}"

    async with _db() as db:
        return await _get(db, "student_tasks", params)


@router.post("/student/{student_id}/tasks", status_code=201)
async def create_task(student_id: str, body: TaskCreate):
    payload: dict[str, Any] = {
        "student_id":  student_id,
        "tenant_id":   TENANT_ID,
        "title":       body.title,
        "task_type":   body.task_type,
        "priority":    body.priority,
    }
    if body.due_at:        payload["due_at"]       = body.due_at
    if body.opens_at:      payload["opens_at"]     = body.opens_at
    if body.closes_at:     payload["closes_at"]    = body.closes_at
    if body.course_code:   payload["course_code"]  = body.course_code
    if body.notes:         payload["notes"]        = body.notes
    if body.remind_at:     payload["remind_at"]    = body.remind_at
    if body.exam_date:     payload["exam_date"]    = body.exam_date

    async with _db() as db:
        return await _post(db, "student_tasks", payload)


@router.patch("/student/{student_id}/tasks/{task_id}")
async def update_task(student_id: str, task_id: str, body: TaskUpdate):
    payload: dict[str, Any] = {"updated_at": _now_iso()}
    if body.status is not None:
        payload["status"] = body.status
        if body.status == "done":
            payload["acted_on_at"] = _now_iso()
    if body.title is not None:          payload["title"]        = body.title
    if body.priority is not None:       payload["priority"]     = body.priority
    if body.due_at is not None:         payload["due_at"]       = body.due_at
    if body.opens_at is not None:       payload["opens_at"]     = body.opens_at
    if body.closes_at is not None:      payload["closes_at"]    = body.closes_at
    if body.snoozed_until is not None:  payload["snoozed_until"] = body.snoozed_until
    if body.notes is not None:          payload["notes"]        = body.notes
    if body.acted_on_at is not None:    payload["acted_on_at"]  = body.acted_on_at

    async with _db() as db:
        rows = await _patch(db, "student_tasks",
                            {"id": task_id, "student_id": student_id},
                            payload)
        if not rows:
            raise HTTPException(404, "Task not found")
        return rows[0]


@router.delete("/student/{student_id}/tasks/{task_id}", status_code=204)
async def delete_task(student_id: str, task_id: str):
    async with _db() as db:
        await _delete(db, "student_tasks", {"id": task_id, "student_id": student_id})


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------

@router.get("/student/{student_id}/inbox")
async def student_inbox(
    student_id: str,
    limit:      int = Query(default=30, ge=1, le=100),
    unread_only: bool = Query(default=False),
):
    """
    Ranked inbox: announcements from professors + institutional news,
    filtered to courses the student is active in.
    """
    async with _db() as db:
        # Get active courses
        le_rows = await _get(db, "learning_events", {
            "select":      "course_codes",
            "user_id":     f"eq.{student_id}",
            "tenant_id":   f"eq.{TENANT_ID}",
            "occurred_at": f"gte.{_days_from_now(-30)}",
            "limit":       "200",
        })
        courses: set[str] = set()
        for ev in le_rows:
            for c in (ev.get("course_codes") or []):
                if c:
                    courses.add(c)

        # Intelligence items (extracted from Telegram) — last 14 days
        items = await _get(db, "intelligence_items", {
            "select":    "id,item_type,title,description,course_code,due_date,confidence,created_at",
            "tenant_id": f"eq.{TENANT_ID}",
            "created_at": f"gte.{_days_from_now(-14)}",
            "order":     "created_at.desc",
            "limit":     str(limit * 3),
        })

        # Rank: course match → recency → confidence
        def rank(item: dict) -> tuple:
            course_match = item.get("course_code") in courses
            try:
                age = (datetime.now(timezone.utc) -
                       datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
                      ).total_seconds() / 3600
            except Exception:
                age = 999
            return (not course_match, age, -(item.get("confidence") or 0))

        items.sort(key=rank)
        relevant = [i for i in items if i.get("course_code") in courses or not courses]

        return {
            "active_courses": list(courses),
            "items":          relevant[:limit],
            "total":          len(relevant),
        }


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

@router.get("/student/{student_id}/notifications")
async def get_notifications(
    student_id:  str,
    unread_only: bool = Query(default=False),
    limit:       int  = Query(default=20, ge=1, le=50),
):
    params: dict[str, Any] = {
        "select":       "*",
        "student_id":   f"eq.{student_id}",
        "tenant_id":    f"eq.{TENANT_ID}",
        "dismissed_at": "is.null",
        "order":        "urgency.asc,created_at.desc",
        "limit":        str(limit),
    }
    if unread_only:
        params["seen_at"] = "is.null"

    async with _db() as db:
        return await _get(db, "student_notifications", params)


@router.post("/student/{student_id}/notifications/{notif_id}/seen", status_code=204)
async def mark_notification_seen(student_id: str, notif_id: str):
    async with _db() as db:
        await _patch(db, "student_notifications",
                     {"id": notif_id, "student_id": student_id},
                     {"seen_at": _now_iso()})


@router.post("/student/{student_id}/notifications/{notif_id}/dismiss", status_code=204)
async def dismiss_notification(student_id: str, notif_id: str):
    async with _db() as db:
        await _patch(db, "student_notifications",
                     {"id": notif_id, "student_id": student_id},
                     {"dismissed_at": _now_iso()})


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

@router.get("/request-types")
async def list_request_types():
    """All available request types with their fields — for the request picker UI."""
    async with _db() as db:
        return await _get(db, "request_templates", {
            "select": "request_type,title_ar,description_ar,target_entity,"
                      "required_fields,deadline_rule,attachments_needed",
            "active": "eq.true",
            "order":  "title_ar.asc",
        })


@router.get("/student/{student_id}/requests")
async def list_requests(
    student_id: str,
    status:     Optional[str] = Query(default=None),
):
    params: dict[str, Any] = {
        "select":     "*",
        "student_id": f"eq.{student_id}",
        "tenant_id":  f"eq.{TENANT_ID}",
        "order":      "created_at.desc",
    }
    if status:
        params["status"] = f"eq.{status}"

    async with _db() as db:
        return await _get(db, "student_requests", params)


@router.post("/student/{student_id}/requests/start", status_code=201)
async def start_request(student_id: str, body: RequestStart):
    """
    Start a new request flow. Returns the request ID + first question
    RUMMAN needs to ask the student to collect required fields.
    """
    async with _db() as db:
        # Fetch template
        templates = await _get(db, "request_templates", {
            "request_type": f"eq.{body.request_type}",
            "active":       "eq.true",
        })
        if not templates:
            raise HTTPException(404, f"Request type '{body.request_type}' not found")
        tmpl = templates[0]

        deadline_at = None
        if tmpl.get("deadline_days") is not None:
            deadline_at = _days_from_now(tmpl["deadline_days"])

        first_turn = {
            "role":    "assistant",
            "content": f"فهمت — هذا طلب {tmpl['title_ar']}. سأساعدك في إعداده.",
            "ts":      _now_iso(),
        }

        payload = {
            "student_id":        student_id,
            "tenant_id":         TENANT_ID,
            "request_type":      body.request_type,
            "title":             tmpl["title_ar"],
            "status":            "draft",
            "target_entity":     tmpl["target_entity"],
            "course_code":       body.course_code,
            "deadline_at":       deadline_at,
            "attachments_needed":tmpl["attachments_needed"],
            "conversation":      [first_turn],
        }

        req = await _post(db, "student_requests", payload)

        # First required question
        first_q = next(
            (f for f in (tmpl.get("required_fields") or []) if f.get("required")),
            None,
        )

        return {
            "request":        req,
            "first_question": first_q,
            "attachments":    tmpl.get("attachments_needed", []),
            "deadline":       deadline_at,
        }


@router.patch("/student/{student_id}/requests/{request_id}")
async def update_request(student_id: str, request_id: str, body: RequestUpdate):
    """
    Update a request: add collected fields, update status, add conversation turn.
    When status='submitted', records submitted_at.
    """
    payload: dict[str, Any] = {"updated_at": _now_iso()}

    if body.status is not None:
        payload["status"] = body.status
        if body.status == "submitted":
            payload["submitted_at"] = _now_iso()
        elif body.status == "resolved":
            payload["resolved_at"] = _now_iso()

    if body.collected_fields is not None:
        # Merge into existing collected_fields via SQL jsonb ||
        # We'll overwrite for simplicity (client sends full object)
        payload["collected_fields"] = body.collected_fields

    if body.body_final is not None:
        payload["body_final"] = body.body_final
        payload["status"]     = "ready"

    if body.target_name is not None:
        payload["target_name"] = body.target_name

    async with _db() as db:
        if body.conversation_turn is not None:
            # Append to conversation array — fetch then update
            rows = await _get(db, "student_requests", {
                "id":         f"eq.{request_id}",
                "student_id": f"eq.{student_id}",
                "select":     "conversation",
            })
            if rows:
                conv = rows[0].get("conversation") or []
                conv.append({**body.conversation_turn, "ts": _now_iso()})
                payload["conversation"] = conv

        rows = await _patch(db, "student_requests",
                            {"id": request_id, "student_id": student_id},
                            payload)
        if not rows:
            raise HTTPException(404, "Request not found")
        return rows[0]


# ---------------------------------------------------------------------------
# Course Intelligence
# ---------------------------------------------------------------------------

@router.get("/courses/{course_code}/intelligence")
async def course_intelligence(
    course_code: str,
    student_id:  Optional[str] = Query(default=None, description="If provided, includes student-specific signals"),
):
    """
    Course Intelligence Card — powers the Smart Course Profile screen.
    Returns: health score, top exam concepts, panic index, recent announcements.
    """
    async with _db() as db:
        # Health score
        health = await _get(db, "course_health_score", {
            "select":      "*",
            "course_code": f"eq.{course_code}",
        })

        # Top concepts from trajectory (by exam_appearances)
        concepts = await _get(db, "concept_temporal_trajectory", {
            "select":      "concept_name,exam_appearances,academic_year",
            "course_code": f"eq.{course_code}",
            "tenant_id":   f"eq.{TENANT_ID}",
            "order":       "exam_appearances.desc",
            "limit":       "10",
        })

        # Confusion registry for this course
        confusion = await _get(db, "concept_confusion_registry", {
            "select":      "concept_name,confusion_score,exam_frequency,critical_intersection",
            "course_code": f"eq.{course_code}",
            "tenant_id":   f"eq.{TENANT_ID}",
            "order":       "confusion_score.desc",
            "limit":       "5",
        })

        # Recent announcements
        announcements = await _get(db, "intelligence_items", {
            "select":      "item_type,title,description,due_date,created_at",
            "course_code": f"eq.{course_code}",
            "tenant_id":   f"eq.{TENANT_ID}",
            "order":       "created_at.desc",
            "limit":       "5",
        })

        # Student-specific: which concepts they've asked about (grounded=false)
        student_gaps: list[dict] = []
        if student_id:
            gap_rows = await _get(db, "learning_events", {
                "select":      "concept_tags,occurred_at",
                "user_id":     f"eq.{student_id}",
                "tenant_id":   f"eq.{TENANT_ID}",
                "grounded":    "eq.false",
                "occurred_at": f"gte.{_days_from_now(-60)}",
                "limit":       "200",
            })
            gap_counts: dict[str, int] = {}
            for ev in gap_rows:
                for tag in (ev.get("concept_tags") or []):
                    gap_counts[tag] = gap_counts.get(tag, 0) + 1
            student_gaps = [
                {"concept": c, "failed_queries": n}
                for c, n in sorted(gap_counts.items(), key=lambda x: -x[1])
            ][:5]

        return {
            "course_code":   course_code,
            "health":        health[0] if health else None,
            "top_concepts":  concepts,
            "confusion":     confusion,
            "announcements": announcements,
            "student_gaps":  student_gaps,
        }


# ---------------------------------------------------------------------------
# Exam Practice
# ---------------------------------------------------------------------------

_ALLOWED_EXAM_TYPES = {"midterm", "final", "quiz", "general", "all"}
_DEFAULT_TF_OPTIONS = [{"key": "صح", "text": "صح"}, {"key": "خطأ", "text": "خطأ"}]


@router.get("/student/{student_id}/exam-practice/{course_code}")
async def exam_practice(
    student_id:  str,
    course_code: str,
    exam_type:   Optional[str] = Query(default=None, description="midterm|final|quiz|general|all"),
    topic:       Optional[str] = Query(default=None,  description="Optional topic filter (substring match on topic_tags)"),
    limit:       int           = Query(default=10,    description="Number of questions, 1-25. Default 10."),
):
    """
    Exam practice questions for a specific course.
    Returns MCQ and true_false questions where model_answer IS NOT NULL.
    Ordered by extraction_confidence DESC.
    """
    # ── Validate course_code ──────────────────────────────────────────────
    code = course_code.strip().upper()
    if not code or code == "UNKNOWN":
        raise HTTPException(400, "course_code غير صالح أو غير محدد")

    # ── Validate exam_type ────────────────────────────────────────────────
    et = (exam_type or "all").lower().strip()
    if et not in _ALLOWED_EXAM_TYPES:
        raise HTTPException(
            400,
            f"exam_type غير مسموح: '{exam_type}'. القيم المتاحة: {', '.join(sorted(_ALLOWED_EXAM_TYPES))}",
        )

    # ── Clamp limit ───────────────────────────────────────────────────────
    if limit < 1:
        limit = 10
    elif limit > 25:
        limit = 25

    # ── Fetch from Supabase ───────────────────────────────────────────────
    # Fetch extra to compensate for Python-side filtering (empty answers, bad MCQ options, topic)
    fetch_limit = min(limit * 8 if topic else limit * 4, 200)

    params: dict[str, Any] = {
        "select":        "id,course_code,exam_type,exam_year,question_type,"
                         "question_text,answer_options,model_answer,topic_tags,"
                         "extraction_confidence",
        "course_code":   f"eq.{code}",
        "question_text": "not.is.null",
        "model_answer":  "not.is.null",
        "question_type": "in.(mcq,true_false)",
        "order":         "extraction_confidence.desc.nullslast",
        "limit":         str(fetch_limit),
    }

    if et != "all":
        params["exam_type"] = f"eq.{et}"

    try:
        async with _db() as db:
            rows = await _get(db, "exam_questions", params)
    except HTTPException:
        raise HTTPException(500, "تعذّر جلب الأسئلة. حاول مرة أخرى.")
    except Exception:
        raise HTTPException(500, "تعذّر جلب الأسئلة. حاول مرة أخرى.")

    # ── Python-side filtering & shaping ──────────────────────────────────
    topic_lower = topic.lower().strip() if topic else None
    questions: list[dict] = []

    for row in rows:
        # Skip empty model_answer (null already filtered by DB; guard against empty string)
        ma = (row.get("model_answer") or "").strip()
        if not ma:
            continue

        q_type  = row.get("question_type")
        options = row.get("answer_options")

        # Validate / normalise answer_options
        if q_type == "mcq":
            if not isinstance(options, list) or len(options) == 0:
                continue           # MCQ without usable options is discarded
        elif q_type == "true_false":
            if not isinstance(options, list) or len(options) == 0:
                options = _DEFAULT_TF_OPTIONS

        # Topic filter — substring match against topic_tags array of strings
        if topic_lower:
            tags = row.get("topic_tags") or []
            matched = any(
                topic_lower in (t.lower() if isinstance(t, str) else str(t).lower())
                for t in tags
            )
            if not matched:
                continue

        questions.append({
            "id":                    row["id"],
            "course_code":           row["course_code"],
            "exam_type":             row.get("exam_type"),
            "exam_year":             row.get("exam_year"),
            "question_type":         q_type,
            "question_text":         row["question_text"],
            "answer_options":        options,
            "model_answer":          ma,
            "topic_tags":            row.get("topic_tags") or [],
            "extraction_confidence": row.get("extraction_confidence"),
        })

        if len(questions) >= limit:
            break

    # ── Build response ────────────────────────────────────────────────────
    response: dict[str, Any] = {
        "student_id":  student_id,
        "course_code": code,
        "exam_type":   et,
        "topic":       topic or None,
        "limit":       limit,
        "count":       len(questions),
        "questions":   questions,
    }

    if not questions:
        response["message"] = "لا توجد أسئلة تدريبية جاهزة لهذا المقرر حاليًا."

    return response


# ---------------------------------------------------------------------------
# Founder Cockpit
# ---------------------------------------------------------------------------

@router.get("/founder/cockpit")
async def founder_cockpit(days: int = Query(default=7, ge=1, le=30)):
    """Platform health dashboard for the founder."""
    async with _db() as db:
        since = _days_from_now(-days)

        # Query volume
        all_events = await _get(db, "learning_events", {
            "select":      "grounded,course_codes,intent_type,occurred_at",
            "tenant_id":   f"eq.{TENANT_ID}",
            "occurred_at": f"gte.{since}",
            "limit":       "2000",
        })

        total        = len(all_events)
        grounded     = sum(1 for e in all_events if e.get("grounded"))
        zero_result  = total - grounded
        grounded_pct = round(grounded / total * 100, 1) if total else 0

        # Top courses by query volume
        course_counts: dict[str, int] = {}
        for ev in all_events:
            for c in (ev.get("course_codes") or []):
                if c:
                    course_counts[c] = course_counts.get(c, 0) + 1
        top_courses = sorted(course_counts.items(), key=lambda x: -x[1])[:10]

        # Top zero-result concepts (content gaps)
        gap_counts: dict[str, int] = {}
        for ev in [e for e in all_events if not e.get("grounded")]:
            pass  # concept_tags on zero-result events — need separate query

        gap_events = await _get(db, "learning_events", {
            "select":      "concept_tags",
            "tenant_id":   f"eq.{TENANT_ID}",
            "grounded":    "eq.false",
            "occurred_at": f"gte.{since}",
            "limit":       "500",
        })
        for ev in gap_events:
            for tag in (ev.get("concept_tags") or []):
                if tag:
                    gap_counts[tag] = gap_counts.get(tag, 0) + 1
        top_gaps = sorted(gap_counts.items(), key=lambda x: -x[1])[:10]

        # Course health distribution
        health_rows = await _get(db, "course_health_score", {
            "select": "health_tier",
        })
        tier_counts = {"green": 0, "yellow": 0, "red": 0}
        for r in health_rows:
            t = r.get("health_tier", "red")
            tier_counts[t] = tier_counts.get(t, 0) + 1

        # Registry size
        registry = await _get(db, "concept_confusion_registry", {
            "select":    "id",
            "tenant_id": f"eq.{TENANT_ID}",
        })

        return {
            "period_days":     days,
            "query_volume":    total,
            "grounded":        grounded,
            "zero_result":     zero_result,
            "grounded_pct":    grounded_pct,
            "top_courses":     [{"course": c, "queries": n} for c, n in top_courses],
            "top_content_gaps":[{"concept": c, "failed": n} for c, n in top_gaps],
            "health_distribution": tier_counts,
            "course_count":    len(health_rows),
            "confusion_registry_rows": len(registry),
        }


# ---------------------------------------------------------------------------
# Ask RUMMAN — Academic Intelligence Sensor
# Every query is an Event, not just a question.
# Signals (resolved / confused / task) feed learning_events → concept_confusion_worker.
# ---------------------------------------------------------------------------

import json as _json
import logging as _logging

_ask_log = _logging.getLogger("ask_rumman")


class AskRequest(BaseModel):
    query:                str
    course_code:          Optional[str]        = None
    follow_up_of:         Optional[str]        = None   # event_id of previous exchange
    conversation_history: Optional[list[dict]] = None   # [{role: user|assistant, content: str}]


class SignalRequest(BaseModel):
    signal_type: str                      # "resolved" | "confused" | "task"
    task_title:  Optional[str] = None     # populated when signal_type == "task"


def _time_context(academic_calendar_events: list) -> str:
    """Derive time_context from upcoming academic events (already fetched for today)."""
    if not academic_calendar_events:
        return "normal"
    now = datetime.now(timezone.utc)
    for ev in academic_calendar_events:
        start = ev.get("start_date", "")
        if not start:
            continue
        try:
            delta = (datetime.fromisoformat(start.replace("Z", "+00:00")) - now).days
            ev_type = ev.get("event_type", "")
            if ev_type in ("exam", "midterm", "final") and delta <= 7:
                return "exam_approaching"
            if ev_type in ("registration_window", "add_drop") and delta <= 5:
                return "registration_window"
            if ev_type in ("payment_deadline",) and delta <= 3:
                return "deadline_day"
        except Exception:
            continue
    return "normal"


@router.post("/student/{student_id}/ask")
async def ask_rumman(student_id: str, body: AskRequest):
    """
    Academic Intelligence Sensor — wraps /synthesize and logs a learning_event.
    Returns: answer, sources, grounded, source_count, event_id, course_code, concept_names.
    The event_id is used by the signal endpoint to record resolution/confusion/task intents.
    """
    import uuid as _uuid

    # 1. Call /synthesize
    synth_payload: dict = {
        "query":      body.query,
        "session_id": student_id,
        "user_id":    student_id,
    }
    if body.course_code:
        synth_payload["course_code"] = body.course_code
    if body.conversation_history:
        synth_payload["conversation_history"] = body.conversation_history

    async with httpx.AsyncClient(timeout=30) as http:
        try:
            synth_resp = await http.post(f"{SEARCH_API_BASE}/synthesize", json=synth_payload)
            synth_resp.raise_for_status()
            synth = synth_resp.json()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"synthesize unavailable: {exc}")

    answer       = synth.get("answer") or synth.get("synthesized_answer") or ""
    sources      = synth.get("sources") or synth.get("results") or []
    grounded     = bool(sources)
    source_count = len(sources)
    course_code  = (
        body.course_code
        or synth.get("course_code")
        or (sources[0].get("course_code") if sources else None)
    )

    # 2. Extract concept names from sources metadata (best-effort)
    concept_names: list = []
    for s in sources[:5]:
        meta = s.get("metadata") or {}
        for field in ("concept", "topic", "title", "concept_name"):
            v = meta.get(field)
            if v and isinstance(v, str) and v not in concept_names:
                concept_names.append(v)

    # 3. Log to learning_events (user_id = null; student_id stored in metadata)
    event_id = str(_uuid.uuid4())
    async with _db() as db:
        event_payload = {
            "id":            event_id,
            "event_type":    "query",
            "query_raw":     body.query,
            "grounded":      grounded,
            "retrieval_count": source_count,
            "metadata": {
                "student_id":          student_id,
                "course_code":         course_code,
                "concept_names":       concept_names,
                "follow_up_of":        body.follow_up_of,
                "resolution_signal":   None,
                "confusion_signal":    None,
                "task_generated":      False,
                "time_context":        "normal",   # updated by client if needed
                "source_count":        source_count,
            },
        }
        if course_code:
            event_payload["course_codes"] = [course_code]
        try:
            await _post(db, "learning_events", event_payload)
        except Exception as exc:
            _ask_log.warning("learning_event write failed (non-fatal): %s", exc)

    return {
        "event_id":      event_id,
        "answer":        answer,
        "sources":       sources,
        "grounded":      grounded,
        "source_count":  source_count,
        "course_code":   course_code,
        "concept_names": concept_names,
    }


@router.patch("/student/{student_id}/ask/{event_id}/signal")
async def signal_ask(student_id: str, event_id: str, body: SignalRequest):
    """
    Record a resolution, confusion, or task-intent signal on a learning_event.
    Signals feed the concept_confusion_worker compounding asset.

    signal_type:
      "resolved"  → event_type = feedback_positive  (confusion resolved)
      "confused"  → event_type = feedback_negative  (confusion persists)
      "task"      → event_type = feedback_positive + auto-create student_task
    """
    if body.signal_type not in ("resolved", "confused", "task"):
        raise HTTPException(status_code=400, detail="signal_type must be resolved | confused | task")

    event_type_map = {
        "resolved": "feedback_positive",
        "confused":  "feedback_negative",
        "task":      "feedback_positive",
    }
    new_event_type = event_type_map[body.signal_type]

    async with _db() as db:
        # Fetch current metadata to merge
        rows = await _get(db, "learning_events", {
            "id":     f"eq.{event_id}",
            "select": "metadata",
        })
        current_meta: dict = (rows[0].get("metadata") or {}) if rows else {}

        # Merge signal into metadata
        current_meta["resolution_signal"] = (body.signal_type == "resolved") or (body.signal_type == "task")
        current_meta["confusion_signal"]  = body.signal_type == "confused"
        if body.signal_type == "task":
            current_meta["task_generated"] = True

        await _patch(db, "learning_events",
                     {"id": event_id},
                     {"event_type": new_event_type, "metadata": current_meta})

        # Auto-create student_task when signal_type == "task"
        task_id = None
        if body.signal_type == "task" and body.task_title:
            task_title = body.task_title.strip()[:200]
            course     = current_meta.get("course_code")
            try:
                task = await _post(db, "student_tasks", {
                    "student_id":  student_id,
                    "tenant_id":   TENANT_ID,
                    "title":       task_title,
                    "task_type":   "reading",
                    "priority":    2,
                    "status":      "pending",
                    "course_code": course,
                })
                task_id = task.get("id")
                # Back-patch learning_event with task_id
                current_meta["task_id"] = task_id
                await _patch(db, "learning_events", {"id": event_id}, {"metadata": current_meta})
            except Exception as exc:
                _ask_log.warning("task creation from ask failed (non-fatal): %s", exc)

    return {
        "event_id":   event_id,
        "signal":     body.signal_type,
        "event_type": new_event_type,
        "task_id":    task_id,
    }
