"""
auth_api.py — RUMMAN Mobile Authentication & Identity

Frictionless identity for First-100 students.
No passwords. No phone numbers. No friction.

A student launches the app → device generates UUID → we hash it →
create or retrieve their rumman_users row → return student_id.
Their identity builds over time through actions, not credentials.

Endpoints:
  POST /v1/auth/identify              — device_id → student_id (create or get)
  GET  /v1/student/{id}/profile       — get onboarding profile
  PUT  /v1/student/{id}/profile       — save onboarding profile (from OnboardingScreen)
  POST /v1/student/{id}/history       — append to student_history (the Time Asset)
  GET  /v1/ping                       — keep-alive, cold start elimination
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

load_dotenv()

SUPABASE_URL  = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY  = os.environ.get("SUPABASE_KEY", "")
TENANT_ID     = "00000000-0000-0000-0000-000000000001"
USER_SALT     = os.environ.get("RUMMAN_USER_SALT", "rumman-mobile-v1")

_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

_UPSERT_HEADERS = {
    **_HEADERS,
    "Prefer": "resolution=merge-duplicates,return=representation",
}

router = APIRouter(prefix="/v1", tags=["auth"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=f"{SUPABASE_URL}/rest/v1",
        headers=_HEADERS,
        timeout=10,
    )

def _hash_device(device_id: str) -> str:
    return hashlib.sha256(f"{USER_SALT}:mobile:{device_id}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class IdentifyRequest(BaseModel):
    device_id:   str   = Field(..., min_length=10, max_length=200)
    platform_os: Optional[str] = Field(None, pattern="^(ios|android)$")
    app_version: Optional[str] = None


class IdentifyResponse(BaseModel):
    student_id:          str
    is_new:              bool
    onboarding_complete: bool


class OnboardingProfile(BaseModel):
    version:                  int = 1
    university:               str = "SEU"
    university_name:          str = "الجامعة السعودية الإلكترونية"
    college_id:               Optional[str] = None
    college_name_ar:          Optional[str] = None
    college_code:             Optional[str] = None
    specialization_id:        Optional[str] = None
    specialization_name_ar:   Optional[str] = None
    specialization_code:      Optional[str] = None
    current_level:            Optional[int] = None
    gender:                   Optional[str] = None
    enrolled_courses:         list[str] = Field(default_factory=list)
    completed_courses:        list[str] = Field(default_factory=list)
    completed_credit_hours:   Optional[int] = None
    remaining_credit_hours:   Optional[int] = None
    total_credit_hours:       Optional[int] = None
    num_levels:               Optional[int] = None


class HistoryEvent(BaseModel):
    event_type:   str
    event_data:   dict = Field(default_factory=dict)
    course_code:  Optional[str] = None
    caused_by_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Keep-alive / ping — eliminates Railway cold starts when called regularly
# ---------------------------------------------------------------------------

@router.get("/ping")
async def ping():
    return {"status": "alive", "ts": datetime.now(timezone.utc).isoformat()}


# ---------------------------------------------------------------------------
# POST /v1/auth/identify
# ---------------------------------------------------------------------------

@router.post("/auth/identify", response_model=IdentifyResponse)
async def identify(body: IdentifyRequest):
    device_hash = _hash_device(body.device_id)

    async with _db() as db:
        # 1. Check if device already registered
        r = await db.get(
            "/mobile_device_sessions",
            params={"device_hash": f"eq.{device_hash}", "select": "student_id"},
        )
        if r.status_code == 200 and r.json():
            student_id = r.json()[0]["student_id"]
            # Update last_seen_at
            await db.patch(
                "/mobile_device_sessions",
                params={"device_hash": f"eq.{device_hash}"},
                json={"last_seen_at": "now()"},
                headers={**_HEADERS, "Prefer": "return=minimal"},
            )
            # Check if onboarding complete
            profile = await _get_profile_data(db, student_id)
            return IdentifyResponse(
                student_id=student_id,
                is_new=False,
                onboarding_complete=profile is not None,
            )

        # 2. New device — create or get rumman_users row
        r = await db.post(
            "/rumman_users?on_conflict=platform,platform_user_hash",
            json={
                "tenant_id":          TENANT_ID,
                "platform":           "mobile",
                "platform_user_hash": device_hash,
                "opted_into_memory":  True,
            },
            headers=_UPSERT_HEADERS,
        )
        if r.status_code not in (200, 201):
            raise HTTPException(502, f"Failed to create user: {r.text[:200]}")

        rows = r.json()
        if not rows:
            raise HTTPException(502, "Empty response from user upsert")

        student_id = rows[0]["id"]
        is_new = r.status_code == 201

        # 3. Register device
        await db.post(
            "/mobile_device_sessions?on_conflict=device_hash",
            json={
                "student_id":   student_id,
                "tenant_id":    TENANT_ID,
                "device_hash":  device_hash,
                "app_version":  body.app_version,
                "platform_os":  body.platform_os,
                "last_seen_at": "now()",
            },
            headers=_UPSERT_HEADERS,
        )

        # 4. Append to student_history
        await _append_history(db, student_id, "session_started", {
            "platform_os": body.platform_os,
            "app_version": body.app_version,
        })

        return IdentifyResponse(
            student_id=student_id,
            is_new=is_new,
            onboarding_complete=False,
        )


# ---------------------------------------------------------------------------
# GET /v1/student/{id}/profile
# ---------------------------------------------------------------------------

@router.get("/student/{student_id}/profile")
async def get_profile(student_id: str):
    async with _db() as db:
        data = await _get_profile_data(db, student_id)
        if data is None:
            return {"onboarding_complete": False, "profile": None}
        return {"onboarding_complete": True, "profile": data}


# ---------------------------------------------------------------------------
# PUT /v1/student/{id}/profile
# ---------------------------------------------------------------------------

@router.put("/student/{student_id}/profile")
async def save_profile(student_id: str, body: OnboardingProfile):
    now_iso = datetime.now(timezone.utc).isoformat()
    profile_data = body.model_dump()
    profile_data["onboarding_completed_at"] = now_iso

    async with _db() as db:
        # Upsert onboarding_profile into student_context
        r = await db.post(
            "/student_context?on_conflict=user_id,context_type",
            json={
                "user_id":       student_id,
                "tenant_id":     TENANT_ID,
                "context_type":  "onboarding_profile",
                "context_value": profile_data,
                "confidence":    "high",
                "source":        "explicit",
                "observed_count": 1,
                "last_seen_at":  now_iso,
                "expires_at":    None,
            },
            headers=_UPSERT_HEADERS,
        )
        if r.status_code not in (200, 201):
            raise HTTPException(502, f"Failed to save profile: {r.text[:200]}")

        # Also upsert enrolled_courses separately for fast lookup
        if body.enrolled_courses:
            await db.post(
                "/student_context?on_conflict=user_id,context_type",
                json={
                    "user_id":       student_id,
                    "tenant_id":     TENANT_ID,
                    "context_type":  "enrolled_courses",
                    "context_value": {"codes": body.enrolled_courses},
                    "confidence":    "high",
                    "source":        "explicit",
                    "observed_count": 1,
                    "last_seen_at":  now_iso,
                    "expires_at":    None,
                },
                headers=_UPSERT_HEADERS,
            )

        # Append to student_history
        await _append_history(db, student_id, "onboarding_completed", {
            "college_code":          body.college_code,
            "specialization_code":   body.specialization_code,
            "current_level":         body.current_level,
            "enrolled_courses":      body.enrolled_courses,
            "completed_credit_hours": body.completed_credit_hours,
        })

        # Update last_active_at on rumman_users
        await db.patch(
            "/rumman_users",
            params={"id": f"eq.{student_id}"},
            json={"last_active_at": now_iso},
            headers={**_HEADERS, "Prefer": "return=minimal"},
        )

        return {"status": "saved", "student_id": student_id}


# ---------------------------------------------------------------------------
# POST /v1/student/{id}/history
# ---------------------------------------------------------------------------

@router.post("/student/{student_id}/history")
async def append_history(student_id: str, body: HistoryEvent):
    async with _db() as db:
        await _append_history(
            db, student_id,
            body.event_type, body.event_data,
            body.course_code, body.caused_by_id,
        )
    return {"status": "recorded"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _get_profile_data(db: httpx.AsyncClient, student_id: str) -> dict | None:
    r = await db.get(
        "/student_context",
        params={
            "user_id":      f"eq.{student_id}",
            "context_type": "eq.onboarding_profile",
            "select":       "context_value",
        },
    )
    if r.status_code == 200 and r.json():
        return r.json()[0]["context_value"]
    return None


async def _append_history(
    db: httpx.AsyncClient,
    student_id: str,
    event_type: str,
    event_data: dict,
    course_code: str | None = None,
    caused_by_id: str | None = None,
) -> None:
    row: dict[str, Any] = {
        "student_id": student_id,
        "tenant_id":  TENANT_ID,
        "event_type": event_type,
        "event_data": event_data,
    }
    if course_code:
        row["course_code"] = course_code
    if caused_by_id:
        row["caused_by_id"] = caused_by_id
    try:
        await db.post("/student_history", json=row)
    except Exception:
        pass  # history is best-effort, never crash on it
