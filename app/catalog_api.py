"""
catalog_api.py — RUMMAN Official Catalog Adapter

Read-only adapter over the cat_* catalog tables (via v_draft_catalog_* QA views).
Does NOT touch inst_courses, inst_specializations, or any non-cat_* table.
Does NOT write to the database.

Active surface rule (enforced at view level AND in this adapter):
  support_level = 'active' AND status IN ('ready', 'needs_review')

  LAW   → reference/provisional_conflicted → excluded (belt-and-suspenders guard added)
  Diplomas → future/ready → excluded from active program endpoints
  PH/MDM/FIN → active/needs_review → included; needs_review=True in responses

Endpoints:
  GET /v1/catalog/version
  GET /v1/catalog/programs
  GET /v1/catalog/programs/{program_code}
  GET /v1/catalog/programs/{program_code}/courses
  GET /v1/catalog/programs/{program_code}/plan
  GET /v1/catalog/programs/{program_code}/prerequisites
  GET /v1/catalog/courses/{course_code}/programs
  GET /v1/catalog/aliases/{alias_label}
"""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Any, Optional

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

# Programs that are NEVER served regardless of view filter (belt-and-suspenders)
_EXCLUDED_PROGRAMS = frozenset({"LAW"})
# Degree types never served in active registration endpoints
_EXCLUDED_DEGREE_TYPES = frozenset({"diploma"})

# Catalog statuses considered "serving" — draft/validated are both pre-activation states
# that serve data via v_draft_catalog_* views; active is post-activation.
_SERVING_STATUSES = frozenset({"draft", "validated", "active"})

_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}

router = APIRouter(prefix="/v1/catalog", tags=["catalog"])


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _db() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=f"{SUPABASE_URL}/rest/v1",
        headers=_HEADERS,
        timeout=15,
    )


async def _get(
    db: httpx.AsyncClient,
    view: str,
    params: dict,
    *,
    allow_empty: bool = True,
) -> list[dict]:
    r = await db.get(f"/{view}", params=params)
    if r.status_code not in (200, 206):
        raise HTTPException(r.status_code, detail=r.text[:300])
    data = r.json()
    return data if isinstance(data, list) else []


def _guard_program(program_code: str) -> None:
    """Raise 404 for explicitly excluded programs (LAW) before any DB call."""
    if program_code.upper() in _EXCLUDED_PROGRAMS:
        raise HTTPException(404, detail=f"Program '{program_code}' is not available on this surface.")


def _guard_program_row(row: dict, program_code: str) -> None:
    """Raise 404 if a fetched program row is excluded (diploma, LAW). Belt-and-suspenders."""
    if row.get("program_code", "").upper() in _EXCLUDED_PROGRAMS:
        raise HTTPException(404, detail=f"Program '{program_code}' is not available on this surface.")
    if row.get("degree_type") in _EXCLUDED_DEGREE_TYPES:
        raise HTTPException(404, detail=f"Program '{program_code}' is not served on this surface (degree type: {row.get('degree_type')}).")


def _strip_catalog_envelope(row: dict) -> dict:
    """Remove internal catalog version fields from public responses."""
    return {k: v for k, v in row.items() if k not in ("catalog_version_id", "tenant_id", "id")}


def _program_summary(row: dict) -> dict:
    return {
        "program_code":              row["program_code"],
        "degree_type":               row["degree_type"],
        "official_name_ar":          row.get("official_program_name_ar"),
        "official_name_en":          row.get("official_program_name_en"),
        "total_credits_official":    row.get("total_credits_official"),
        "total_credits_alt":         row.get("total_credits_alt"),
        "num_levels":                row.get("num_levels"),
        "support_level":             row["support_level"],
        "needs_review":              row.get("program_status") == "needs_review",
        "college_code":              row.get("college_code"),
        "college_name_ar":           row.get("college_name_ar"),
        "college_name_en":           row.get("college_name_en"),
        "catalog_status":            row.get("catalog_status"),
        "version_code":              row.get("version_code"),
    }


