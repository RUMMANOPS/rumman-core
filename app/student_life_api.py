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
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
TENANT_ID    = "00000000-0000-0000-0000-000000000001"

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
    course_code:    Optional[str]      = None
    notes:          Optional[str]      = None
    remind_at:      Optional[str]      = None
    exam_date:      Optional[str]      = None


class TaskUpdate(BaseModel):
    status:         Optional[str]      = None
    title:          Optional[str]      = None
    priority:       Optional[int]      = None
    due_at:         Optional[str]      = None
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

        # Upcoming exam proximity (days_to_exam per course)
        # Pull from academic_calendar where event mentions a course code
        cal_rows = await _get(db, "academic_calendar", {
            "select":  "event_type,event_name_ar,start_date,end_date",
            "order":   "start_date.asc",
            "limit":   "50",
        })
        exam_proximities = []
        for row in cal_rows:
            name = row.get("event_name_ar") or ""
            if "اختبار" in name or row.get("event_type") in ("midterm_exam", "final_exam", "exam"):
                try:
                    ed = datetime.fromisoformat(row["start_date"])
                    days = (ed.date() - now.date()).days
                    if 0 <= days <= 14:
                        exam_proximities.append({
                            "event_name": name,
                            "event_date": row["start_date"],
                            "days_away":  days,
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
            if req.get("deadline_at") and req["deadline_at"][:10] <= _days_from_now(1)[:10]:
                urgent.append({
                    "type":     "request_deadline",
                    "urgency":  "high",
                    "title":    f"موعد طلب قريب: {req['title']}",
                    "ref_id":   req["id"],
                    "ref_type": "request",
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
            "select":     "id,title,task_type,priority,due_at,course_code,status",
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
    if body.title is not None:       payload["title"]        = body.title
    if body.priority is not None:    payload["priority"]     = body.priority
    if body.due_at is not None:      payload["due_at"]       = body.due_at
    if body.snoozed_until is not None: payload["snoozed_until"] = body.snoozed_until
    if body.notes is not None:       payload["notes"]        = body.notes
    if body.acted_on_at is not None: payload["acted_on_at"]  = body.acted_on_at

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
            "select":    "id,item_type,title,content,course_code,due_date,confidence,created_at",
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
            "select":      "item_type,title,content,due_date,created_at",
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
