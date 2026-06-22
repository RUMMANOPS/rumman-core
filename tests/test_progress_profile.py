"""
tests/test_progress_profile.py — Progress API: /v1/progress/profile

All tests use mocked _get to avoid DB writes and to control return data.

Run:
  python3 -m pytest tests/test_progress_profile.py -v
"""
import os
import sys
import pytest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

os.environ.setdefault("SUPABASE_URL", "https://yriavgczteuirigsvedu.supabase.co")
os.environ.setdefault(
    "SUPABASE_SERVICE_ROLE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlyaWF2Z2N6dGV1aXJpZ3N2ZWR1Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3OTU5NDkyOSwiZXhwIjoyMDk1MTcwOTI5fQ.GTAxJc6mpMfe13x_d_QtjiU3QOsdt4NPkFUc_nG6BNg",
)

import progress_api
from fastapi import FastAPI
from fastapi.testclient import TestClient

_app = FastAPI()
_app.include_router(progress_api.router)
client = TestClient(_app, raise_server_exceptions=True)

# ─────────────────────────────────────────────────────────────────────────────
# Test fixtures
# ─────────────────────────────────────────────────────────────────────────────

STUDENT_UUID  = "aaaaaaaa-0000-0000-0000-000000000001"
UNKNOWN_UUID  = "bbbbbbbb-0000-0000-0000-000000000099"

MOCK_PROFILE = {
    "id":                 "cccccccc-0000-0000-0000-000000000001",
    "student_id":         STUDENT_UUID,
    "program_code":       "CS",
    "catalog_version_id": None,
    "declared_at":        None,
    "started_at":         None,
    "source":             "banner_sync",
    "is_active":          True,
    "created_at":         "2026-06-22T00:00:00+00:00",
}

MOCK_PROGRAM_CS = {
    "program_code":              "CS",
    "degree_type":               "bachelor",
    "official_program_name_ar":  "علوم الحاسب",
    "official_program_name_en":  "Computer Science",
    "total_credits_official":    132,
    "total_credits_alt":         None,
    "num_levels":                9,
    "support_level":             "active",
    "program_status":            "ready",
    "college_code":              "CIT",
    "college_name_ar":           "كلية الحاسب والمعلوماتية",
    "college_name_en":           "College of Computing and Informatics",
    "catalog_status":            "validated",
    "version_code":              "official-2026-06",
}

MOCK_PROGRAM_PH = {
    **MOCK_PROGRAM_CS,
    "program_code":             "PH",
    "official_program_name_en": "Public Health",
    "program_status":           "needs_review",
}

MOCK_PROGRAM_DIPLOMA = {
    **MOCK_PROGRAM_CS,
    "program_code": "DIPL001",
    "degree_type":  "diploma",
}


def make_mock(profile_rows, program_rows):
    """Returns an async side_effect that dispatches by table name."""
    async def _mock(db, table, params):
        if table == "student_program_profile":
            return profile_rows
        if table == "v_draft_catalog_programs":
            return program_rows
        return []
    return _mock


# ─────────────────────────────────────────────────────────────────────────────
# GET /v1/progress/profile
# ─────────────────────────────────────────────────────────────────────────────

