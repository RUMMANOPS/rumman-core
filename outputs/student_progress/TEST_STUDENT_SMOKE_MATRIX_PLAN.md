# Test Student Smoke Matrix — Planning Document
**Date:** 2026-06-23
**Status:** PLANNING ONLY — no SQL, no DB writes, no migration, no commit, no deploy
**Author note:** All course codes, credit hours, and prerequisites below were read live from the validated catalog (`v_draft_catalog_*`). Nothing is invented from memory.

---

## 0. Why this is not an ordinary seed

This is a **diagnostic instrument**, not sample data. Five students are engineered so that *if the system is wrong in a specific way, a specific assertion fails*. Each student is a probe pointed at a known failure mode of catalog-linked progress systems — the same failure modes that quietly corrupt every university degree-audit product in the GCC.

The 5 students are chosen against the **real SEU academic reality**, not abstract test rows:

- **معادلة (transfer/exemption)** rows from Applied College diplomas arrive in Banner with non-standard course codes the catalog has never mapped → `canonical_course_code = NULL`. This is the single most common real corruption.
- **الإنجليزي المكثّف (ENG001/ENG002)** is the #1 repeated course at SEU. A `repeated` attempt that double-counts is the most expensive credit bug.
- **تحويل التخصص (program transfer)** — e.g. إدارة → حاسب — is extremely common. The same `ENG001` is 16cr in the old program and 8cr in the new one. Whichever program the student is *active* in must drive the credit value.
- **الانسحاب (withdraw / drop)** before the final is routine. `withdrawn` history and `dropped` sections must never count.
- **needs_review programs (PH/FIN/MDM)** must keep serving with a visible flag — never silently disappear.
- **الرموز العربية** — the entire Public Health plan is in Arabic canonical codes. A system that hardcodes Latin codes silently fails for every health student.

---

## 1. Testing Philosophy — what this sample must expose

| Dimension | How it is exercised | Real catalog fact used |
|-----------|--------------------|------------------------|
| Shared-course credit differs per program | Student A passes `ENG001`=**16cr** (MGT); Student B passes the same `ENG001`=**8cr** (CS) | `ENG001`: 16cr in MGT/IT/ACC, **8cr in CS** |
| `credit_hours` from catalog, never Banner | Every expected credit is computed from `cat_program_courses.credit_hours` | per-program rows confirmed |
| `repeated` counted once | Student B fails `CS230` then passes it on retake; only the pass counts | `CS230` 3cr, CS |
| `failed` does not open prerequisites | Student B failed `CS231` (only attempt) yet is registered in `CS241` (which requires `CS231`) | edge `CS241 → requires CS231` |
| `withdrawn` / `dropped` never count | Student A withdrew `ACCT101` (history) and dropped `STAT101` (section) | `ACCT101` 3cr, `STAT101` 3cr, MGT |
| `transferred` / `exempted` count | Student D `MGT520` transferred; Student C `صحة101` exempted | both real |
| `canonical_course_code = NULL` never crashes | Student B has an unmapped transfer row (`TRNS200`) | P9 backfill gap |
| Arabic canonical codes | Student C is entirely in `نجل001/ريض001/...` | PH = 48/48 Arabic |
| Alias resolution `ENG001 → نجل001` | Student C's Banner English row stores `banner=ENG001, canonical=نجل001` | alias confirmed |
| `needs_review` program stays visible + flagged | Student C (PH) must return 200 + `needs_review_program` warning | PH `program_status=needs_review` |
| Master ≠ bachelor assumptions | Student D (MBA) total = **36cr**, levels 1–4 | MBA 36cr |
| Diploma/`future` excluded explicitly | Student E (diploma) must return **404**, never silent zero | diploma in `future` view only |
| No grades/GPA/percentages | Every response is string-scanned for forbidden tokens | schema has none |
| Non-contiguous levels | MGT skips level 2; CS has a `NULL` level | confirmed |

---

## 2. The five students

> **Reserved UUID block:** `eeeeeeee-0000-0000-0000-00000000000A` … `…00E`.
> All five share the `eeeeeeee-` prefix so cleanup is a single predicate. None exist in `rumman_users` (no FK enforced on `student_id` in the progress tables — verified).
> **Tenant:** all use the default tenant `00000000-0000-0000-0000-000000000001` — mandatory, because the catalog views are tenant-scoped to it; a separate "test tenant" would return an empty catalog and invalidate every assertion.
> **Term codes** are illustrative SEU-style: `000000` = transfer/exemption (pre-enrollment), `2024x0` = prior years, `2025x0` = recent/current.

