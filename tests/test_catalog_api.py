"""
tests/test_catalog_api.py — Catalog API hardening tests

Covers all 8 endpoints:
  GET /v1/catalog/version
  GET /v1/catalog/programs
  GET /v1/catalog/programs/{program_code}
  GET /v1/catalog/programs/{program_code}/courses
  GET /v1/catalog/programs/{program_code}/plan
  GET /v1/catalog/programs/{program_code}/prerequisites
  GET /v1/catalog/courses/{course_code}/programs
  GET /v1/catalog/aliases/{alias_label}

Run:
  SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python3 -m pytest tests/test_catalog_api.py -v
"""
import os
import sys
import pytest

# Allow running from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

os.environ.setdefault("SUPABASE_URL", "https://yriavgczteuirigsvedu.supabase.co")
os.environ.setdefault(
    "SUPABASE_SERVICE_ROLE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlyaWF2Z2N6dGV1aXJpZ3N2ZWR1Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3OTU5NDkyOSwiZXhwIjoyMDk1MTcwOTI5fQ.GTAxJc6mpMfe13x_d_QtjiU3QOsdt4NPkFUc_nG6BNg",
)

import catalog_api  # noqa: E402  (after path setup)
from fastapi import FastAPI
from fastapi.testclient import TestClient

_app = FastAPI()
_app.include_router(catalog_api.router)
client = TestClient(_app, raise_server_exceptions=True)


# ─────────────────────────────────────────────────────────────────────────────
# GET /v1/catalog/version
# ─────────────────────────────────────────────────────────────────────────────

class TestVersion:
    def test_200(self):
        r = client.get("/v1/catalog/version")
        assert r.status_code == 200

    def test_version_code_present(self):
        r = client.get("/v1/catalog/version")
        assert r.json()["version_code"] == "official-2026-06"

    def test_status_is_validated_or_active(self):
        r = client.get("/v1/catalog/version")
        b = r.json()
        # After 074 the status is 'validated'; after activation it becomes 'active'
        assert b["status"] in ("validated", "active")

    def test_is_serving_true(self):
        r = client.get("/v1/catalog/version")
        assert r.json()["is_serving"] is True

    def test_is_active_false_pre_activation(self):
        r = client.get("/v1/catalog/version")
        b = r.json()
        # Will flip to True after activation — for now asserts no accidental activation
        if b["status"] == "validated":
            assert b["is_active"] is False

    def test_is_validated_field_present(self):
        r = client.get("/v1/catalog/version")
        assert "is_validated" in r.json()

    def test_no_active_release(self):
        r = client.get("/v1/catalog/version")
        b = r.json()
        # Either validated (pre-activation) or active — never draft after 074
        assert b["status"] != "draft"


# ─────────────────────────────────────────────────────────────────────────────
# GET /v1/catalog/programs
# ─────────────────────────────────────────────────────────────────────────────

class TestListPrograms:
    def test_200(self):
        assert client.get("/v1/catalog/programs").status_code == 200

    def test_count_19(self):
        r = client.get("/v1/catalog/programs")
        assert r.json()["count"] == 19

    def test_law_absent(self):
        r = client.get("/v1/catalog/programs")
        codes = [p["program_code"] for p in r.json()["programs"]]
        assert "LAW" not in codes

    def test_no_diplomas(self):
        r = client.get("/v1/catalog/programs")
        types = [p["degree_type"] for p in r.json()["programs"]]
        assert "diploma" not in types

    def test_ph_mdm_fin_present(self):
        r = client.get("/v1/catalog/programs")
        codes = {p["program_code"] for p in r.json()["programs"]}
        assert {"PH", "MDM", "FIN"}.issubset(codes)

    def test_ph_mdm_fin_needs_review(self):
        r = client.get("/v1/catalog/programs")
        nr = {p["program_code"] for p in r.json()["programs"] if p["needs_review"]}
        assert nr == {"PH", "MDM", "FIN"}

    def test_degree_type_filter_bachelor(self):
        r = client.get("/v1/catalog/programs?degree_type=bachelor")
        assert r.status_code == 200
        progs = r.json()["programs"]
        assert len(progs) == 11
        assert all(p["degree_type"] == "bachelor" for p in progs)

    def test_degree_type_filter_master(self):
        r = client.get("/v1/catalog/programs?degree_type=master")
        progs = r.json()["programs"]
        assert len(progs) == 6
        assert all(p["degree_type"] == "master" for p in progs)

    def test_degree_type_filter_executive_master(self):
        r = client.get("/v1/catalog/programs?degree_type=executive_master")
        progs = r.json()["programs"]
        assert len(progs) == 2
        assert all(p["degree_type"] == "executive_master" for p in progs)


# ─────────────────────────────────────────────────────────────────────────────
# GET /v1/catalog/programs/{program_code}
# ─────────────────────────────────────────────────────────────────────────────

