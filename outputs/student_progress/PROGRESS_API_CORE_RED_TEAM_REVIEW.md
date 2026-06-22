# Progress API Core — Red Team Review
**Date:** 2026-06-23
**Scope:** `app/progress_api.py`, `tests/test_progress_profile.py`, `TEST_STUDENT_SMOKE_MATRIX_PLAN.md`
**Mode:** adversarial audit before drafting `077` seed. No DB writes, no migration, no seed, no deploy, no commit.
**Method:** every load-bearing claim was verified — code read in full, four claims probed against the live catalog, three claims confirmed by grep.

---

## Axis 1 — `app/progress_api.py`

| Check | Verdict | Evidence |
|-------|---------|----------|
| `/current` filter correct (`active`/`approved` only) | **PASS** | `_CURRENT_SECTION_FILTER="in.(active,approved)"` applied to all 3 section reads. Live-verified: `status` domain = `[active,dropped,needs_review,planned,approved]` |
| `student_registered_sections` is the sole `current` source | **PASS** | All three current/in_progress reads hit `student_registered_sections`; no other table feeds "current" |
| `course_state='in_progress'` NOT used as current source | **PASS** | grep: `in_progress` appears only in docstrings and as `in_progress_map` built **from section rows** — no `course_state=eq.in_progress` query exists |
| All credits from `cat_program_courses.credit_hours` | **PASS** | `_fetch_credit_hours_map` reads `v_draft_catalog_program_courses.credit_hours`; sums use only `cat["credit_hours"]` |
| `credit_hours_banner` excluded from calculations | **PASS** | grep: token appears only in comments/docstrings — never selected, never summed |
| No grades/GPA/percentages | **PASS** | grep: `grade/gpa/percent` appear only in one docstring line; no such field selected or returned |
| LAW protected | **PASS** | `_require_active_profile` raises 404 on `program_code.upper() in {LAW}` before any catalog read |
| Diploma protected | **PASS** | Diplomas absent from `v_draft_catalog_programs` → `_require_catalog_program` 404; `degree_type='diploma'` guard is the second line |
| `canonical_course_code=NULL` never crashes | **PASS** | Null codes excluded from `in.()`, rendered with `catalog_match=false, credit_hours=null`, surfaced as `canonical_code_missing` |
| Shared courses keep program context | **PASS** | Credit map filters `program_code=eq.{code}`. Live-verified: `ENG001`=8cr (CS) vs 16cr (MGT) — program-scoped |
| **program_code casing** | **FIXED** | Live probe: `program_code=eq.cs` returns `[]` (silent empty) while `eq.CS` returns the row. Was a silent `completed_credits=0` bug — now normalized to the catalog's canonical casing |