---

### Student A — Administrative Bachelor (MGT)

| Field | Value |
|-------|-------|
| `test_student_id` | `eeeeeeee-0000-0000-0000-00000000000A` |
| `program_code` | `MGT` |
| `degree_type` | bachelor |
| `support_level` | active |
| `program_status` | ready |
| `total_credits_official` | 130 |

**Why chosen:** A clean, active 130-credit administrative program — the baseline "happy" student. It anchors the shared-course test at the **16cr** end of `ENG001`, and carries a withdrawn course + a dropped section to expose status-filtering bugs.

**Defect it exposes if the system is wrong:**
- If `ENG001` is summed at 8cr (CS value) instead of 16cr (MGT value) → shared-course-per-program bug.
- If `withdrawn` `ACCT101` adds credit → withdrawn-counted bug.
- If a `dropped` section (`STAT101`) shows as a current course → status-filter bug (see §6, Issue 1).
- If the course `LAW101` is blocked because the string contains "LAW" → over-eager program guard.

**`student_program_profile` (expected, 1 active row):**
`program_code=MGT, source=banner_sync, is_active=true, started_at=2024-09-01, change_reason='RUMMAN_SMOKE_TEST'`

**`student_course_history` (proposed):**

| banner_course_code | canonical_course_code | course_state | term_code | is_counted | source | confidence | verified_by_student | catalog credit |
|---|---|---|---|---|---|---|---|---|
| ENG001 | ENG001 | passed | 202410 | true | banner_sync | high | true | **16** |
| MATH001 | MATH001 | passed | 202410 | true | banner_sync | high | true | 3 |
| CS001 | CS001 | passed | 202410 | true | banner_sync | high | false | 3 |
| CI001 | CI001 | passed | 202420 | true | banner_sync | high | false | 2 |
| COMM001 | COMM001 | passed | 202420 | true | banner_sync | high | false | 2 |
| ISLM101 | ISLM101 | passed | 202430 | true | banner_sync | high | false | 2 |
| LAW101 | LAW101 | passed | 202510 | true | banner_sync | medium | false | 3 |
| ACCT101 | ACCT101 | withdrawn | 202510 | false | banner_sync | high | false | (0) |

**Expected `completed_credits` = 16+3+3+2+2+2+3 = 31.** (`ACCT101` withdrawn → excluded.)

**`student_registered_sections` (proposed):**

| banner_course_code | canonical_course_code | status | term_code |
|---|---|---|---|
| MGT101 | MGT101 | active | 202520 |
| ECON101 | ECON101 | active | 202520 |
| STAT101 | STAT101 | dropped | 202520 |

---

### Student B — Computing Bachelor (CS), program transferee

| Field | Value |
|-------|-------|
| `test_student_id` | `eeeeeeee-0000-0000-0000-00000000000B` |
| `program_code` | `CS` (active) — **previously MGT (inactive)** |
| `degree_type` | bachelor |
| `support_level` | active |
| `program_status` | ready |
| `total_credits_official` | 130 |

**Why chosen:** The single richest probe. A realistic SEU **محول من إدارة إلى حاسب** who repeated a course, failed another, and carries an unmapped transfer row. Anchors the shared-course test at the **8cr** end of `ENG001` (CS), and validates the program-history design (one active profile among several).

**Defect it exposes if the system is wrong:**
- If the **inactive MGT** profile is used → `ENG001` summed at 16cr instead of CS's **8cr**, and 409 falsely raised.
- If the **repeated** `CS230` is counted twice → repeated double-count bug.
- If the **failed** `CS230` attempt is counted → failed-counted bug.
- If the **failed-only** `CS231` is treated as satisfying the prereq for the registered `CS241` → prerequisite-gap bug (future prereq layer must flag it; today it must at least not count `CS231` as completed).
- If the `NULL`-canonical `TRNS200` row crashes the endpoint or is silently summed → null-handling bug.

**`student_program_profile` (expected, 2 rows — exactly 1 active):**

| program_code | is_active | started_at | ended_at | change_reason |
|---|---|---|---|---|
| MGT | false | 2023-09-01 | 2024-06-01 | program transfer → CS (RUMMAN_SMOKE_TEST) |
| CS | true | 2024-09-01 | (null) | RUMMAN_SMOKE_TEST |