class TestGetProgram:
    def test_cs_200(self):
        assert client.get("/v1/catalog/programs/CS").status_code == 200

    def test_cs_fields(self):
        b = client.get("/v1/catalog/programs/CS").json()
        assert b["program_code"] == "CS"
        assert b["degree_type"] == "bachelor"
        assert b["needs_review"] is False
        assert b["course_count"] > 0
        assert b["required_count"] > 0

    def test_law_404(self):
        assert client.get("/v1/catalog/programs/LAW").status_code == 404

    def test_law_lowercase_404(self):
        assert client.get("/v1/catalog/programs/law").status_code == 404

    def test_nonexistent_404(self):
        assert client.get("/v1/catalog/programs/ZZNOTEXIST").status_code == 404

    def test_ph_needs_review_true(self):
        b = client.get("/v1/catalog/programs/PH").json()
        assert b["needs_review"] is True

    def test_mdm_needs_review_true(self):
        b = client.get("/v1/catalog/programs/MDM").json()
        assert b["needs_review"] is True

    def test_fin_needs_review_true(self):
        b = client.get("/v1/catalog/programs/FIN").json()
        assert b["needs_review"] is True

    def test_cs_total_credits(self):
        b = client.get("/v1/catalog/programs/CS").json()
        assert b.get("total_credits_official") is not None
        assert b["total_credits_official"] > 0


# ─────────────────────────────────────────────────────────────────────────────
# GET /v1/catalog/programs/{program_code}/courses
# ─────────────────────────────────────────────────────────────────────────────

class TestProgramCourses:
    def test_cs_200(self):
        assert client.get("/v1/catalog/programs/CS/courses").status_code == 200

    def test_cs_count(self):
        b = client.get("/v1/catalog/programs/CS/courses").json()
        assert b["count"] == 46

    def test_all_have_program_code(self):
        b = client.get("/v1/catalog/programs/CS/courses").json()
        assert all(c["program_code"] == "CS" for c in b["courses"])

    def test_credit_hours_from_program_context(self):
        b = client.get("/v1/catalog/programs/CS/courses").json()
        assert all(c["credit_hours"] is not None for c in b["courses"])

    def test_eng001_cs_8cr(self):
        b = client.get("/v1/catalog/programs/CS/courses").json()
        eng = next((c for c in b["courses"] if c["canonical_course_code"] == "ENG001"), None)
        assert eng is not None, "ENG001 not found in CS courses"
        assert eng["credit_hours"] == 8

    def test_all_three_code_forms(self):
        b = client.get("/v1/catalog/programs/CS/courses").json()
        for c in b["courses"]:
            assert c.get("canonical_course_code")
            assert c.get("official_course_code_raw")
            assert c.get("normalized_course_code")

    def test_law_404(self):
        assert client.get("/v1/catalog/programs/LAW/courses").status_code == 404

    def test_nonexistent_program_404(self):
        assert client.get("/v1/catalog/programs/ZZNOTEXIST/courses").status_code == 404

    def test_level_filter(self):
        r = client.get("/v1/catalog/programs/CS/courses?level=1")
        assert r.status_code == 200
        b = r.json()
        assert all(c["level"] == 1 for c in b["courses"])

    def test_required_only_filter(self):
        r = client.get("/v1/catalog/programs/CS/courses?required_only=true")
        assert r.status_code == 200
        b = r.json()
        assert all(c["is_required"] for c in b["courses"])


# ─────────────────────────────────────────────────────────────────────────────
# GET /v1/catalog/programs/{program_code}/plan
# ─────────────────────────────────────────────────────────────────────────────

class TestProgramPlan:
    def test_cs_200(self):
        assert client.get("/v1/catalog/programs/CS/plan").status_code == 200

    def test_cs_levels_nonempty(self):
        b = client.get("/v1/catalog/programs/CS/plan").json()
        assert len(b["levels"]) > 0

    def test_cs_required_credits_positive(self):
        b = client.get("/v1/catalog/programs/CS/plan").json()
        assert b["required_credits_sum"] > 0

    def test_cs_needs_review_false(self):
        b = client.get("/v1/catalog/programs/CS/plan").json()
        assert b["needs_review"] is False

    def test_ph_needs_review_true(self):
        b = client.get("/v1/catalog/programs/PH/plan").json()
        assert b["needs_review"] is True

    def test_credit_hours_per_program_in_plan(self):
        b_cs = client.get("/v1/catalog/programs/CS/plan").json()
        b_it = client.get("/v1/catalog/programs/IT/plan").json()
        def find_eng(levels):
            for lvl in levels:
                for c in lvl["courses"]:
                    if c["canonical_course_code"] == "ENG001":
                        return c["credit_hours"]
            return None
        cs_cr = find_eng(b_cs["levels"])
        it_cr = find_eng(b_it["levels"])
        assert cs_cr == 8,  f"ENG001 in CS expected 8cr, got {cs_cr}"
        assert it_cr == 16, f"ENG001 in IT expected 16cr, got {it_cr}"

    def test_law_404(self):
        assert client.get("/v1/catalog/programs/LAW/plan").status_code == 404

    def test_nonexistent_program_404(self):
        assert client.get("/v1/catalog/programs/ZZNOTEXIST/plan").status_code == 404

    def test_level_credits_sum_consistent(self):
        b = client.get("/v1/catalog/programs/CS/plan").json()
        level_sum = sum(lvl["required_credits"] for lvl in b["levels"])
        assert level_sum == b["required_credits_sum"]


