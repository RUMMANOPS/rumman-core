"""
tests/test_progress_profile.py — Progress API full test suite

Covers all 5 endpoints:
  GET /v1/progress/profile
  GET /v1/progress/completed
  GET /v1/progress/current
  GET /v1/progress/summary
  GET /v1/progress/plan-status

All tests use mocked _get — no DB writes, no DB reads.

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
# Shared test fixtures
# ─────────────────────────────────────────────────────────────────────────────

STUDENT_UUID = "aaaaaaaa-0000-0000-0000-000000000001"
UNKNOWN_UUID = "bbbbbbbb-0000-0000-0000-000000000099"

MOCK_PROFILE = {
    "id":          "cccccccc-0000-0000-0000-000000000001",
    "student_id":  STUDENT_UUID,
    "program_code": "CS",
    "declared_at": None,
    "started_at":  None,
    "source":      "banner_sync",
    "is_active":   True,
    "created_at":  "2026-06-22T00:00:00+00:00",
}

MOCK_PROGRAM_CS = {
    "program_code":             "CS",
    "degree_type":              "bachelor",
    "official_program_name_ar": "علوم الحاسب",
    "official_program_name_en": "Computer Science",
    "total_credits_official":   132,
    "total_credits_alt":        None,
    "num_levels":               9,
    "support_level":            "active",
    "program_status":           "ready",
    "college_code":             "CIT",
    "college_name_ar":          "كلية الحاسب",
    "college_name_en":          "College of Computing",
    "catalog_status":           "validated",
    "version_code":             "official-2026-06",
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

# student_course_history rows (is_counted=true)
MOCK_COMPLETED_ROWS = [
    {
        "id":                    "hh000001",
        "canonical_course_code": "CS101",
        "banner_course_code":    "CS101",
        "course_state":          "passed",
        "term_code":             "202310",
        "source":                "banner_sync",
        "confidence":            "high",
        "verified_by_student":   False,
    },
    {
        "id":                    "hh000002",
        "canonical_course_code": "MATH101",
        "banner_course_code":    "MATH101",
        "course_state":          "transferred",
        "term_code":             "202210",
        "source":                "student_import",
        "confidence":            "medium",
        "verified_by_student":   True,
    },
    {
        "id":                    "hh000003",
        "canonical_course_code": None,           # NULL — unresolved
        "banner_course_code":    "UNKN999",
        "course_state":          "passed",
        "term_code":             "202110",
        "source":                "banner_sync",
        "confidence":            "low",
        "verified_by_student":   False,
    },
]

# v_draft_catalog_program_courses rows (credit_hours per program)
MOCK_CATALOG_CREDIT_ROWS = [
    {
        "canonical_course_code": "CS101",
        "credit_hours":          3,
        "level":                 1,
        "category":              "core",
        "is_required":           True,
        "is_elective":           False,
        "official_title_ar":     "مبادئ البرمجة",
        "official_title_en":     "Programming Fundamentals",
    },
    {
        "canonical_course_code": "MATH101",
        "credit_hours":          4,
        "level":                 1,
        "category":              "support",
        "is_required":           True,
        "is_elective":           False,
        "official_title_ar":     "حساب التفاضل",
        "official_title_en":     "Calculus I",
    },
]

# student_registered_sections rows.
# Two active (one with NULL canonical) + one dropped that MUST be filtered out
# by the status IN ('active','approved') rule (decision §6 Issue 1).
MOCK_SECTIONS = [
    {
        "id":                    "ss000001",
        "term_code":             "202420",
        "crn":                   "12345",
        "banner_course_code":    "CS201",
        "canonical_course_code": "CS201",
        "course_name":           "Data Structures",
        "status":                "active",
        "source":                "smart_registration",
        "delivery_mode":         "online",
        "created_at":            "2026-06-01T00:00:00+00:00",
    },
    {
        "id":                    "ss000002",
        "term_code":             "202420",
        "crn":                   "12346",
        "banner_course_code":    "XUNKNOWN",
        "canonical_course_code": None,           # NULL — unresolved
        "course_name":           "Unknown Course",
        "status":                "active",
        "source":                "banner_sync",
        "delivery_mode":         None,
        "created_at":            "2026-06-01T00:00:00+00:00",
    },
    {
        "id":                    "ss000003",
        "term_code":             "202420",
        "crn":                   "12347",
        "banner_course_code":    "STAT199",
        "canonical_course_code": "STAT199",
        "course_name":           "Dropped Course",
        "status":                "dropped",        # MUST be filtered out
        "source":                "smart_registration",
        "delivery_mode":         None,
        "created_at":            "2026-06-01T00:00:00+00:00",
    },
]

# catalog row for CS201 (used in /current enrichment)
MOCK_CATALOG_CS201 = [
    {
        "canonical_course_code": "CS201",
        "credit_hours":          3,
        "level":                 2,
        "category":              "core",
        "is_required":           True,
        "is_elective":           False,
        "official_title_ar":     "هياكل البيانات",
        "official_title_en":     "Data Structures",
    },
]

# Full plan rows for /plan-status
MOCK_PLAN_ROWS = [
    {
        "canonical_course_code":  "CS101",
        "official_course_code_raw": "CS101",
        "normalized_course_code": "CS101",
        "official_title_ar":      "مبادئ البرمجة",
        "official_title_en":      "Programming Fundamentals",
        "level":                  1,
        "credit_hours":           3,
        "category":               "core",
        "is_required":            True,
        "is_elective":            False,
        "elective_group":         None,
        "track":                  None,
    },
    {
        "canonical_course_code":  "MATH101",
        "official_course_code_raw": "MATH101",
        "normalized_course_code": "MATH101",
        "official_title_ar":      "حساب التفاضل",
        "official_title_en":      "Calculus I",
        "level":                  1,
        "credit_hours":           4,
        "category":               "support",
        "is_required":            True,
        "is_elective":            False,
        "elective_group":         None,
        "track":                  None,
    },
    {
        "canonical_course_code":  "CS201",
        "official_course_code_raw": "CS201",
        "normalized_course_code": "CS201",
        "official_title_ar":      "هياكل البيانات",
        "official_title_en":      "Data Structures",
        "level":                  2,
        "credit_hours":           3,
        "category":               "core",
        "is_required":            True,
        "is_elective":            False,
        "elective_group":         None,
        "track":                  None,
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Mock dispatcher factory
# ─────────────────────────────────────────────────────────────────────────────

def make_mock(
    profile_rows=None,
    program_rows=None,
    history_rows=None,
    catalog_enrich_rows=None,
    section_rows=None,
    plan_rows=None,
):
    """
    Dispatches _get calls by table name.
    v_draft_catalog_program_courses:
      - If params include 'canonical_course_code' → enrichment query → catalog_enrich_rows
      - Otherwise → full plan query → plan_rows
    """
    async def _mock(db, table, params):
        if table == "student_program_profile":
            return profile_rows or []
        if table == "v_draft_catalog_programs":
            return program_rows or []
        if table == "student_course_history":
            return history_rows or []
        if table == "v_draft_catalog_program_courses":
            if "canonical_course_code" in params:
                return catalog_enrich_rows or []
            return plan_rows or []
        if table == "student_registered_sections":
            rows = section_rows or []
            # Faithfully simulate the DB-side status filter the endpoint applies.
            status_filter = params.get("status")
            if status_filter and status_filter.startswith("in.("):
                allowed = status_filter[len("in.("):-1].split(",")
                rows = [r for r in rows if r.get("status") in allowed]
            return rows
        return []
    return _mock


# ─────────────────────────────────────────────────────────────────────────────
# /v1/progress/profile
# ─────────────────────────────────────────────────────────────────────────────

class TestProgressProfile:

    def test_200_cs_student(self):
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([MOCK_PROFILE], [MOCK_PROGRAM_CS])
        )):
            r = client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}")
        assert r.status_code == 200

    def test_required_fields_present(self):
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([MOCK_PROFILE], [MOCK_PROGRAM_CS])
        )):
            b = client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}").json()
        for f in ["student_id","program_code","catalog_status","program_status",
                  "support_level","total_credits_official","needs_review"]:
            assert f in b, f"missing: {f}"

    def test_needs_review_false_cs(self):
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([MOCK_PROFILE], [MOCK_PROGRAM_CS])
        )):
            b = client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}").json()
        assert b["needs_review"] is False

    def test_needs_review_true_ph(self):
        ph = {**MOCK_PROFILE, "program_code": "PH"}
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([ph], [MOCK_PROGRAM_PH])
        )):
            b = client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}").json()
        assert b["needs_review"] is True

    def test_404_no_profile(self):
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock())):
            assert client.get(f"/v1/progress/profile?student_id={UNKNOWN_UUID}").status_code == 404

    def test_409_duplicate_active(self):
        dup = {**MOCK_PROFILE, "id": "dup-id", "program_code": "IT"}
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([MOCK_PROFILE, dup], [MOCK_PROGRAM_CS])
        )):
            assert client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}").status_code == 409

    def test_404_law(self):
        law = {**MOCK_PROFILE, "program_code": "LAW"}
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock([law]))):
            assert client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}").status_code == 404

    def test_404_law_lowercase(self):
        law = {**MOCK_PROFILE, "program_code": "law"}
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock([law]))):
            assert client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}").status_code == 404

    def test_404_diploma(self):
        dip = {**MOCK_PROFILE, "program_code": "DIPL001"}
        with patch("progress_api._get", new=AsyncMock(
            side_effect=make_mock([dip], [MOCK_PROGRAM_DIPLOMA])
        )):
            assert client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}").status_code == 404

    def test_422_missing_student_id(self):
        assert client.get("/v1/progress/profile").status_code == 422

    def test_program_code_not_accepted_from_caller(self):
        calls = []
        async def recording(db, table, params):
            calls.append({"table": table, "params": params})
            if table == "student_program_profile": return [MOCK_PROFILE]
            return [MOCK_PROGRAM_CS]
        with patch("progress_api._get", new=AsyncMock(side_effect=recording)):
            client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}&program_code=IT")
        catalog_call = next(c for c in calls if c["table"] == "v_draft_catalog_programs")
        assert "eq.CS" in catalog_call["params"].get("program_code", "")

    def test_no_db_writes(self):
        calls = []
        async def recording(db, table, params):
            calls.append(table)
            if table == "student_program_profile": return [MOCK_PROFILE]
            return [MOCK_PROGRAM_CS]
        with patch("progress_api._get", new=AsyncMock(side_effect=recording)):
            client.get(f"/v1/progress/profile?student_id={STUDENT_UUID}")
        assert calls == ["student_program_profile", "v_draft_catalog_programs"]


# ─────────────────────────────────────────────────────────────────────────────
# /v1/progress/completed
# ─────────────────────────────────────────────────────────────────────────────

class TestProgressCompleted:

    def test_200(self):
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=MOCK_COMPLETED_ROWS,
            catalog_enrich_rows=MOCK_CATALOG_CREDIT_ROWS,
        ))):
            assert client.get(f"/v1/progress/completed?student_id={STUDENT_UUID}").status_code == 200

    def test_404_no_profile(self):
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock())):
            assert client.get(f"/v1/progress/completed?student_id={UNKNOWN_UUID}").status_code == 404

    def test_409_duplicate_active(self):
        dup = {**MOCK_PROFILE, "id": "dup", "program_code": "IT"}
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE, dup], [MOCK_PROGRAM_CS]
        ))):
            assert client.get(f"/v1/progress/completed?student_id={STUDENT_UUID}").status_code == 409

    def test_credit_hours_from_catalog_not_banner(self):
        """CS101 = 3cr from catalog; any Banner value is ignored."""
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=MOCK_COMPLETED_ROWS,
            catalog_enrich_rows=MOCK_CATALOG_CREDIT_ROWS,
        ))):
            b = client.get(f"/v1/progress/completed?student_id={STUDENT_UUID}").json()
        cs101 = next(c for c in b["courses"] if c["canonical_course_code"] == "CS101")
        assert cs101["credit_hours"] == 3
        assert cs101["catalog_match"] is True

    def test_completed_credits_sum_from_catalog(self):
        """3 (CS101) + 4 (MATH101) = 7; UNKN999 (null canonical) excluded."""
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=MOCK_COMPLETED_ROWS,
            catalog_enrich_rows=MOCK_CATALOG_CREDIT_ROWS,
        ))):
            b = client.get(f"/v1/progress/completed?student_id={STUDENT_UUID}").json()
        assert b["completed_credits"] == 7

    def test_shared_course_credit_per_program(self):
        """ENG001 = 8cr in CS, 16cr in IT — catalog_enrich returns program-specific value."""
        eng_history = [{
            "id": "hh999", "canonical_course_code": "ENG001",
            "banner_course_code": "ENG001", "course_state": "passed",
            "term_code": "202310", "source": "banner_sync",
            "confidence": "high", "verified_by_student": False,
        }]
        eng_catalog_cs = [{"canonical_course_code": "ENG001", "credit_hours": 8,
                           "level": 1, "category": "core", "is_required": True,
                           "is_elective": False, "official_title_ar": "انجليزي",
                           "official_title_en": "English"}]
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=eng_history, catalog_enrich_rows=eng_catalog_cs,
        ))):
            b = client.get(f"/v1/progress/completed?student_id={STUDENT_UUID}").json()
        eng = b["courses"][0]
        assert eng["credit_hours"] == 8
        assert b["completed_credits"] == 8

    def test_null_canonical_catalog_match_false(self):
        """UNKN999 has canonical_course_code=NULL → catalog_match=False, credit_hours=None."""
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=MOCK_COMPLETED_ROWS,
            catalog_enrich_rows=MOCK_CATALOG_CREDIT_ROWS,
        ))):
            b = client.get(f"/v1/progress/completed?student_id={STUDENT_UUID}").json()
        unkn = next(c for c in b["courses"] if c["banner_course_code"] == "UNKN999")
        assert unkn["catalog_match"] is False
        assert unkn["credit_hours"] is None

    def test_null_canonical_generates_warning(self):
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=MOCK_COMPLETED_ROWS,
            catalog_enrich_rows=MOCK_CATALOG_CREDIT_ROWS,
        ))):
            b = client.get(f"/v1/progress/completed?student_id={STUDENT_UUID}").json()
        warning_codes = [w["code"] for w in b["warnings"]]
        assert "canonical_code_missing" in warning_codes

    def test_no_grades_in_response(self):
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=MOCK_COMPLETED_ROWS,
            catalog_enrich_rows=MOCK_CATALOG_CREDIT_ROWS,
        ))):
            b = client.get(f"/v1/progress/completed?student_id={STUDENT_UUID}").json()
        response_str = str(b)
        for forbidden in ["grade", "gpa", "percentage", "percent", "score", "mark"]:
            assert forbidden not in response_str.lower(), f"forbidden field found: {forbidden}"

    def test_needs_review_warning_for_ph(self):
        ph = {**MOCK_PROFILE, "program_code": "PH"}
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [ph], [MOCK_PROGRAM_PH],
            history_rows=[], catalog_enrich_rows=[],
        ))):
            b = client.get(f"/v1/progress/completed?student_id={STUDENT_UUID}").json()
        assert any(w["code"] == "needs_review_program" for w in b["warnings"])

    def test_empty_history_returns_zero_credits(self):
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS], history_rows=[], catalog_enrich_rows=[],
        ))):
            b = client.get(f"/v1/progress/completed?student_id={STUDENT_UUID}").json()
        assert b["completed_credits"] == 0
        assert b["count"] == 0

    def test_404_law(self):
        law = {**MOCK_PROFILE, "program_code": "LAW"}
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock([law]))):
            assert client.get(f"/v1/progress/completed?student_id={STUDENT_UUID}").status_code == 404

    def test_404_diploma(self):
        dip = {**MOCK_PROFILE, "program_code": "DIPL001"}
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [dip], [MOCK_PROGRAM_DIPLOMA]
        ))):
            assert client.get(f"/v1/progress/completed?student_id={STUDENT_UUID}").status_code == 404

    def test_lowercase_profile_code_uses_catalog_canonical_casing(self):
        """
        Regression (red-team): a profile storing 'cs' (lowercase) must still
        resolve credits. The credit-map query must use the catalog's canonical
        'CS' — not the profile's raw 'cs' (which returns empty in PostgREST).
        """
        lc_profile = {**MOCK_PROFILE, "program_code": "cs"}
        seen = {}
        async def recording(db, table, params):
            if table == "student_program_profile":
                return [lc_profile]
            if table == "v_draft_catalog_programs":
                return [MOCK_PROGRAM_CS]          # catalog row has program_code='CS'
            if table == "student_course_history":
                return MOCK_COMPLETED_ROWS
            if table == "v_draft_catalog_program_courses":
                seen["program_code"] = params.get("program_code")
                return MOCK_CATALOG_CREDIT_ROWS
            return []
        with patch("progress_api._get", new=AsyncMock(side_effect=recording)):
            b = client.get(f"/v1/progress/completed?student_id={STUDENT_UUID}").json()
        assert seen["program_code"] == "eq.CS"     # canonical casing, not 'eq.cs'
        assert b["completed_credits"] == 7         # credits resolved despite lowercase profile


# ─────────────────────────────────────────────────────────────────────────────
# /v1/progress/current
# ─────────────────────────────────────────────────────────────────────────────

class TestProgressCurrent:

    def test_200(self):
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            section_rows=MOCK_SECTIONS,
            catalog_enrich_rows=MOCK_CATALOG_CS201,
        ))):
            assert client.get(f"/v1/progress/current?student_id={STUDENT_UUID}").status_code == 200

    def test_404_no_profile(self):
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock())):
            assert client.get(f"/v1/progress/current?student_id={UNKNOWN_UUID}").status_code == 404

    def test_409_duplicate_active(self):
        dup = {**MOCK_PROFILE, "id": "dup", "program_code": "IT"}
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE, dup], [MOCK_PROGRAM_CS]
        ))):
            assert client.get(f"/v1/progress/current?student_id={STUDENT_UUID}").status_code == 409

    def test_cs201_catalog_matched(self):
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            section_rows=MOCK_SECTIONS,
            catalog_enrich_rows=MOCK_CATALOG_CS201,
        ))):
            b = client.get(f"/v1/progress/current?student_id={STUDENT_UUID}").json()
        cs201 = next(c for c in b["courses"] if c.get("canonical_course_code") == "CS201")
        assert cs201["catalog_match"] is True
        assert cs201["credit_hours"] == 3

    def test_null_canonical_handled_gracefully(self):
        """XUNKNOWN has canonical_course_code=NULL — must not raise, must return catalog_match=False."""
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            section_rows=MOCK_SECTIONS,
            catalog_enrich_rows=MOCK_CATALOG_CS201,
        ))):
            b = client.get(f"/v1/progress/current?student_id={STUDENT_UUID}").json()
        unmatched = next(c for c in b["courses"] if c["banner_course_code"] == "XUNKNOWN")
        assert unmatched["catalog_match"] is False
        assert unmatched["credit_hours"] is None

    def test_null_canonical_warning_emitted(self):
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            section_rows=MOCK_SECTIONS,
            catalog_enrich_rows=MOCK_CATALOG_CS201,
        ))):
            b = client.get(f"/v1/progress/current?student_id={STUDENT_UUID}").json()
        assert any(w["code"] == "canonical_code_missing" for w in b["warnings"])

    def test_empty_sections_returns_empty_list(self):
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS], section_rows=[], catalog_enrich_rows=[],
        ))):
            b = client.get(f"/v1/progress/current?student_id={STUDENT_UUID}").json()
        assert b["count"] == 0
        assert b["courses"] == []

    def test_dropped_section_excluded(self):
        """STAT199 (status=dropped) must NOT appear; only active rows count (§6 Issue 1)."""
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            section_rows=MOCK_SECTIONS,
            catalog_enrich_rows=MOCK_CATALOG_CS201,
        ))):
            b = client.get(f"/v1/progress/current?student_id={STUDENT_UUID}").json()
        banners = [c["banner_course_code"] for c in b["courses"]]
        assert "STAT199" not in banners
        assert b["count"] == 2  # CS201 + XUNKNOWN, dropped excluded

    def test_status_filter_sent_to_db(self):
        """The section query must carry status=in.(active,approved)."""
        seen = {}
        async def recording(db, table, params):
            if table == "student_program_profile": return [MOCK_PROFILE]
            if table == "v_draft_catalog_programs": return [MOCK_PROGRAM_CS]
            if table == "student_registered_sections":
                seen["status"] = params.get("status")
                return []
            return []
        with patch("progress_api._get", new=AsyncMock(side_effect=recording)):
            client.get(f"/v1/progress/current?student_id={STUDENT_UUID}")
        assert seen.get("status") == "in.(active,approved)"

    def test_no_grades_in_response(self):
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            section_rows=MOCK_SECTIONS,
            catalog_enrich_rows=MOCK_CATALOG_CS201,
        ))):
            b = client.get(f"/v1/progress/current?student_id={STUDENT_UUID}").json()
        response_str = str(b)
        for forbidden in ["grade", "gpa", "percentage"]:
            assert forbidden not in response_str.lower()

    def test_422_missing_student_id(self):
        assert client.get("/v1/progress/current").status_code == 422

    def test_404_law(self):
        law = {**MOCK_PROFILE, "program_code": "LAW"}
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock([law]))):
            assert client.get(f"/v1/progress/current?student_id={STUDENT_UUID}").status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# /v1/progress/summary
# ─────────────────────────────────────────────────────────────────────────────

class TestProgressSummary:

    def test_200(self):
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=MOCK_COMPLETED_ROWS,
            catalog_enrich_rows=MOCK_CATALOG_CREDIT_ROWS,
            section_rows=MOCK_SECTIONS,
        ))):
            assert client.get(f"/v1/progress/summary?student_id={STUDENT_UUID}").status_code == 200

    def test_required_fields(self):
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=MOCK_COMPLETED_ROWS,
            catalog_enrich_rows=MOCK_CATALOG_CREDIT_ROWS,
            section_rows=MOCK_SECTIONS,
        ))):
            b = client.get(f"/v1/progress/summary?student_id={STUDENT_UUID}").json()
        for f in ["program_code","total_credits_official","completed_credits",
                  "remaining_credits","completed_courses_count","current_courses_count",
                  "needs_review","warnings"]:
            assert f in b, f"missing: {f}"

    def test_completed_credits_from_catalog(self):
        """3 (CS101) + 4 (MATH101) = 7; UNKN999 null-canonical excluded."""
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=MOCK_COMPLETED_ROWS,
            catalog_enrich_rows=MOCK_CATALOG_CREDIT_ROWS,
            section_rows=[],
        ))):
            b = client.get(f"/v1/progress/summary?student_id={STUDENT_UUID}").json()
        assert b["completed_credits"] == 7

    def test_remaining_credits_computed_correctly(self):
        """132 - 7 = 125."""
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=MOCK_COMPLETED_ROWS,
            catalog_enrich_rows=MOCK_CATALOG_CREDIT_ROWS,
            section_rows=[],
        ))):
            b = client.get(f"/v1/progress/summary?student_id={STUDENT_UUID}").json()
        assert b["remaining_credits"] == 125

    def test_completed_courses_count(self):
        """3 rows total (including one null-canonical)."""
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=MOCK_COMPLETED_ROWS,
            catalog_enrich_rows=MOCK_CATALOG_CREDIT_ROWS,
            section_rows=[],
        ))):
            b = client.get(f"/v1/progress/summary?student_id={STUDENT_UUID}").json()
        assert b["completed_courses_count"] == 3

    def test_current_courses_count(self):
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=[], catalog_enrich_rows=[],
            section_rows=MOCK_SECTIONS,
        ))):
            b = client.get(f"/v1/progress/summary?student_id={STUDENT_UUID}").json()
        assert b["current_courses_count"] == 2

    def test_null_canonical_warning_in_summary(self):
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=MOCK_COMPLETED_ROWS,
            catalog_enrich_rows=MOCK_CATALOG_CREDIT_ROWS,
            section_rows=[],
        ))):
            b = client.get(f"/v1/progress/summary?student_id={STUDENT_UUID}").json()
        assert any(w["code"] == "canonical_code_missing" for w in b["warnings"])

    def test_needs_review_true_ph(self):
        ph = {**MOCK_PROFILE, "program_code": "PH"}
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [ph], [MOCK_PROGRAM_PH],
            history_rows=[], catalog_enrich_rows=[], section_rows=[],
        ))):
            b = client.get(f"/v1/progress/summary?student_id={STUDENT_UUID}").json()
        assert b["needs_review"] is True
        assert any(w["code"] == "needs_review_program" for w in b["warnings"])

    def test_no_grades_in_response(self):
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=MOCK_COMPLETED_ROWS,
            catalog_enrich_rows=MOCK_CATALOG_CREDIT_ROWS,
            section_rows=[],
        ))):
            b = client.get(f"/v1/progress/summary?student_id={STUDENT_UUID}").json()
        for f in ["grade", "gpa", "percentage", "score"]:
            assert f not in str(b).lower()

    def test_404_no_profile(self):
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock())):
            assert client.get(f"/v1/progress/summary?student_id={UNKNOWN_UUID}").status_code == 404

    def test_409_duplicate_active(self):
        dup = {**MOCK_PROFILE, "id": "dup", "program_code": "IT"}
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE, dup], [MOCK_PROGRAM_CS]
        ))):
            assert client.get(f"/v1/progress/summary?student_id={STUDENT_UUID}").status_code == 409

    def test_404_law(self):
        law = {**MOCK_PROFILE, "program_code": "LAW"}
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock([law]))):
            assert client.get(f"/v1/progress/summary?student_id={STUDENT_UUID}").status_code == 404

    def test_422_missing_student_id(self):
        assert client.get("/v1/progress/summary").status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# /v1/progress/plan-status
# ─────────────────────────────────────────────────────────────────────────────

class TestProgressPlanStatus:

    def test_200(self):
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=MOCK_COMPLETED_ROWS,
            section_rows=MOCK_SECTIONS,
            plan_rows=MOCK_PLAN_ROWS,
        ))):
            assert client.get(f"/v1/progress/plan-status?student_id={STUDENT_UUID}").status_code == 200

    def test_levels_present(self):
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=MOCK_COMPLETED_ROWS,
            section_rows=MOCK_SECTIONS,
            plan_rows=MOCK_PLAN_ROWS,
        ))):
            b = client.get(f"/v1/progress/plan-status?student_id={STUDENT_UUID}").json()
        assert len(b["levels"]) == 2  # level 1 and level 2

    def test_completed_status_overlay(self):
        """CS101 and MATH101 are in MOCK_COMPLETED_ROWS → status=completed."""
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=MOCK_COMPLETED_ROWS,
            section_rows=[],
            plan_rows=MOCK_PLAN_ROWS,
        ))):
            b = client.get(f"/v1/progress/plan-status?student_id={STUDENT_UUID}").json()
        lvl1 = next(l for l in b["levels"] if l["level"] == 1)
        statuses = {c["canonical_course_code"]: c["status"] for c in lvl1["courses"]}
        assert statuses["CS101"] == "completed"
        assert statuses["MATH101"] == "completed"

    def test_in_progress_status_overlay(self):
        """CS201 is in MOCK_SECTIONS → status=in_progress."""
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=[],
            section_rows=MOCK_SECTIONS,
            plan_rows=MOCK_PLAN_ROWS,
        ))):
            b = client.get(f"/v1/progress/plan-status?student_id={STUDENT_UUID}").json()
        lvl2 = next(l for l in b["levels"] if l["level"] == 2)
        cs201 = next(c for c in lvl2["courses"] if c["canonical_course_code"] == "CS201")
        assert cs201["status"] == "in_progress"

    def test_not_started_for_unmatched(self):
        """CS201 with no history and no section → status=not_started."""
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=[], section_rows=[],
            plan_rows=MOCK_PLAN_ROWS,
        ))):
            b = client.get(f"/v1/progress/plan-status?student_id={STUDENT_UUID}").json()
        all_statuses = [
            c["status"] for l in b["levels"] for c in l["courses"]
        ]
        assert all(s == "not_started" for s in all_statuses)

    def test_orphaned_completed_in_response(self):
        """UNKN999 has null canonical → appears in orphaned_completed, not on plan."""
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=MOCK_COMPLETED_ROWS,
            section_rows=[],
            plan_rows=MOCK_PLAN_ROWS,
        ))):
            b = client.get(f"/v1/progress/plan-status?student_id={STUDENT_UUID}").json()
        orphaned_banners = [o["banner_course_code"] for o in b["orphaned_completed"]]
        assert "UNKN999" in orphaned_banners

    def test_orphaned_status_blocked_unknown(self):
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=MOCK_COMPLETED_ROWS,
            section_rows=[],
            plan_rows=MOCK_PLAN_ROWS,
        ))):
            b = client.get(f"/v1/progress/plan-status?student_id={STUDENT_UUID}").json()
        for o in b["orphaned_completed"]:
            assert o["status"] == "blocked_unknown"

    def test_credit_hours_from_catalog(self):
        """credit_hours in plan comes from catalog, not from Banner."""
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=[], section_rows=[],
            plan_rows=MOCK_PLAN_ROWS,
        ))):
            b = client.get(f"/v1/progress/plan-status?student_id={STUDENT_UUID}").json()
        lvl1 = next(l for l in b["levels"] if l["level"] == 1)
        cs101 = next(c for c in lvl1["courses"] if c["canonical_course_code"] == "CS101")
        assert cs101["credit_hours"] == 3  # from MOCK_PLAN_ROWS, catalog-authoritative

    def test_prereq_scaffold_warning_present(self):
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=[], section_rows=[],
            plan_rows=MOCK_PLAN_ROWS,
        ))):
            b = client.get(f"/v1/progress/plan-status?student_id={STUDENT_UUID}").json()
        assert any(w["code"] == "prereq_check_not_implemented" for w in b["warnings"])

    def test_no_grades_in_response(self):
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE], [MOCK_PROGRAM_CS],
            history_rows=MOCK_COMPLETED_ROWS,
            section_rows=MOCK_SECTIONS,
            plan_rows=MOCK_PLAN_ROWS,
        ))):
            b = client.get(f"/v1/progress/plan-status?student_id={STUDENT_UUID}").json()
        for f in ["grade", "gpa", "percentage", "score"]:
            assert f not in str(b).lower()

    def test_404_no_profile(self):
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock())):
            assert client.get(f"/v1/progress/plan-status?student_id={UNKNOWN_UUID}").status_code == 404

    def test_409_duplicate_active(self):
        dup = {**MOCK_PROFILE, "id": "dup", "program_code": "IT"}
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [MOCK_PROFILE, dup], [MOCK_PROGRAM_CS]
        ))):
            assert client.get(f"/v1/progress/plan-status?student_id={STUDENT_UUID}").status_code == 409

    def test_404_law(self):
        law = {**MOCK_PROFILE, "program_code": "LAW"}
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock([law]))):
            assert client.get(f"/v1/progress/plan-status?student_id={STUDENT_UUID}").status_code == 404

    def test_404_diploma(self):
        dip = {**MOCK_PROFILE, "program_code": "DIPL001"}
        with patch("progress_api._get", new=AsyncMock(side_effect=make_mock(
            [dip], [MOCK_PROGRAM_DIPLOMA]
        ))):
            assert client.get(f"/v1/progress/plan-status?student_id={STUDENT_UUID}").status_code == 404

    def test_422_missing_student_id(self):
        assert client.get("/v1/progress/plan-status").status_code == 422