> Validates the partial unique index `uq_student_program_profile_one_active`: two rows, one active, no 409.

**`student_course_history` (proposed):**

| banner_course_code | canonical_course_code | course_state | term_code | is_counted | confidence | catalog credit |
|---|---|---|---|---|---|---|
| ENG001 | ENG001 | passed | 202410 | true | high | **8** |
| ENG002 | ENG002 | passed | 202420 | true | high | 8 |
| MATH001 | MATH001 | passed | 202410 | true | high | 3 |
| CS001 | CS001 | passed | 202410 | true | high | 3 |
| CI001 | CI001 | passed | 202420 | true | high | 2 |
| COMM001 | COMM001 | passed | 202420 | true | high | 2 |
| ISLM101 | ISLM101 | passed | 202430 | true | high | 2 |
| CS230 | CS230 | failed | 202430 | false | high | (0) |
| CS230 | CS230 | repeated | 202510 | true | high | 3 |
| CS231 | CS231 | failed | 202510 | false | high | (0) |
| MATH150 | MATH150 | passed | 202510 | true | high | 3 |
| TRNS200 | *(NULL)* | passed | 000000 | true | low | (0 — no catalog match) |

> Note: `(student_id, term_code, banner_course_code)` is unique — the two `CS230` attempts sit in different terms (`202430` fail, `202510` retake), satisfying the constraint.

**Expected `completed_credits` = 8+8+3+3+2+2+2+3+3 = 34.** (`CS230` fail, `CS231` fail, `TRNS200` null → 0 each.)
**Expected `completed_courses_count` = 10** (all `is_counted=true` rows, *including* `TRNS200`) — deliberately ≠ the 9 credit-bearing rows, to test that count and credit are computed independently.

**`student_registered_sections` (proposed):**

| banner_course_code | canonical_course_code | status | term_code | note |
|---|---|---|---|---|
| CS240 | CS240 | active | 202520 | prereq `CS230` ✓ (retake passed) → legitimately in progress |
| CS241 | CS241 | active | 202520 | prereq `CS231` ✗ (only failed) → **prerequisite gap** |

---

### Student C — Health Bachelor (PH), Arabic-coded, needs_review

| Field | Value |
|-------|-------|
| `test_student_id` | `eeeeeeee-0000-0000-0000-00000000000C` |
| `program_code` | `PH` |
| `degree_type` | bachelor |
| `support_level` | active |
| `program_status` | **needs_review** |
| `total_credits_official` | 133 |

**Why chosen:** PH is the only chosen program that is simultaneously (a) `needs_review` and (b) **entirely Arabic-coded** (48/48). It is the single best probe for the two most dangerous "invisible" failures: a flagged program silently vanishing, and Arabic canonical codes breaking joins/encoding.

**Defect it exposes if the system is wrong:**
- If PH returns 404/empty because it is `needs_review` → flagged-program-hidden bug (it must return **200 + warning**).
- If `نجل001` (Arabic) crashes the catalog join or renders as mojibake → Arabic-code bug.
- If the Banner English row (`banner=ENG001`) is not reconciled to canonical `نجل001` → alias-resolution gap.
- If `exempted` `صحة101` does not count → exempted-not-counted bug.

**`student_program_profile` (expected):** `program_code=PH, source=banner_sync, is_active=true, change_reason='RUMMAN_SMOKE_TEST'`

**`student_course_history` (proposed — real PH Arabic codes):**

| banner_course_code | canonical_course_code | course_state | term_code | is_counted | catalog credit |
|---|---|---|---|---|---|
| ENG001 | نجل001 | passed | 202410 | true | **16** (alias-resolved) |
| ريض001 | ريض001 | passed | 202410 | true | 3 |
| عال001 | عال001 | passed | 202410 | true | 3 |
| علم001 | علم001 | passed | 202420 | true | 2 |
| نهج001 | نهج001 | passed | 202420 | true | 2 |
| حيا101 | حيا101 | passed | 202430 | true | 3 |
| صحة101 | صحة101 | exempted | 000000 | true | 3 |

**Expected `completed_credits` = 16+3+3+2+2+3+3 = 32.**

**`student_registered_sections` (proposed):**

