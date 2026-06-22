"""
progress_api.py — RUMMAN Student Progress API

Read-only adapter over student progress tables.
Does NOT write to the database.

student_id is supplied by the caller (temporary — will derive from auth token later).
program_code is NEVER accepted from the caller — always resolved from student_program_profile.

Endpoints:
  GET /v1/progress/profile
  GET /v1/progress/completed
  GET /v1/progress/current
  GET /v1/progress/summary
  GET /v1/progress/plan-status
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Query

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = (
    os.environ.get("SUPABASE_KEY")
    or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
)
TENANT_ID = "00000000-0000-0000-0000-000000000001"

_EXCLUDED_PROGRAMS     = frozenset({"LAW"})
_EXCLUDED_DEGREE_TYPES = frozenset({"diploma"})

# A registered section counts as a "current course" only when the student is
# actually enrolled/approved. 'planned' is a smart-registration draft (not an
# enrollment), 'dropped' was abandoned, 'needs_review' is unresolved — none of
# these are current. PostgREST filter form: status=in.(active,approved)
_CURRENT_SECTION_STATUSES = ("active", "approved")
_CURRENT_SECTION_FILTER   = f"in.({','.join(_CURRENT_SECTION_STATUSES)})"

_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}

router = APIRouter(prefix="/v1/progress", tags=["progress"])


# ─────────────────────────────────────────────────────────────────────────────
# DB transport
# ─────────────────────────────────────────────────────────────────────────────

def _db() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=f"{SUPABASE_URL}/rest/v1",
        headers=_HEADERS,
        timeout=15,
    )


async def _get(db: httpx.AsyncClient, table: str, params: dict) -> list[dict]:
    r = await db.get(f"/{table}", params=params)
    if r.status_code not in (200, 206):
        raise HTTPException(r.status_code, detail=r.text[:300])
    data = r.json()
    return data if isinstance(data, list) else []


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers — all accept an open db context
# ─────────────────────────────────────────────────────────────────────────────

async def _require_active_profile(db: httpx.AsyncClient, student_id: str) -> dict:
    """
    Fetch the single active profile for this student.
    Raises 404 / 409 / 404-LAW as appropriate.
    """
    rows = await _get(db, "student_program_profile", {
        "student_id": f"eq.{student_id}",
        "is_active":  "eq.true",
        "tenant_id":  f"eq.{TENANT_ID}",
        "select":     "id,student_id,program_code,declared_at,started_at,source,is_active,created_at",
    })
    if not rows:
        raise HTTPException(
            404,
            detail=f"No active program profile found for student '{student_id}'.",
        )
    if len(rows) > 1:
        raise HTTPException(
            409,
            detail=(
                f"Multiple active profiles found for student '{student_id}'. "
                "Data integrity error — only one active program is permitted per student."
            ),
        )
    profile = rows[0]
    if profile["program_code"].upper() in _EXCLUDED_PROGRAMS:
        raise HTTPException(
            404,
            detail=f"Program '{profile['program_code']}' is not available on this surface.",
        )
    return profile


async def _require_catalog_program(db: httpx.AsyncClient, program_code: str) -> dict:
    """
    Fetch catalog program row. Raises 404 if not found or is a diploma.
    """
    rows = await _get(db, "v_draft_catalog_programs", {
        "tenant_id":    f"eq.{TENANT_ID}",
        "program_code": f"eq.{program_code.upper()}",
        "select":       (
            "program_code,degree_type,official_program_name_ar,official_program_name_en,"
            "total_credits_official,total_credits_alt,num_levels,"
            "support_level,program_status,college_code,college_name_ar,college_name_en,"
            "catalog_status,version_code"
        ),
    })
    if not rows:
        raise HTTPException(404, detail=f"Program '{program_code}' not found in active catalog.")
    prog = rows[0]
    if prog.get("degree_type") in _EXCLUDED_DEGREE_TYPES:
        raise HTTPException(
            404,
            detail=(
                f"Program '{program_code}' is not served on this surface "
                f"(degree type: {prog.get('degree_type')})."
            ),
        )
    return prog


async def _fetch_credit_hours_map(
    db: httpx.AsyncClient,
    program_code: str,
    canonical_codes: list[str],
) -> dict[str, Any]:
    """
    Returns a dict {canonical_code: catalog_row} for the given codes in this program.
    credit_hours comes from cat_program_courses — authoritative, per-program value.
    credit_hours_banner is NEVER used here.
    """
    if not canonical_codes:
        return {}
    rows = await _get(db, "v_draft_catalog_program_courses", {
        "tenant_id":              f"eq.{TENANT_ID}",
        "program_code":           f"eq.{program_code}",
        "canonical_course_code":  f"in.({','.join(canonical_codes)})",
        "select":                 (
            "canonical_course_code,credit_hours,level,category,"
            "is_required,is_elective,official_title_ar,official_title_en"
        ),
    })
    return {r["canonical_course_code"]: r for r in rows}


def _build_null_warning(count: int) -> dict:
    return {
        "code":    "canonical_code_missing",
        "message": (
            f"{count} course(s) have no canonical_course_code — "
            "they cannot be matched to the catalog. Credits may be understated."
        ),
        "count": count,
    }


def _build_needs_review_warning() -> dict:
    return {
        "code":    "needs_review_program",
        "message": "Program credit totals are under review — figures may change.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /v1/progress/profile
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/profile")
async def get_progress_profile(
    student_id: str = Query(..., description="Student UUID"),
):
    """
    Returns the student's active program profile with catalog metadata.
    program_code is resolved from student_program_profile (is_active=true).
    """
    async with _db() as db:
        profile = await _require_active_profile(db, student_id)
        prog    = await _require_catalog_program(db, profile["program_code"])

    return {
        "student_id":             profile["student_id"],
        "profile_id":             profile["id"],
        "program_code":           prog["program_code"],
        "degree_type":            prog.get("degree_type"),
        "official_name_ar":       prog.get("official_program_name_ar"),
        "official_name_en":       prog.get("official_program_name_en"),
        "catalog_status":         prog.get("catalog_status"),
        "program_status":         prog.get("program_status"),
        "support_level":          prog.get("support_level"),
        "total_credits_official": prog.get("total_credits_official"),
        "total_credits_alt":      prog.get("total_credits_alt"),
        "num_levels":             prog.get("num_levels"),
        "needs_review":           prog.get("program_status") == "needs_review",
        "college_code":           prog.get("college_code"),
        "college_name_ar":        prog.get("college_name_ar"),
        "college_name_en":        prog.get("college_name_en"),
        "declared_at":            profile.get("declared_at"),
        "started_at":             profile.get("started_at"),
        "source":                 profile.get("source"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /v1/progress/completed
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/completed")
async def get_progress_completed(
    student_id: str = Query(..., description="Student UUID"),
):
    """
    All courses the student has completed (is_counted=true in student_course_history).

    credit_hours per course comes from cat_program_courses for the student's program —
    never from credit_hours_banner.

    Courses with canonical_course_code=NULL are included with catalog_match=false
    and credit_hours=null; they do NOT contribute to completed_credits.
    """
    async with _db() as db:
        profile      = await _require_active_profile(db, student_id)
        prog         = await _require_catalog_program(db, profile["program_code"])
        # Use the catalog's canonical program_code casing for all downstream
        # queries. A profile storing a different case (e.g. 'cs') would otherwise
        # silently return an empty credit map → completed_credits=0 with no error.
        program_code = prog["program_code"]

        history_rows = await _get(db, "student_course_history", {
            "student_id": f"eq.{student_id}",
            "is_counted": "eq.true",
            "tenant_id":  f"eq.{TENANT_ID}",
            "select":     (
                "id,canonical_course_code,banner_course_code,course_state,"
                "term_code,source,confidence,verified_by_student"
            ),
            "order": "term_code.desc",
        })

        canonical_codes = [
            r["canonical_course_code"] for r in history_rows
            if r.get("canonical_course_code")
        ]
        catalog_map = await _fetch_credit_hours_map(db, program_code, canonical_codes)

    courses = []
    null_count = 0
    for r in history_rows:
        cc  = r.get("canonical_course_code")
        cat = catalog_map.get(cc) if cc else None
        if cat:
            courses.append({
                "canonical_course_code": cc,
                "banner_course_code":    r["banner_course_code"],
                "catalog_match":         True,
                "course_state":          r["course_state"],
                "credit_hours":          cat["credit_hours"],  # catalog-authoritative
                "level":                 cat.get("level"),
                "category":              cat.get("category"),
                "is_required":           cat.get("is_required"),
                "official_title_ar":     cat.get("official_title_ar"),
                "official_title_en":     cat.get("official_title_en"),
                "term_code":             r.get("term_code"),
                "source":                r.get("source"),
                "confidence":            r.get("confidence"),
                "verified_by_student":   r.get("verified_by_student"),
            })
        else:
            null_count += 1
            courses.append({
                "canonical_course_code": cc,
                "banner_course_code":    r["banner_course_code"],
                "catalog_match":         False,
                "course_state":          r["course_state"],
                "credit_hours":          None,  # cannot compute without canonical link
                "term_code":             r.get("term_code"),
                "source":                r.get("source"),
                "confidence":            r.get("confidence"),
                "verified_by_student":   r.get("verified_by_student"),
                "warning":               "canonical_code_missing" if not cc else "not_in_catalog",
            })

    completed_credits = sum(
        c["credit_hours"] for c in courses
        if c["catalog_match"] and c["credit_hours"] is not None
    )

    warnings = []
    if null_count:
        warnings.append(_build_null_warning(null_count))
    if prog.get("program_status") == "needs_review":
        warnings.append(_build_needs_review_warning())

    return {
        "student_id":        student_id,
        "program_code":      program_code,
        "completed_credits": completed_credits,
        "count":             len(courses),
        "courses":           courses,
        "warnings":          warnings,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /v1/progress/current
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/current")
async def get_progress_current(
    student_id: str = Query(..., description="Student UUID"),
):
    """
    Courses the student is currently enrolled in.

    Source of truth: student_registered_sections, filtered to status IN
    ('active','approved') — 'planned' (draft), 'dropped', and 'needs_review'
    are NOT current courses. student_course_history.course_state='in_progress'
    is intentionally NOT used here, to avoid a course appearing twice.

    Rows with canonical_course_code=NULL are included with catalog_match=false.
    credit_hours from student_registered_sections is never used in calculations
    (informational only — same semantics as credit_hours_banner).
    """
    async with _db() as db:
        profile      = await _require_active_profile(db, student_id)
        prog         = await _require_catalog_program(db, profile["program_code"])
        # Use the catalog's canonical program_code casing for all downstream
        # queries. A profile storing a different case (e.g. 'cs') would otherwise
        # silently return an empty credit map → completed_credits=0 with no error.
        program_code = prog["program_code"]

        section_rows = await _get(db, "student_registered_sections", {
            "student_id": f"eq.{student_id}",
            "tenant_id":  f"eq.{TENANT_ID}",
            "status":     _CURRENT_SECTION_FILTER,   # active/approved only — not planned/dropped
            "select":     (
                "id,term_code,crn,banner_course_code,canonical_course_code,"
                "course_name,status,source,delivery_mode,created_at"
            ),
            "order": "term_code.desc",
        })

        canonical_codes = [
            r["canonical_course_code"] for r in section_rows
            if r.get("canonical_course_code")
        ]
        catalog_map = await _fetch_credit_hours_map(db, program_code, canonical_codes)

    courses = []
    null_count = 0
    for r in section_rows:
        cc  = r.get("canonical_course_code")
        cat = catalog_map.get(cc) if cc else None
        if cat:
            courses.append({
                "canonical_course_code": cc,
                "banner_course_code":    r["banner_course_code"],
                "catalog_match":         True,
                "term_code":             r.get("term_code"),
                "crn":                   r.get("crn"),
                "registration_status":   r.get("status"),
                "delivery_mode":         r.get("delivery_mode"),
                "credit_hours":          cat["credit_hours"],  # catalog-authoritative
                "level":                 cat.get("level"),
                "category":              cat.get("category"),
                "is_required":           cat.get("is_required"),
                "official_title_ar":     cat.get("official_title_ar"),
                "official_title_en":     cat.get("official_title_en"),
                "source":                r.get("source"),
            })
        else:
            null_count += 1
            courses.append({
                "canonical_course_code": cc,
                "banner_course_code":    r["banner_course_code"],
                "catalog_match":         False,
                "term_code":             r.get("term_code"),
                "crn":                   r.get("crn"),
                "registration_status":   r.get("status"),
                "delivery_mode":         r.get("delivery_mode"),
                "credit_hours":          None,
                "course_name_banner":    r.get("course_name"),
                "source":                r.get("source"),
                "warning":               "canonical_code_missing" if not cc else "not_in_catalog",
            })

    warnings = []
    if null_count:
        warnings.append(_build_null_warning(null_count))
    if prog.get("program_status") == "needs_review":
        warnings.append(_build_needs_review_warning())

    return {
        "student_id":   student_id,
        "program_code": program_code,
        "count":        len(courses),
        "courses":      courses,
        "warnings":     warnings,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /v1/progress/summary
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/summary")
async def get_progress_summary(
    student_id: str = Query(..., description="Student UUID"),
):
    """
    High-level progress snapshot.

    completed_credits and remaining_credits are computed exclusively from
    cat_program_courses.credit_hours — never from credit_hours_banner.
    No grades or GPA are used or returned.
    """
    async with _db() as db:
        profile      = await _require_active_profile(db, student_id)
        prog         = await _require_catalog_program(db, profile["program_code"])
        # Use the catalog's canonical program_code casing for all downstream
        # queries. A profile storing a different case (e.g. 'cs') would otherwise
        # silently return an empty credit map → completed_credits=0 with no error.
        program_code = prog["program_code"]

        # Completed courses
        history_rows = await _get(db, "student_course_history", {
            "student_id": f"eq.{student_id}",
            "is_counted": "eq.true",
            "tenant_id":  f"eq.{TENANT_ID}",
            "select":     "canonical_course_code,banner_course_code",
        })

        canonical_codes = [
            r["canonical_course_code"] for r in history_rows
            if r.get("canonical_course_code")
        ]
        catalog_map = await _fetch_credit_hours_map(db, program_code, canonical_codes)

        # Current registrations count — active/approved only (decision: §6 Issue 1)
        section_rows = await _get(db, "student_registered_sections", {
            "student_id": f"eq.{student_id}",
            "tenant_id":  f"eq.{TENANT_ID}",
            "status":     _CURRENT_SECTION_FILTER,
            "select":     "id",
        })

    # Compute credits — catalog only, never Banner
    completed_credits = sum(
        catalog_map[r["canonical_course_code"]]["credit_hours"]
        for r in history_rows
        if r.get("canonical_course_code") and r["canonical_course_code"] in catalog_map
        and catalog_map[r["canonical_course_code"]]["credit_hours"] is not None
    )

    total_credits   = prog.get("total_credits_official") or 0
    remaining       = max(0, total_credits - completed_credits) if total_credits else None
    null_count      = sum(1 for r in history_rows if not r.get("canonical_course_code"))

    warnings = []
    if null_count:
        warnings.append(_build_null_warning(null_count))
    if prog.get("program_status") == "needs_review":
        warnings.append(_build_needs_review_warning())

    return {
        "student_id":              student_id,
        "program_code":            program_code,
        "degree_type":             prog.get("degree_type"),
        "total_credits_official":  prog.get("total_credits_official"),
        "completed_credits":       completed_credits,
        "remaining_credits":       remaining,
        "completed_courses_count": len(history_rows),
        "current_courses_count":   len(section_rows),
        "needs_review":            prog.get("program_status") == "needs_review",
        "warnings":                warnings,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /v1/progress/plan-status
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/plan-status")
async def get_progress_plan_status(
    student_id: str = Query(..., description="Student UUID"),
):
    """
    Full study plan for the student's program with progress status overlaid.

    Status values per course:
      completed      — is_counted=true in student_course_history
      in_progress    — canonical_course_code in student_registered_sections
      not_started    — not yet attempted
      blocked_unknown — student has history rows for this term with no canonical link;
                        true prerequisite blocking requires full prereq logic (future)

    NOTE: available/blocked prerequisite logic is NOT implemented in this endpoint.
    When that layer is ready, `not_started` courses will be split into
    `available` and `blocked` based on cat_prerequisites.
    """
    async with _db() as db:
        profile      = await _require_active_profile(db, student_id)
        prog         = await _require_catalog_program(db, profile["program_code"])
        # Use the catalog's canonical program_code casing for all downstream
        # queries. A profile storing a different case (e.g. 'cs') would otherwise
        # silently return an empty credit map → completed_credits=0 with no error.
        program_code = prog["program_code"]

        # All courses in the program (catalog-authoritative)
        plan_rows = await _get(db, "v_draft_catalog_program_courses", {
            "tenant_id":    f"eq.{TENANT_ID}",
            "program_code": f"eq.{program_code}",
            "select":       (
                "canonical_course_code,official_course_code_raw,normalized_course_code,"
                "official_title_ar,official_title_en,level,credit_hours,"
                "category,is_required,is_elective,elective_group,track"
            ),
            "order": "level,is_required.desc,canonical_course_code",
        })

        # Completed set: canonical codes where is_counted=true
        completed_rows = await _get(db, "student_course_history", {
            "student_id": f"eq.{student_id}",
            "is_counted": "eq.true",
            "tenant_id":  f"eq.{TENANT_ID}",
            "select":     "canonical_course_code,banner_course_code,course_state,term_code,confidence",
        })

        # In-progress set: active/approved registered sections only (§6 Issue 1)
        section_rows = await _get(db, "student_registered_sections", {
            "student_id": f"eq.{student_id}",
            "tenant_id":  f"eq.{TENANT_ID}",
            "status":     _CURRENT_SECTION_FILTER,
            "select":     "canonical_course_code,banner_course_code,term_code,status",
        })

    # Build lookup sets
    completed_map: dict[str, dict] = {}
    for r in completed_rows:
        cc = r.get("canonical_course_code")
        if cc:
            completed_map[cc] = r

    in_progress_map: dict[str, dict] = {}
    for r in section_rows:
        cc = r.get("canonical_course_code")
        if cc:
            in_progress_map[cc] = r

    # Orphaned history rows (canonical=NULL — counted somewhere but can't be placed on plan)
    orphaned = [r for r in completed_rows if not r.get("canonical_course_code")]

    # Overlay status on each catalog course
    from collections import defaultdict
    by_level: dict = defaultdict(list)
    for course in plan_rows:
        cc     = course["canonical_course_code"]
        status = "not_started"
        detail: dict = {}

        if cc in completed_map:
            status = "completed"
            detail = {
                "course_state": completed_map[cc].get("course_state"),
                "term_code":    completed_map[cc].get("term_code"),
                "confidence":   completed_map[cc].get("confidence"),
            }
        elif cc in in_progress_map:
            status = "in_progress"
            detail = {
                "term_code":             in_progress_map[cc].get("term_code"),
                "registration_status":   in_progress_map[cc].get("status"),
            }

        by_level[course.get("level")].append({
            "canonical_course_code":  cc,
            "official_code_raw":      course.get("official_course_code_raw"),
            "official_title_ar":      course.get("official_title_ar"),
            "official_title_en":      course.get("official_title_en"),
            "credit_hours":           course["credit_hours"],  # catalog-authoritative
            "category":               course.get("category"),
            "is_required":            course.get("is_required"),
            "is_elective":            course.get("is_elective"),
            "status":                 status,
            **detail,
        })

    levels = []
    for lvl in sorted(by_level.keys(), key=lambda x: (x is None, x)):
        courses_at_level = by_level[lvl]
        levels.append({
            "level":            lvl,
            "course_count":     len(courses_at_level),
            "completed_count":  sum(1 for c in courses_at_level if c["status"] == "completed"),
            "in_progress_count": sum(1 for c in courses_at_level if c["status"] == "in_progress"),
            "courses":          courses_at_level,
        })

    warnings = []
    if orphaned:
        warnings.append({
            "code":    "orphaned_completed_courses",
            "message": (
                f"{len(orphaned)} completed course(s) have no canonical_course_code "
                "and cannot be placed on the plan. Credits may be understated."
            ),
            "count": len(orphaned),
        })
    in_progress_null = [r for r in section_rows if not r.get("canonical_course_code")]
    if in_progress_null:
        warnings.append({
            "code":    "current_registration_unmatched",
            "message": (
                f"{len(in_progress_null)} current registration(s) have no canonical_course_code "
                "and are not shown on the plan."
            ),
            "count": len(in_progress_null),
        })
    if prog.get("program_status") == "needs_review":
        warnings.append(_build_needs_review_warning())

    # Scaffold note — will be replaced when prerequisite layer is implemented
    warnings.append({
        "code":    "prereq_check_not_implemented",
        "message": (
            "'not_started' courses are not yet split into available/blocked. "
            "Prerequisite checking requires the progress prerequisite layer (future milestone)."
        ),
    })

    return {
        "student_id":             student_id,
        "program_code":           program_code,
        "degree_type":            prog.get("degree_type"),
        "official_name_ar":       prog.get("official_program_name_ar"),
        "official_name_en":       prog.get("official_program_name_en"),
        "total_credits_official": prog.get("total_credits_official"),
        "needs_review":           prog.get("program_status") == "needs_review",
        "levels":                 levels,
        "orphaned_completed":     [
            {"banner_course_code": r.get("banner_course_code"), "status": "blocked_unknown"}
            for r in orphaned
        ],
        "warnings":               warnings,
    }
