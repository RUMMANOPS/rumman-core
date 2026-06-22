"""
progress_api.py — RUMMAN Student Progress API

Read-only adapter over student progress tables.
Does NOT write to the database.

student_id is supplied by the caller (temporary — will derive from auth token later).
program_code is NEVER accepted from the caller — always resolved from student_program_profile.

Endpoints:
  GET /v1/progress/profile
"""
from __future__ import annotations

import os

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

_EXCLUDED_PROGRAMS    = frozenset({"LAW"})
_EXCLUDED_DEGREE_TYPES = frozenset({"diploma"})

_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}

router = APIRouter(prefix="/v1/progress", tags=["progress"])


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
# GET /v1/progress/profile
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/profile")
async def get_progress_profile(
    student_id: str = Query(..., description="Student UUID"),
):
    """
    Returns the student's active program profile with catalog metadata.

    - program_code is resolved from student_program_profile (is_active=true).
    - 404 if no active profile exists.
    - 409 if multiple active profiles exist (should never happen; partial unique
      index uq_student_program_profile_one_active enforces one-active-per-student).
    - 404 if the declared program is LAW or diploma.
    """
    async with _db() as db:
        profile_rows = await _get(db, "student_program_profile", {
            "student_id": f"eq.{student_id}",
            "is_active":  "eq.true",
            "tenant_id":  f"eq.{TENANT_ID}",
            "select":     (
                "id,student_id,program_code,catalog_version_id,"
                "declared_at,started_at,source,is_active,created_at"
            ),
        })

    if not profile_rows:
        raise HTTPException(
            404,
            detail=f"No active program profile found for student '{student_id}'.",
        )

    if len(profile_rows) > 1:
        raise HTTPException(
            409,
            detail=(
                f"Multiple active profiles found for student '{student_id}'. "
                "Data integrity error — only one active program is permitted per student."
            ),
        )

    profile      = profile_rows[0]
    program_code = profile["program_code"]

    if program_code.upper() in _EXCLUDED_PROGRAMS:
        raise HTTPException(
            404,
            detail=f"Program '{program_code}' is not available on this surface.",
        )

    async with _db() as db:
        prog_rows = await _get(db, "v_draft_catalog_programs", {
            "tenant_id":    f"eq.{TENANT_ID}",
            "program_code": f"eq.{program_code.upper()}",
            "select":       (
                "program_code,degree_type,official_program_name_ar,official_program_name_en,"
                "total_credits_official,total_credits_alt,num_levels,"
                "support_level,program_status,college_code,college_name_ar,college_name_en,"
                "catalog_status,version_code"
            ),
        })

    if not prog_rows:
        raise HTTPException(
            404,
            detail=f"Program '{program_code}' not found in active catalog.",
        )

    prog = prog_rows[0]

    if prog.get("degree_type") in _EXCLUDED_DEGREE_TYPES:
        raise HTTPException(
            404,
            detail=(
                f"Program '{program_code}' is not served on this surface "
                f"(degree type: {prog.get('degree_type')})."
            ),
        )

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