| banner_course_code | canonical_course_code | status | term_code |
|---|---|---|---|
| حيا102 | حيا102 | active | 202520 |

---

### Student D — Graduate Program (MBA)

| Field | Value |
|-------|-------|
| `test_student_id` | `eeeeeeee-0000-0000-0000-00000000000D` |
| `program_code` | `MBA` |
| `degree_type` | **master** |
| `support_level` | active |
| `program_status` | ready |
| `total_credits_official` | **36** |

**Why chosen:** Proves the progress engine makes no bachelor-shaped assumptions. MBA is 36 credits across 4 levels, every course 3cr. Carries a `transferred` course (معادلة من جهة أخرى — common for executives).

**Defect it exposes if the system is wrong:**
- If `remaining_credits` is computed against a hardcoded bachelor total (e.g. 130) → it would read 115 instead of 21.
- If `degree_type=master` is not surfaced, or the short 4-level plan is mis-grouped → master-assumption bug.
- If `transferred` `MGT520` does not count → transferred-not-counted bug.

**`student_program_profile` (expected):** `program_code=MBA, source=self_declared, is_active=true, change_reason='RUMMAN_SMOKE_TEST'`

**`student_course_history` (proposed — real MBA codes, all 3cr):**

| banner_course_code | canonical_course_code | course_state | term_code | is_counted |
|---|---|---|---|---|
| ECN500 | ECN500 | passed | 202410 | true |
| FIN500 | FIN500 | passed | 202410 | true |
| RES500 | RES500 | passed | 202420 | true |
| MGT510 | MGT510 | passed | 202420 | true |
| MGT520 | MGT520 | transferred | 000000 | true |

**Expected `completed_credits` = 5 × 3 = 15.** `remaining = 36 − 15 = 21`.

**`student_registered_sections` (proposed):**

| banner_course_code | canonical_course_code | status | term_code |
|---|---|---|---|
| MGT560 | MGT560 | active | 202520 |

---

### Student E — Applied College Diploma (PROTECTION TEST)

| Field | Value |
|-------|-------|
| `test_student_id` | `eeeeeeee-0000-0000-0000-00000000000E` |
| `program_code` | `BUSINESS_ADMINISTRATION` |
| `degree_type` | diploma |
| `support_level` | **future** |
| `program_status` | ready |
| `total_credits_official` | 30 |

**Why chosen:** This is a *guard* test, not an active learner. A `student_program_profile` row is deliberately created pointing at a `future` diploma (possible, because `program_code` is a free soft-FK). The system must refuse to serve it.

**Defect it exposes if the system is wrong:**
- If any endpoint returns **200 + empty / zero credits** → silent-zero bug (the worst outcome: looks like a real but empty student).
- If the diploma surfaces as an active program → `future` leakage (the diploma-equivalent of the LAW leak).

**`student_program_profile` (expected):** `program_code=BUSINESS_ADMINISTRATION, source=inferred, is_active=true, change_reason='RUMMAN_SMOKE_TEST'`

**`student_course_history`:** none. **`student_registered_sections`:** none. (Adding them would be pointless — the guard fires before any history is read.)

**Guard path:** `BUSINESS_ADMINISTRATION` does not exist in `v_draft_catalog_programs` (it lives only in `v_draft_catalog_future_programs`). Therefore `_require_catalog_program` finds no row → **404 "Program … not found in active catalog."** The `degree_type='diploma'` exclusion in `_require_catalog_program` is the belt-and-suspenders second line, fired only if a diploma ever leaks into the active view.

---

## 3. Expected outputs per endpoint

Legend: `cc` = `completed_credits`, `rc` = `remaining_credits`, `nr` = `needs_review`.

### Student A (MGT)

| Endpoint | Status | Key expectations |
|---|---|---|
| `/profile` | 200 | `program_code=MGT`, `degree_type=bachelor`, `nr=false`, `total=130` |
| `/completed` | 200 | `cc=31`; 7 courses; `ENG001.credit_hours=16`; `ACCT101` absent; no `canonical_code_missing` |
| `/current` | 200 | 2 courses (MGT101, ECON101); `STAT101` (dropped) **excluded** by the `status IN ('active','approved')` filter (Decision 1, implemented) |
| `/summary` | 200 | `cc=31`, `rc=99`, `completed_courses_count=7`, `current_courses_count=2`, `nr=false` |
| `/plan-status` | 200 | level-1 group all `completed`; `MGT101/ECON101` `in_progress`; `ACCT101` `not_started`; **no level-2 group** (MGT has none); `prereq_check_not_implemented` warning present |

