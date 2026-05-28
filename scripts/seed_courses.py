#!/usr/bin/env python3
"""
seed_courses.py — Seeds courses, prerequisites, and description embeddings.

Usage:
    python3 scripts/seed_courses.py                  # seed all programs
    python3 scripts/seed_courses.py --dry-run        # validate JSON, no DB writes
    python3 scripts/seed_courses.py --embed          # also embed course descriptions
    python3 scripts/seed_courses.py --program BSCS   # one program only

Requires:
    - supabase/migrations/003_knowledge_layer.sql applied
    - SUPABASE_URL, SUPABASE_KEY, OPENAI_API_KEY env vars
"""

import os
import sys
import json
import argparse
import asyncio
from pathlib import Path

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

INSTITUTION = "SEU"
EMBED_MODEL = "text-embedding-3-large"
EMBED_DIMS = 1536

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

DATA_FILE = Path(__file__).parent / "data" / "seu_courses.json"


def log(event: str, **kwargs):
    parts = [event] + [f"{k}={v}" for k, v in kwargs.items()]
    print(" | ".join(parts), flush=True)


async def upsert_course(http: httpx.AsyncClient, row: dict) -> str:
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/courses",
        headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=representation"},
        params={"on_conflict": "course_code,institution"},
        json=row,
    )
    if r.status_code >= 400:
        log("COURSE_UPSERT_ERROR", code=row["course_code"], status=r.status_code, error=r.text[:120])
        return "error"
    return "upserted"


async def upsert_prerequisite(http: httpx.AsyncClient, row: dict) -> str:
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/course_prerequisites",
        headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=representation"},
        params={"on_conflict": "course_code,institution,prereq_code,prereq_institution"},
        json=row,
    )
    if r.status_code >= 400:
        log("PREREQ_UPSERT_ERROR", status=r.status_code, error=r.text[:120])
        return "error"
    return "upserted"


async def embed_description(ai: AsyncOpenAI, http: httpx.AsyncClient, course: dict, program: dict) -> bool:
    description = course.get("description", "").strip()
    if not description:
        return False

    course_code = course["course_code"]
    text = f"{course['course_title']} ({course_code})\n\n{description}"

    try:
        resp = await ai.embeddings.create(model=EMBED_MODEL, input=text, dimensions=EMBED_DIMS)
        embedding = resp.data[0].embedding

        chunk_row = {
            "institution": INSTITUTION,
            "course_code": course_code,
            "source_type": "course_description",
            "content": text,
            "embedding": embedding,
            "chunk_index": 0,
            "total_chunks": 1,
            "language": program.get("language", "en"),
        }

        r = await http.post(
            f"{SUPABASE_URL}/rest/v1/document_chunks",
            headers=HEADERS,
            json=chunk_row,
        )
        if r.status_code >= 400:
            log("EMBED_INSERT_ERROR", course=course_code, status=r.status_code, error=r.text[:120])
            return False

        log("EMBED_OK", course=course_code)
        return True

    except Exception as e:
        log("EMBED_ERROR", course=course_code, error=str(e)[:120])
        return False


async def seed_program(
    http: httpx.AsyncClient,
    ai: AsyncOpenAI,
    program: dict,
    dry_run: bool,
    embed: bool,
) -> dict:
    prog_code = program["program"]
    courses = program["courses"]
    log("PROGRAM_START", program=prog_code, courses=len(courses))

    inserted = 0
    prereq_inserted = 0
    embed_inserted = 0
    errors = 0

    for course in courses:
        course_code = course["course_code"]
        row = {
            "course_code": course_code,
            "institution": INSTITUTION,
            "course_title": course["course_title"],
            "credit_hours": course["credit_hours"],
            "level": course.get("level"),
            "program": prog_code,
            "college": program.get("college"),
            "college_ar": program.get("college_ar"),
            "description": course.get("description"),
            "language": program.get("language", "en"),
        }

        if dry_run:
            print(f"  [DRY] COURSE {course_code} — {course['course_title']}")
            for prereq in course.get("prerequisites", []):
                print(f"    [DRY] PREREQ {course_code} → {prereq}")
        else:
            result = await upsert_course(http, row)
            if result == "error":
                errors += 1
                continue
            inserted += 1

            for prereq_code in course.get("prerequisites", []):
                prereq_row = {
                    "course_code": course_code,
                    "institution": INSTITUTION,
                    "prereq_code": prereq_code,
                    "prereq_institution": INSTITUTION,
                }
                pr = await upsert_prerequisite(http, prereq_row)
                if pr == "upserted":
                    prereq_inserted += 1

            if embed and course.get("description"):
                ok = await embed_description(ai, http, course, program)
                if ok:
                    embed_inserted += 1

    log(
        "PROGRAM_DONE",
        program=prog_code,
        courses_upserted=inserted,
        prereqs_upserted=prereq_inserted,
        embeddings=embed_inserted,
        errors=errors,
    )
    return {"inserted": inserted, "prereqs": prereq_inserted, "embeddings": embed_inserted, "errors": errors}


async def main():
    parser = argparse.ArgumentParser(description="Seed SEU courses into RUMMAN database")
    parser.add_argument("--dry-run", action="store_true", help="Validate data without DB writes")
    parser.add_argument("--embed", action="store_true", help="Also embed course descriptions into document_chunks")
    parser.add_argument("--program", type=str, default=None, help="Seed a specific program only (e.g. BSCS)")
    args = parser.parse_args()

    if not DATA_FILE.exists():
        print(f"ERROR: Data file not found: {DATA_FILE}", file=sys.stderr)
        sys.exit(1)

    with open(DATA_FILE) as f:
        data = json.load(f)

    programs = data["programs"]
    if args.program:
        programs = [p for p in programs if p["program"] == args.program]
        if not programs:
            print(f"ERROR: Program '{args.program}' not found in data file", file=sys.stderr)
            sys.exit(1)

    log("SEED_START", institution=data["institution"], programs=len(programs), dry_run=args.dry_run, embed=args.embed)

    ai = AsyncOpenAI(api_key=OPENAI_API_KEY) if args.embed else None

    async with httpx.AsyncClient(timeout=60) as http:
        totals = {"inserted": 0, "prereqs": 0, "embeddings": 0, "errors": 0}
        for program in programs:
            result = await seed_program(http, ai, program, args.dry_run, args.embed)
            for k in totals:
                totals[k] += result.get(k, 0)

    log(
        "SEED_DONE",
        total_courses=totals["inserted"],
        total_prereqs=totals["prereqs"],
        total_embeddings=totals["embeddings"],
        total_errors=totals["errors"],
    )


if __name__ == "__main__":
    asyncio.run(main())
