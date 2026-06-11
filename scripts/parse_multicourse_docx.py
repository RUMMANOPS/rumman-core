#!/usr/bin/env python3
"""
parse_multicourse_docx.py — Parse program-specification DOCX files for IT/DS courses.

These files (توصيف-مقررات-BSIT/BSDS-2025-2026.docx) are program-level documents, NOT
individual course syllabi. They contain the course list with codes and titles but no
chapter-level breakdowns.

This script:
1. Extracts course codes + titles from the structured tables inside the DOCX
2. For each course without an existing syllabus in kg_syllabi, calls GPT-4o to generate
   the standard academic chapter structure based on the course title
3. Stores chapters with source_type='synthesized' and confidence=0.70 so downstream
   workers (chapter_attribution) know these are inferred, not official

Run:
    python3 scripts/parse_multicourse_docx.py
    python3 scripts/parse_multicourse_docx.py --dry-run
    python3 scripts/parse_multicourse_docx.py --skip-existing  # skip courses already in kg_syllabi
"""
from __future__ import annotations
import os, json, asyncio, sys, argparse
from pathlib import Path
import httpx
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL   = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
TENANT_ID      = "00000000-0000-0000-0000-000000000001"

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

TARGET_DOCX = [
    Path("/Users/ibrahim../Projects/0-RUMMAN/0-Universities/1- Saudi Electronic University/4. CourseContent/كلية الحوسبة والمعلوماتية/قسم تقنية المعلومات/توصيف-مقررات-BSIT-2025-2026.docx"),
    Path("/Users/ibrahim../Projects/0-RUMMAN/0-Universities/1- Saudi Electronic University/4. CourseContent/كلية الحوسبة والمعلوماتية/قسم علوم الحاسب/توصيف-مقررات-BSDS-2025-2026.docx"),
]

# Prefixes that are institute-level, not IT/DS-specific — skip chapter generation
_SKIP_PREFIXES = {"CS0", "ENG0", "CI0", "MATH0", "COMM0", "SCI", "ISLM", "STAT"}

_SYNTH_SYSTEM = """\
You are an academic curriculum expert specializing in Information Technology and Computer Science.

Given a course code and title from Saudi Electronic University (SEU), generate the standard
chapter / weekly topic structure for a 15-week undergraduate course.

Return ONLY valid JSON (no text outside):
{
  "course_code": "IT353",
  "course_title": "System Analysis and Design",
  "academic_year": "2025-2026",
  "total_chapters": 10,
  "chapters": [
    {
      "chapter_number": 1,
      "chapter_title": "Introduction to Systems Development",
      "chapter_title_ar": "مقدمة في تطوير الأنظمة",
      "topics_raw": ["SDLC phases", "System analyst role", "Types of information systems"],
      "learning_outcomes": ["Define information system", "Identify SDLC phases"],
      "week_start": 1,
      "week_end": 1
    }
  ]
}

Rules:
- Produce 8–14 chapters (typical 15-week course after removing review/exam weeks)
- Topics must be specific and aligned to the course title — not generic placeholders
- chapter_title_ar should be a proper Arabic academic title
- week_start/week_end should distribute evenly across 15 weeks
- Order chapters from foundational to advanced
"""


def _read_course_table(path: Path) -> list[dict]:
    """Extract course code + title from the program-spec DOCX (Table 4 = course list)."""
    try:
        import docx
    except ImportError:
        print("ERROR: python-docx not installed. Run: pip install python-docx")
        sys.exit(1)

    doc = docx.Document(str(path))
    courses = []
    for table in doc.tables:
        # Find the course-list table: first cell header contains "Level" and second "Course"
        if not table.rows:
            continue
        header_cells = [c.text.strip() for c in table.rows[0].cells]
        if len(header_cells) < 3:
            continue
        if "Level" not in header_cells[0] or "Course" not in header_cells[1]:
            continue
        # Parse rows
        for row in table.rows[1:]:
            code  = row.cells[1].text.strip().replace("\n", "").strip()
            title = row.cells[2].text.strip().replace("\n", " ").strip()
            if code and title and code not in ("Course\nCode", "Course Code"):
                # Filter out rows with no real code (empty separator rows)
                if any(c.isalpha() for c in code):
                    courses.append({"course_code": code, "course_title": title})
        break  # only the first matching table
    return courses


async def _fetch_existing_codes(http: httpx.AsyncClient) -> set[str]:
    """Return course codes already in kg_syllabi."""
    resp = await http.get(
        f"{SUPABASE_URL}/rest/v1/kg_syllabi",
        headers=HEADERS,
        params={"select": "course_code", "limit": "1000"},
    )
    if resp.status_code == 200:
        return {r["course_code"] for r in resp.json()}
    return set()