### Student B (CS, transferee)

| Endpoint | Status | Key expectations |
|---|---|---|
| `/profile` | 200 | `program_code=CS` (the **active** one — not MGT); no 409 |
| `/completed` | 200 | `cc=34`; `ENG001.credit_hours=8` (CS, not 16); `CS230` appears once (`repeated`); `CS231`/failed-`CS230` absent; `TRNS200` present with `catalog_match=false, credit_hours=null`; `canonical_code_missing` warning (count=1) |
| `/current` | 200 | 2 courses (CS240, CS241) |
| `/summary` | 200 | `cc=34`, `rc=96`, `completed_courses_count=10`, `current_courses_count=2`, `canonical_code_missing` warning |
| `/plan-status` | 200 | level-1/2 completed; `CS240` `in_progress`; `CS241` `in_progress` **today** but flagged by future prereq layer (registered while `CS231` failed); `TRNS200` in `orphaned_completed` as `blocked_unknown`; CS `NULL`-level electives grouped under a null-level bucket |

### Student C (PH, Arabic, needs_review)

| Endpoint | Status | Key expectations |
|---|---|---|
| `/profile` | 200 | `program_code=PH`, `nr=true`, `total=133` — **served, not hidden** |
| `/completed` | 200 | `cc=32`; Arabic codes render correctly; `نجل001.credit_hours=16`; `صحة101` (exempted) counted; `needs_review_program` warning present |
| `/current` | 200 | 1 course (`حيا102`); `needs_review_program` warning |
| `/summary` | 200 | `cc=32`, `rc=101`, `completed_courses_count=7`, `current_courses_count=1`, `nr=true`, `needs_review_program` warning |
| `/plan-status` | 200 | levels 1–4 with Arabic titles; passed PH courses `completed`; `حيا102` `in_progress`; `nr=true` |

### Student D (MBA, master)

| Endpoint | Status | Key expectations |
|---|---|---|
| `/profile` | 200 | `degree_type=master`, `total=36` |
| `/completed` | 200 | `cc=15`; 5 courses; `MGT520` (transferred) counted; no `canonical_code_missing` |
| `/current` | 200 | 1 course (`MGT560`) |
| `/summary` | 200 | `cc=15`, **`rc=21`** (proves 36 not 130), `completed_courses_count=5`, `current_courses_count=1` |
| `/plan-status` | 200 | levels 1–4 only; passed courses `completed`; `MGT560` `in_progress` |

### Student E (diploma, guard)

| Endpoint | Status | Key expectations |
|---|---|---|
| `/profile` | **404** | clear message: program not in active catalog. **Not** 200/empty |
| `/completed` | **404** | same |
| `/current` | **404** | same |
| `/summary` | **404** | same |
| `/plan-status` | **404** | same |

---

## 4. Bug → Student mapping (explicit)

| Failure mode | Caught by | Failing signal |
|---|---|---|
| `credit_hours` taken from Banner instead of catalog | A & B & C | A `ENG001`≠16, B `ENG001`≠8, C `نجل001`≠16 |
| `program_code` taken from caller instead of profile | all | injecting `?program_code=X` changes output |
| Wrong profile used (inactive program) | B | `cc` uses 16cr `ENG001` (MGT) instead of 8cr (CS) |
| Diploma shown as active | E | any 200 instead of 404 |
| Silent zero instead of explicit error | E | 200 with `cc=0` instead of 404 |
| LAW leaked into surface | (covered by catalog suite) + A | `LAW101` course must still serve; LAW *program* never does |
| PH/MDM/FIN hidden because `needs_review` | C | `/profile` 404 or missing `needs_review_program` warning |
| `canonical_course_code = NULL` crash / silent sum | B | 500 error, or `TRNS200` adds credit |
| `repeated` counted twice | B | `cc=37` instead of 34 |
| `failed` opens prerequisites / counts | B | `CS231` treated as completed; `cc` too high |
| `current` course counted as `completed` | A, B, C, D | `in_progress` rows leak into `cc` |
| `dropped` / `withdrawn` counted | A | `STAT101`/`ACCT101` appear or add credit |
| shared course merged without program context | A vs B | both show identical `ENG001` credit |
| GPA/grades surfaced | all | forbidden token in any response body |
| Master assumed bachelor | D | `rc=115` instead of 21 |
| Arabic code broken | C | mojibake / empty join for `نجل001` |