class TestProgressProfile:

    # ── Happy path ────────────────────────────────────────────────────────────

    def test_200_cs_student(self):
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([MOCK_PROFILE], [MOCK_PROGRAM_CS])
        )):
            r = client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}")
        assert r.status_code == 200

    def test_program_code_in_response(self):
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([MOCK_PROFILE], [MOCK_PROGRAM_CS])
        )):
            b = client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}").json()
        assert b["program_code"] == "CS"

    def test_student_id_in_response(self):
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([MOCK_PROFILE], [MOCK_PROGRAM_CS])
        )):
            b = client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}").json()
        assert b["student_id"] == STUDENT_UUID

    def test_required_fields_present(self):
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([MOCK_PROFILE], [MOCK_PROGRAM_CS])
        )):
            b = client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}").json()
        for field in [
            "student_id", "program_code", "catalog_status", "program_status",
            "support_level", "total_credits_official", "needs_review",
        ]:
            assert field in b, f"missing required field: {field}"

    def test_needs_review_false_for_cs(self):
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([MOCK_PROFILE], [MOCK_PROGRAM_CS])
        )):
            b = client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}").json()
        assert b["needs_review"] is False

    def test_total_credits_official_correct(self):
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([MOCK_PROFILE], [MOCK_PROGRAM_CS])
        )):
            b = client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}").json()
        assert b["total_credits_official"] == 132

    def test_catalog_status_validated(self):
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([MOCK_PROFILE], [MOCK_PROGRAM_CS])
        )):
            b = client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}").json()
        assert b["catalog_status"] == "validated"

    def test_profile_id_in_response(self):
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([MOCK_PROFILE], [MOCK_PROGRAM_CS])
        )):
            b = client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}").json()
        assert b["profile_id"] == MOCK_PROFILE["id"]

    # ── needs_review programs ─────────────────────────────────────────────────

    def test_needs_review_true_for_ph(self):
        ph_profile = {**MOCK_PROFILE, "program_code": "PH"}
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([ph_profile], [MOCK_PROGRAM_PH])
        )):
            b = client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}").json()
        assert b["needs_review"] is True

    def test_ph_returns_200(self):
        ph_profile = {**MOCK_PROFILE, "program_code": "PH"}
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([ph_profile], [MOCK_PROGRAM_PH])
        )):
            r = client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}")
        assert r.status_code == 200

    # ── 404 cases ─────────────────────────────────────────────────────────────

    def test_404_no_active_profile(self):
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([], [])
        )):
            r = client.get(f"/v1/progress/profile?student_id={UNKNOWN_UUID}")
        assert r.status_code == 404

    def test_404_detail_mentions_student(self):
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([], [])
        )):
            b = client.get(f"/v1/progress/profile?student_id={UNKNOWN_UUID}").json()
        assert UNKNOWN_UUID in b["detail"]

    def test_404_law_program_from_profile(self):
        law_profile = {**MOCK_PROFILE, "program_code": "LAW"}
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([law_profile], [])
        )):
            r = client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}")
        assert r.status_code == 404

    def test_404_law_detail_message(self):
        law_profile = {**MOCK_PROFILE, "program_code": "LAW"}
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([law_profile], [])
        )):
            b = client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}").json()
        assert "LAW" in b["detail"]

    def test_404_law_lowercase_also_guarded(self):
        law_profile = {**MOCK_PROFILE, "program_code": "law"}
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([law_profile], [])
        )):
            r = client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}")
        assert r.status_code == 404

    def test_404_diploma_program(self):
        dip_profile = {**MOCK_PROFILE, "program_code": "DIPL001"}
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([dip_profile], [MOCK_PROGRAM_DIPLOMA])
        )):
            r = client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}")
        assert r.status_code == 404

    def test_404_diploma_detail_mentions_degree_type(self):
        dip_profile = {**MOCK_PROFILE, "program_code": "DIPL001"}
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([dip_profile], [MOCK_PROGRAM_DIPLOMA])
        )):
            b = client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}").json()
        assert "diploma" in b["detail"]

    def test_404_program_not_in_catalog(self):
        ghost_profile = {**MOCK_PROFILE, "program_code": "ZZNOTEXIST"}
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([ghost_profile], [])
        )):
            r = client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}")
        assert r.status_code == 404

    # ── 409 case ──────────────────────────────────────────────────────────────

    def test_409_multiple_active_profiles(self):
        second_profile = {**MOCK_PROFILE, "id": "dddddddd-0000-0000-0000-000000000002", "program_code": "IT"}
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([MOCK_PROFILE, second_profile], [MOCK_PROGRAM_CS])
        )):
            r = client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}")
        assert r.status_code == 409

    def test_409_detail_message(self):
        second_profile = {**MOCK_PROFILE, "id": "dddddddd-0000-0000-0000-000000000002", "program_code": "IT"}
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([MOCK_PROFILE, second_profile], [MOCK_PROGRAM_CS])
        )):
            b = client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}").json()
        assert "Multiple active profiles" in b["detail"]

    # ── Validation ────────────────────────────────────────────────────────────

    def test_422_missing_student_id(self):
        r = client.get("/v1/progress/profile")
        assert r.status_code == 422

    # ── Read-only guarantee ───────────────────────────────────────────────────

    def test_only_get_calls_made(self):
        """Endpoint must make exactly 2 reads (profile + catalog) and no writes."""
        calls = []
        async def recording_mock(db, table, params):
            calls.append(table)
            if table == "student_program_profile":
                return [MOCK_PROFILE]
            return [MOCK_PROGRAM_CS]

        with patch("progress_api._get", new=AsyncMock(side_effect=recording_mock)):
            client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}")

        assert calls == ["student_program_profile", "v_draft_catalog_programs"]

    def test_program_code_not_accepted_from_caller(self):
        """program_code in query string must be ignored — profile is the authority."""
        calls = []
        async def recording_mock(db, table, params):
            calls.append({"table": table, "params": params})
            if table == "student_program_profile":
                return [MOCK_PROFILE]
            return [MOCK_PROGRAM_CS]

        with patch("progress_api._get", new=AsyncMock(side_effect=recording_mock)):
            # Caller tries to inject program_code=IT — must be ignored
            client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}&program_code=IT")

        profile_call = next(c for c in calls if c["table"] == "student_program_profile")
        catalog_call = next(c for c in calls if c["table"] == "v_draft_catalog_programs")

        # Catalog must be queried with CS (from profile), not IT (from caller)
        assert "eq.CS" in catalog_call["params"].get("program_code", "")
        # Profile query must not include any program_code filter
        assert "program_code" not in profile_call["params"]