**Bug found and fixed (small, safe, clear — per mandate):**
A profile storing a non-canonical case (e.g. `cs`) passed the `_require_catalog_program` lookup (which uppercases internally) but then fed the raw `cs` into the credit-map and plan queries, which are case-sensitive in PostgREST → empty result → `completed_credits=0` with **no error and no warning**. Real Banner program codes vary in case, so this would have silently zeroed real students. **Fix:** after `_require_catalog_program`, all four endpoints now use `program_code = prog["program_code"]` (the catalog's authoritative casing). Regression test `test_lowercase_profile_code_uses_catalog_canonical_casing` added.

**Non-blocking warnings:**
- **W1 — repeated/duplicate-attempt double count is not structurally prevented.** Credit sums iterate over history rows; correctness depends on the invariant "≤1 `is_counted=true` per (student, canonical)". Neither the schema nor the API enforces it. If banner_sync ever writes two `is_counted=true` rows for the same canonical, the credit is counted twice. *Recommendation (future, not now):* a partial unique index `(student_id, canonical_course_code) WHERE is_counted=true`, or dedup-by-canonical in the API. The seed maintains the invariant, so the smoke matrix is unaffected.
- **W2 — no self-alias resolution.** Live probe confirmed `ENG001` does not exist in PH (only `نجل001`). The API trusts `canonical_course_code` to already be the program-correct canonical; it does not resolve `ENG001→نجل001`. If banner_sync stores the Latin canonical for a PH student, the join silently misses. This is a banner_sync/P9 responsibility, but worth tracking.

---

## Axis 2 — `tests/test_progress_profile.py`

| Check | Verdict | Notes |
|-------|---------|-------|
| Tests actually catch errors | **PARTIAL** | They rigorously validate **response-shaping** logic, and three tests record real query params (`status` filter, program_code casing, program_code-not-from-caller). But mocks return rows regardless of most filter params |
| Cosmetic/no-value tests | **PASS (none material)** | Field-presence and no-grades scans are legitimate guards, not filler |
| Mock hides a real bug | **WARNING** | The `is_counted=eq.true` filter is **not** exercised — the mock returns `MOCK_COMPLETED_ROWS` regardless. If someone dropped that filter, unit tests would still pass while failed/withdrawn rows leaked into credits. The **seeded smoke matrix (077) is the designed defense** for exactly this class |
| Covers dropped/planned/current | **PASS** | `test_dropped_section_excluded`, `test_status_filter_sent_to_db` |
| Covers repeated/failed | **PARTIAL** | Covered at the *data* level in the plan; in unit tests the mock can't distinguish `is_counted` so the repeated/failed exclusion is proven only by the seed, not the mock |
| Covers null canonical | **PASS** | `test_null_canonical_*` across completed/current |
| Covers shared credits | **PARTIAL** | `test_shared_course_credit_per_program` proves the 8cr path; the 8-vs-16 *contrast* is proven live/seed (mock returns a fixed value) |

**Verdict:** the unit suite (66 tests) is honest about its scope — it tests Python logic, not SQL predicates. The filter-level guarantees (`is_counted`, per-program credit contrast, repeated/failed exclusion) are deferred to the seeded smoke matrix by design. This is the correct division of labor, but it must be stated so no one mistakes "66 green" for "filters proven." The smoke matrix is therefore **required**, not optional.

---

## Axis 3 — `TEST_STUDENT_SMOKE_MATRIX_PLAN.md`

| Check | Verdict | Notes |
|-------|---------|-------|
| Five students sufficient | **PASS** | Cover bachelor-ready, transferee, needs_review+Arabic, master, diploma-guard — every dimension in the matrix has an owner |
| Built on real catalog data | **PASS** | All codes/credits/prereqs read live; `ENG001` 8/16, PH Arabic 48/48, `نجل001`=16, CS prereq edges, MGT missing level 2, CS null level — all confirmed |
| Expected outputs consistent with current code | **PASS** | Updated post-decision: Student A `current=2` (dropped excluded); credit sums match the casing-fixed code |
| Missing test case before 077 | **PASS (minor gaps noted)** | Non-blocking: (a) no single student combines `needs_review` + null canonical; (b) no case for "completed AND re-registered same course" (precedence is `completed`); (c) no W1 duplicate-`is_counted` probe. None block 077; can be folded into the seed later |
| Risk seed pollutes production | **WARNING (mitigated)** | Test students live in the **production default tenant** (mandatory — catalog views are tenant-scoped). Isolation rests entirely on the reserved `eeeeeeee-…` UUID block + `notes/change_reason='RUMMAN_SMOKE_TEST'`. This is acceptable and reversible (single-predicate teardown), but it is real shared-tenant data and must be torn down after the smoke run |

---

## Axis 4 — Readiness for 077

| Question | Answer |
|----------|--------|
| Does the schema suffice to mark test data? | **Partially.** `student_course_history.notes` ✓, `student_program_profile.change_reason` ✓, `student_registered_sections` ✗ (no marker; `source` CHECK-locked). Deferred debt — not blocking |
| Is the UUID block enough now? | **Yes.** Reserved `eeeeeeee-…A..E`, absent from `rumman_users`, single-predicate cleanup |
| Need a small migration before 077? | **No.** Decision 3 stands; an `is_test_data` migration is optional future hygiene |
| Need a commit before 077? | **Yes (recommended).** Today's red-team fix (casing), the §6 filter changes, and the +3 tests should be committed so 077 is drafted on a clean, recorded base |

---

## Summary

- **Blockers:** none.
- **Fix applied this session:** program_code casing normalization (silent `completed_credits=0` bug) + regression test. Tests: **66 passed**.
- **Non-blocking warnings:** W1 (duplicate-`is_counted` double count — future index/dedup), W2 (no self-alias resolution — banner_sync/P9), mock-coverage gap (filters proven by the seed, not unit tests), shared-tenant test data (mitigated by UUID block + teardown).
- **Recommendation:** commit the current changes, then draft `077_test_student_smoke_seed.DRAFT.sql`. The smoke matrix is not optional — it is the only layer that proves the `is_counted` / per-program-credit / repeated-exclusion guarantees end-to-end.

## Decision

**READY_TO_COMMIT_AND_DRAFT_077**