---

## 5. Later seed plan (NOT executed now)

**Proposed file:** `outputs/student_progress/077_test_student_smoke_seed.DRAFT.sql`
(*077, not 076 — 076 is already applied as the PK fix.*)

**Tables to populate (all reads above already validated the codes exist):**
1. `student_program_profile` — 6 rows (A, B-CS active, B-MGT inactive, C, D, E).
2. `student_course_history` — A:8, B:12, C:7, D:5, E:0 = 32 rows.
3. `student_registered_sections` — A:3, B:2, C:1, D:1, E:0 = 7 rows.

**Test-only guarantee (layered):**
1. **Reserved UUID block** `eeeeeeee-0000-0000-0000-00000000000A..E` — the primary marker. These IDs are not in `rumman_users` and will never be issued to a real student.
2. **`student_course_history.notes = 'RUMMAN_SMOKE_TEST'`** — free-text column exists.
3. **`student_program_profile.change_reason` carries `'RUMMAN_SMOKE_TEST'`** — no `notes` column, but `change_reason` is free text.
4. **`student_registered_sections`** — see blocker below.

**Cleanup (single predicate, reversible):**
```
DELETE FROM student_registered_sections WHERE student_id::text LIKE 'eeeeeeee-%';
DELETE FROM student_course_history      WHERE student_id::text LIKE 'eeeeeeee-%';
DELETE FROM student_program_profile     WHERE student_id::text LIKE 'eeeeeeee-%';
```
The seed migration should ship with this teardown block commented at its foot, and a companion `outputs/.../077_test_student_smoke_teardown.sql`.

---

## 6. Design decisions (resolved 2026-06-23)

The three risks discovered while reading the real schema have been decided. Decisions 1–2 are reflected in `progress_api.py`; Decision 3 is recorded as deferred debt.

### Decision 1 — current courses = `status IN ('active','approved')` only — IMPLEMENTED
`student_registered_sections.status` allows `[active, dropped, needs_review, planned, approved]`. The endpoints now filter the section query to `status IN ('active','approved')`. `planned` (a smart-registration draft), `dropped`, and `needs_review` are no longer treated as current.
- Applied to all three section reads: `/current`, `/summary` (count), and `/plan-status` (in_progress overlay), for consistency.
- **Impact:** Student A's `STAT101` (dropped) is excluded → `current_courses_count = 2`. Covered by tests `test_dropped_section_excluded` and `test_status_filter_sent_to_db`.

### Decision 2 — `student_registered_sections` is the sole source of truth for "current" — ADOPTED
For this milestone, "current courses" are read **only** from `student_registered_sections`. `student_course_history.course_state='in_progress'` is intentionally NOT used as a current source, so a course cannot appear twice. `student_course_history` remains the record of prior/completed/failed/transferred attempts — not the current surface. Documented in the `/current` docstring.

### Decision 3 — test-data marker via reserved UUID block — DEFERRED DEBT (no schema change)
`student_registered_sections` has no `notes`/`is_test_data` column, and `source` is CHECK-restricted to `('smart_registration','manual')`. No migration will be added now. Test data is marked by:
- the reserved `student_id` UUID block `eeeeeeee-…`,
- `student_course_history.notes = 'RUMMAN_SMOKE_TEST'`,
- `student_program_profile.change_reason = 'RUMMAN_SMOKE_TEST'`.
**Deferred debt:** `student_registered_sections` test rows are identifiable only by the UUID block (no per-row marker). If broader test-data hygiene is needed later, add `is_test_data boolean default false` to the three student tables in a dedicated migration. Not required for this smoke matrix.

---

## 7. Final decision

**READY_TO_DRAFT_077_TEST_STUDENT_SMOKE_SEED**

The matrix is built entirely on live catalog data, every expected credit is hand-derived from `cat_program_courses.credit_hours`, each of the five students is pinned to a specific failure mode, and the three design decisions (§6) are resolved — Decisions 1–2 implemented in `progress_api.py` (65/65 tests green), Decision 3 recorded as deferred debt. Nothing blocks authoring `077_test_student_smoke_seed.DRAFT.sql`. No SQL written, no data inserted, nothing deployed in this step.