async def _synthesize_chapters(client: AsyncOpenAI, course_code: str, course_title: str) -> dict:
    resp = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": _SYNTH_SYSTEM},
            {"role": "user",   "content": f"Course code: {course_code}\nCourse title: {course_title}"},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
        max_tokens=4096,
    )
    raw = resp.choices[0].message.content or "{}"
    result = json.loads(raw)
    result["_tokens"] = resp.usage.total_tokens
    return result


async def _store(http: httpx.AsyncClient, course_code: str, course_title: str,
                  parsed: dict) -> int:
    chapters = parsed.get("chapters", [])
    if not chapters:
        return 0

    syl_resp = await http.post(
        f"{SUPABASE_URL}/rest/v1/kg_syllabi",
        headers=HEADERS,
        content=json.dumps({
            "tenant_id":          TENANT_ID,
            "course_code":        course_code,
            "academic_year":      parsed.get("academic_year", "2025-2026"),
            "total_chapters":     len(chapters),
            "is_current":         True,
            "parsing_confidence": 0.70,
            "source_type":        "synthesized",
            "raw_text":           f"[Synthesized from: {course_title}]",
        }),
    )
    if syl_resp.status_code not in (200, 201):
        print(f"  ERROR syllabus {course_code}: {syl_resp.text[:100]}")
        return 0

    syl_data = syl_resp.json()
    syllabus_id = syl_data[0]["id"] if isinstance(syl_data, list) else syl_data["id"]

    rows = [{
        "tenant_id":         TENANT_ID,
        "syllabus_id":       syllabus_id,
        "course_code":       course_code,
        "chapter_number":    ch.get("chapter_number", i + 1),
        "chapter_title":     ch.get("chapter_title"),
        "chapter_title_ar":  ch.get("chapter_title_ar"),
        "topics_raw":        ch.get("topics_raw") or [],
        "learning_outcomes": ch.get("learning_outcomes") or [],
        "week_start":        ch.get("week_start"),
        "week_end":          ch.get("week_end"),
        "confidence":        0.70,
    } for i, ch in enumerate(chapters)]

    ch_resp = await http.post(
        f"{SUPABASE_URL}/rest/v1/kg_chapters"
        "?on_conflict=course_code,chapter_number,syllabus_id",
        headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
        content=json.dumps(rows),
    )
    if ch_resp.status_code in (200, 201, 204):
        return len(rows)
    print(f"  ERROR chapters {course_code}: {ch_resp.text[:100]}")
    return 0


async def main(dry_run: bool, skip_existing: bool):
    client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    async with httpx.AsyncClient(timeout=60) as http:
        existing = await _fetch_existing_codes(http) if skip_existing else set()
        if existing:
            print(f"Skipping {len(existing)} codes already in kg_syllabi")

        total_courses = 0
        total_chapters = 0

        for docx_path in TARGET_DOCX:
            if not docx_path.exists():
                print(f"NOT FOUND: {docx_path.name}")
                continue

            print(f"\n{'='*60}")
            print(f"Reading: {docx_path.name}")
            courses = _read_course_table(docx_path)
            print(f"Found {len(courses)} courses in table")

            for c in courses:
                code  = c["course_code"]
                title = c["course_title"]

                # Skip institute-level general courses
                if any(code.startswith(p) for p in _SKIP_PREFIXES):
                    print(f"  {code:<12} SKIP (non-program course)")
                    continue

                if skip_existing and code in existing:
                    print(f"  {code:<12} SKIP (already exists)")
                    continue

                print(f"  {code:<12} {title[:50]}", end="", flush=True)

                if dry_run:
                    print(" [dry-run]")
                    continue

                parsed = await _synthesize_chapters(client, code, title)
                n = len(parsed.get("chapters", []))
                tokens = parsed.get("_tokens", 0)
                print(f" → {n} chapters ({tokens} tok)", end="", flush=True)

                stored = await _store(http, code, title, parsed)
                print(f" stored={stored}")

                total_courses += 1
                total_chapters += stored

        print(f"\n{'='*60}")
        print(f"Total: {total_courses} courses, {total_chapters} chapters stored")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run",       action="store_true")
    p.add_argument("--skip-existing", action="store_true", help="Skip courses already in kg_syllabi")
    args = p.parse_args()
    asyncio.run(main(dry_run=args.dry_run, skip_existing=args.skip_existing))