# ─────────────────────────────────────────────────────────────────────────────
# GET /v1/catalog/programs/{program_code}/prerequisites
# ─────────────────────────────────────────────────────────────────────────────

class TestProgramPrerequisites:
    def test_cs_200(self):
        assert client.get("/v1/catalog/programs/CS/prerequisites").status_code == 200

    def test_cs_nonempty(self):
        b = client.get("/v1/catalog/programs/CS/prerequisites").json()
        assert b["count"] > 0

    def test_all_resolved(self):
        b = client.get("/v1/catalog/programs/CS/prerequisites").json()
        assert all(p["requires_canonical_code"] for p in b["prerequisites"])

    def test_no_needs_review(self):
        b = client.get("/v1/catalog/programs/CS/prerequisites").json()
        assert all(not p["needs_review"] for p in b["prerequisites"])

    def test_law_404(self):
        assert client.get("/v1/catalog/programs/LAW/prerequisites").status_code == 404

    def test_nonexistent_program_404(self):
        assert client.get("/v1/catalog/programs/ZZNOTEXIST/prerequisites").status_code == 404

    def test_relation_field_present(self):
        b = client.get("/v1/catalog/programs/CS/prerequisites").json()
        assert all(p["relation"] in ("prerequisite", "corequisite") for p in b["prerequisites"])


# ─────────────────────────────────────────────────────────────────────────────
# GET /v1/catalog/courses/{course_code}/programs
# ─────────────────────────────────────────────────────────────────────────────

class TestCoursePrograms:
    def test_eng001_200(self):
        assert client.get("/v1/catalog/courses/ENG001/programs").status_code == 200

    def test_eng001_program_count(self):
        b = client.get("/v1/catalog/courses/ENG001/programs").json()
        assert b["program_count"] == 8

    def test_eng001_law_absent(self):
        b = client.get("/v1/catalog/courses/ENG001/programs").json()
        assert not any(p["program_code"] == "LAW" for p in b["programs"])

    def test_eng001_credit_hours_per_program(self):
        b = client.get("/v1/catalog/courses/ENG001/programs").json()
        cm = {p["program_code"]: p["credit_hours"] for p in b["programs"]}
        assert cm["CS"] == 8
        assert cm["IT"] == 16

    def test_shared_course_no_dedup(self):
        # ENG001 appears in 8 programs — all 8 rows must be returned (no accidental dedup)
        b = client.get("/v1/catalog/courses/ENG001/programs").json()
        assert b["program_count"] == 8

    def test_arabic_canonical_resolves_directly(self):
        # نجل001 is the canonical code for HCI/MTT programs — query by canonical works
        r = client.get("/v1/catalog/courses/نجل001/programs")
        assert r.status_code == 200
        b = r.json()
        assert b["program_count"] > 0
        assert b["canonical_course_code"] == "نجل001"

    def test_alias_resolves_via_alias_lookup(self):
        # /aliases/ENG001 resolves to نجل001 — verify alias endpoint is consistent
        alias_b = client.get("/v1/catalog/aliases/ENG001").json()
        assert alias_b["canonical_course_code"] == "نجل001"

    def test_queried_as_preserved(self):
        b = client.get("/v1/catalog/courses/ENG001/programs").json()
        assert b["queried_as"] == "ENG001"

    def test_nonexistent_course_404(self):
        assert client.get("/v1/catalog/courses/ZZNOTEXIST999/programs").status_code == 404

    def test_islm101_in_multiple_programs(self):
        b = client.get("/v1/catalog/courses/ISLM101/programs").json()
        assert b["program_count"] >= 8


# ─────────────────────────────────────────────────────────────────────────────
# GET /v1/catalog/aliases/{alias_label}
# ─────────────────────────────────────────────────────────────────────────────

class TestAliases:
    def test_eng001_200(self):
        assert client.get("/v1/catalog/aliases/ENG001").status_code == 200

    def test_eng001_canonical(self):
        b = client.get("/v1/catalog/aliases/ENG001").json()
        assert b["canonical_course_code"] == "نجل001"

    def test_eng001_alias_type(self):
        b = client.get("/v1/catalog/aliases/ENG001").json()
        assert b["alias_type"] == "latin_code"

    def test_alias_label_preserved(self):
        b = client.get("/v1/catalog/aliases/ENG001").json()
        assert b["alias_label"] == "ENG001"

    def test_nonexistent_alias_404(self):
        assert client.get("/v1/catalog/aliases/ZZNOTEXIST").status_code == 404

    def test_confidence_field_present(self):
        b = client.get("/v1/catalog/aliases/ENG001").json()
        assert "confidence" in b