def _course_row(row: dict) -> dict:
    return {
        "canonical_course_code":  row["canonical_course_code"],
        "official_course_code_raw": row.get("official_course_code_raw"),
        "normalized_course_code": row.get("normalized_course_code"),
        "official_title_ar":      row.get("official_title_ar"),
        "official_title_en":      row.get("official_title_en"),
        "source_language":        row.get("source_language"),
        "program_code":           row["program_code"],
        "level":                  row.get("level"),
        "credit_hours":           row["credit_hours"],
        "category":               row.get("category"),
        "is_required":            row.get("is_required"),
        "is_elective":            row.get("is_elective"),
        "elective_group":         row.get("elective_group"),
        "track":                  row.get("track"),
        "choose_rule":            row.get("choose_rule"),
        "choose_count":           row.get("choose_count"),
        "choose_credits":         row.get("choose_credits"),
        "needs_human_review":     row.get("needs_human_review"),
        "requirement_note":       row.get("requirement_note"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/version")
async def get_catalog_version():
    """Current catalog version metadata."""
    async with _db() as db:
        rows = await _get(db, "catalog_versions", {
            "tenant_id": f"eq.{TENANT_ID}",
            "select":    "version_code,status,notes,created_at,validated_at,activated_at,metadata",
            "order":     "created_at.desc",
            "limit":     "1",
        })
    if not rows:
        raise HTTPException(404, detail="No catalog version found.")
    row = rows[0]
    status = row["status"]
    return {
        "version_code":   row["version_code"],
        "status":         status,
        "notes":          row.get("notes"),
        "created_at":     row.get("created_at"),
        "validated_at":   row.get("validated_at"),
        "activated_at":   row.get("activated_at"),
        "is_draft":       status == "draft",
        "is_validated":   status == "validated",
        "is_active":      status == "active",
        "is_serving":     status in _SERVING_STATUSES,
    }


@router.get("/programs")
async def list_programs(
    degree_type: Optional[str] = Query(None, description="Filter by degree_type (bachelor/master/executive_master)"),
):
    """
    All active programs (support_level=active, status IN ready/needs_review).
    LAW excluded. Diplomas (future) excluded. PH/MDM/FIN included with needs_review=True.
    """
    async with _db() as db:
        params: dict[str, Any] = {
            "tenant_id": f"eq.{TENANT_ID}",
            "select":    (
                "program_code,degree_type,official_program_name_ar,official_program_name_en,"
                "total_credits_official,total_credits_alt,num_levels,support_level,program_status,"
                "college_code,college_name_ar,college_name_en,catalog_status,version_code"
            ),
            "order":     "degree_type,program_code",
        }
        if degree_type:
            params["degree_type"] = f"eq.{degree_type}"
        rows = await _get(db, "v_draft_catalog_programs", params)

    # Belt-and-suspenders: exclude any slipped-through excluded programs
    programs = [
        _program_summary(r) for r in rows
        if r["program_code"] not in _EXCLUDED_PROGRAMS
        and r.get("degree_type") not in _EXCLUDED_DEGREE_TYPES
    ]
    return {"count": len(programs), "programs": programs}


@router.get("/programs/{program_code}")
async def get_program(program_code: str):
    """
    Single active program by code. Includes course count and credit totals.
    LAW returns 404. Diplomas return 404.
    """
    _guard_program(program_code)
    async with _db() as db:
        rows = await _get(db, "v_draft_catalog_programs", {
            "tenant_id":    f"eq.{TENANT_ID}",
            "program_code": f"eq.{program_code.upper()}",
            "select":       (
                "program_code,degree_type,official_program_name_ar,official_program_name_en,"
                "source_program_name_raw,total_credits_official,total_credits_alt,num_levels,"
                "support_level,program_status,program_metadata,"
                "college_code,college_name_ar,college_name_en,catalog_status,version_code"
            ),
        })
        if not rows:
            raise HTTPException(404, detail=f"Program '{program_code}' not found or not active.")
        prog = rows[0]
        _guard_program_row(prog, program_code)

        # Course count
        course_rows = await _get(db, "v_draft_catalog_program_courses", {
            "tenant_id":    f"eq.{TENANT_ID}",
            "program_code": f"eq.{program_code.upper()}",
            "select":       "canonical_course_code,credit_hours,is_required,is_elective",
        })

    total_courses  = len(course_rows)
    required_count = sum(1 for c in course_rows if c.get("is_required"))
    elective_count = sum(1 for c in course_rows if c.get("is_elective"))

    return {
        **_program_summary(prog),
        "source_program_name_raw": prog.get("source_program_name_raw"),
        "program_metadata":        prog.get("program_metadata"),
        "course_count":            total_courses,
        "required_count":          required_count,
        "elective_count":          elective_count,
    }


@router.get("/programs/{program_code}/courses")
async def list_program_courses(
    program_code: str,
    level: Optional[int]  = Query(None, description="Filter by academic level (1-8)"),
    category: Optional[str] = Query(None, description="Filter by category"),
    required_only: bool   = Query(False, description="Return only required courses"),
    elective_only: bool   = Query(False, description="Return only elective courses"),
):
    """
    All courses in a program with per-program credit_hours, level, and category.
    credit_hours is program-specific — not globally unique per course.
    """
    _guard_program(program_code)
    async with _db() as db:
        # Verify program exists before returning empty course list
        prog_check = await _get(db, "v_draft_catalog_programs", {
            "tenant_id":    f"eq.{TENANT_ID}",
            "program_code": f"eq.{program_code.upper()}",
            "select":       "program_code,degree_type",
            "limit":        "1",
        })
        if not prog_check:
            raise HTTPException(404, detail=f"Program '{program_code}' not found or not active.")
        _guard_program_row(prog_check[0], program_code)

        params: dict[str, Any] = {
            "tenant_id":    f"eq.{TENANT_ID}",
            "program_code": f"eq.{program_code.upper()}",
            "select":       (
                "program_code,canonical_course_code,official_course_code_raw,normalized_course_code,"
                "official_title_ar,official_title_en,source_language,"
                "level,credit_hours,category,category_confidence,"
                "is_required,is_elective,elective_group,track,"
                "choose_rule,choose_count,choose_credits,"
                "needs_human_review,requirement_note,official_raw_text"
            ),
            "order":        "level,canonical_course_code",
        }
        if level is not None:
            params["level"] = f"eq.{level}"
        if category:
            params["category"] = f"eq.{category}"
        if required_only:
            params["is_required"] = "eq.true"
        if elective_only:
            params["is_elective"] = "eq.true"

        rows = await _get(db, "v_draft_catalog_program_courses", params)

    courses = [_course_row(r) for r in rows]
    return {
        "program_code": program_code.upper(),
        "count":        len(courses),
        "courses":      courses,
    }


@router.get("/programs/{program_code}/plan")
async def get_program_plan(program_code: str):
    """
    Study plan layout grouped by academic level.
    Each level contains required courses and elective groups.
    credit_hours are program-specific.
    """
    _guard_program(program_code)
    async with _db() as db:
        # Program header
        prog_rows = await _get(db, "v_draft_catalog_programs", {
            "tenant_id":    f"eq.{TENANT_ID}",
            "program_code": f"eq.{program_code.upper()}",
            "select":       (
                "program_code,degree_type,official_program_name_ar,official_program_name_en,"
                "total_credits_official,num_levels,support_level,program_status,"
                "college_code,college_name_ar,college_name_en"
            ),
        })
        if not prog_rows:
            raise HTTPException(404, detail=f"Program '{program_code}' not found or not active.")
        prog = prog_rows[0]
        _guard_program_row(prog, program_code)

        # All courses
        course_rows = await _get(db, "v_draft_catalog_program_courses", {
            "tenant_id":    f"eq.{TENANT_ID}",
            "program_code": f"eq.{program_code.upper()}",
            "select":       (
                "program_code,canonical_course_code,official_course_code_raw,normalized_course_code,"
                "official_title_ar,official_title_en,"
                "level,credit_hours,category,is_required,is_elective,"
                "elective_group,track,choose_rule,choose_count,choose_credits,needs_human_review"
            ),
            "order":        "level,is_required.desc,canonical_course_code",
        })

    # Group by level
    by_level: dict[Any, list] = defaultdict(list)
    for c in course_rows:
        by_level[c.get("level")].append(c)

    total_credits = sum(
        c["credit_hours"] for c in course_rows
        if c.get("is_required") and c["credit_hours"]
    )

    levels = []
    for lvl in sorted(by_level.keys(), key=lambda x: (x is None, x)):
        courses_at_level = by_level[lvl]
        levels.append({
            "level":          lvl,
            "course_count":   len(courses_at_level),
            "required_credits": sum(
                c["credit_hours"] for c in courses_at_level
                if c.get("is_required") and c["credit_hours"]
            ),
            "courses": [_course_row(c) for c in courses_at_level],
        })

    return {
        "program_code":           prog["program_code"],
        "degree_type":            prog["degree_type"],
        "official_name_ar":       prog.get("official_program_name_ar"),
        "official_name_en":       prog.get("official_program_name_en"),
        "total_credits_official": prog.get("total_credits_official"),
        "num_levels":             prog.get("num_levels"),
        "needs_review":           prog.get("program_status") == "needs_review",
        "college_code":           prog.get("college_code"),
        "required_credits_sum":   total_credits,
        "levels":                 levels,
    }


@router.get("/programs/{program_code}/prerequisites")
async def list_program_prerequisites(program_code: str):
    """
    All prerequisite / corequisite edges for a program.
    requires_canonical_code is None when needs_review=True (unresolved edge).
    All 617 edges in the current catalog have needs_review=False.
    """
    _guard_program(program_code)
    async with _db() as db:
        # Verify program exists and is active before querying prerequisites
        prog_check = await _get(db, "v_draft_catalog_programs", {
            "tenant_id":    f"eq.{TENANT_ID}",
            "program_code": f"eq.{program_code.upper()}",
            "select":       "program_code,degree_type",
            "limit":        "1",
        })
        if not prog_check:
            raise HTTPException(404, detail=f"Program '{program_code}' not found or not active.")
        _guard_program_row(prog_check[0], program_code)

        rows = await _get(db, "v_draft_catalog_prerequisites", {
            "tenant_id":    f"eq.{TENANT_ID}",
            "program_code": f"eq.{program_code.upper()}",
            "select":       (
                "course_code,course_code_raw,course_title_ar,course_title_en,"
                "requires_code_raw,requires_canonical_code,requires_title_ar,requires_title_en,"
                "relation,needs_review,conflict_note,confidence"
            ),
            "order":        "course_code,relation",
        })

    prereqs = [
        {
            "course_code":            r["course_code"],
            "course_code_raw":        r.get("course_code_raw"),
            "course_title_en":        r.get("course_title_en"),
            "course_title_ar":        r.get("course_title_ar"),
            "relation":               r["relation"],
            "requires_code_raw":      r["requires_code_raw"],
            "requires_canonical_code": r.get("requires_canonical_code"),
            "requires_title_en":      r.get("requires_title_en"),
            "requires_title_ar":      r.get("requires_title_ar"),
            "needs_review":           r.get("needs_review", False),
            "conflict_note":          r.get("conflict_note"),
        }
        for r in rows
    ]
    return {
        "program_code": program_code.upper(),
        "count":        len(prereqs),
        "prerequisites": prereqs,
    }


@router.get("/courses/{course_code}/programs")
async def get_course_programs(course_code: str):
    """
    All active programs that include this course, with per-program context.
    credit_hours is per-program and may differ across programs (e.g. ENG001: 8cr in CS, 16cr in IT).
    course_code is NOT assumed to be globally unique — pass canonical or alias form.
    """
    async with _db() as db:
        # Accept both canonical and alias form
        canonical = course_code.strip()
        rows = await _get(db, "v_draft_catalog_program_courses", {
            "tenant_id":              f"eq.{TENANT_ID}",
            "canonical_course_code":  f"eq.{canonical}",
            "select":                 (
                "program_code,degree_type,program_support_level,program_status,total_credits_official,"
                "college_code,college_name_ar,college_name_en,"
                "level,credit_hours,category,is_required,is_elective,"
                "elective_group,track,choose_rule,choose_count,choose_credits"
            ),
            "order":                  "degree_type,program_code",
        })

        # If no rows and code might be an alias, resolve it first
        if not rows:
            alias_rows = await _get(db, "v_draft_catalog_aliases", {
                "tenant_id":   f"eq.{TENANT_ID}",
                "alias_label": f"eq.{canonical}",
                "select":      "canonical_course_code",
                "limit":       "1",
            })
            if alias_rows:
                resolved = alias_rows[0]["canonical_course_code"]
                rows = await _get(db, "v_draft_catalog_program_courses", {
                    "tenant_id":             f"eq.{TENANT_ID}",
                    "canonical_course_code": f"eq.{resolved}",
                    "select":                (
                        "program_code,degree_type,program_support_level,program_status,"
                        "total_credits_official,college_code,college_name_ar,college_name_en,"
                        "level,credit_hours,category,is_required,is_elective,"
                        "elective_group,track,choose_rule,choose_count,choose_credits"
                    ),
                    "order":                 "degree_type,program_code",
                })
                canonical = resolved

    programs_for_course = [
        {
            "program_code":           r["program_code"],
            "degree_type":            r.get("degree_type"),
            "needs_review":           r.get("program_status") == "needs_review",
            "college_code":           r.get("college_code"),
            "college_name_en":        r.get("college_name_en"),
            "level":                  r.get("level"),
            "credit_hours":           r["credit_hours"],
            "category":               r.get("category"),
            "is_required":            r.get("is_required"),
            "is_elective":            r.get("is_elective"),
            "elective_group":         r.get("elective_group"),
        }
        for r in rows
        if r["program_code"] not in _EXCLUDED_PROGRAMS
    ]

    if not programs_for_course:
        raise HTTPException(404, detail=f"Course '{course_code}' not found in any active program.")

    return {
        "canonical_course_code": canonical,
        "queried_as":            course_code,
        "program_count":         len(programs_for_course),
        "programs":              programs_for_course,
    }


@router.get("/aliases/{alias_label}")
async def resolve_alias(alias_label: str):
    """
    Resolve an alias label to its canonical course code.
    Returns the canonical code and basic course identity.
    alias_label is case-sensitive (Latin codes are uppercase by convention).
    """
    async with _db() as db:
        rows = await _get(db, "v_draft_catalog_aliases", {
            "tenant_id":   f"eq.{TENANT_ID}",
            "alias_label": f"eq.{alias_label.strip()}",
            "select":      (
                "alias_label,canonical_course_code,official_course_code_raw,"
                "official_title_ar,official_title_en,source_language,alias_type,confidence"
            ),
            "limit":       "1",
        })

    if not rows:
        raise HTTPException(404, detail=f"Alias '{alias_label}' not found.")
    r = rows[0]
    return {
        "alias_label":            r["alias_label"],
        "canonical_course_code":  r["canonical_course_code"],
        "official_course_code_raw": r.get("official_course_code_raw"),
        "official_title_ar":      r.get("official_title_ar"),
        "official_title_en":      r.get("official_title_en"),
        "alias_type":             r["alias_type"],
        "confidence":             r.get("confidence"),
    }
