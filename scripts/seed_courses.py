#!/usr/bin/env python3
"""
seed_courses.py — Populates inst_courses with names, credit hours, levels, and prerequisites.

Usage:
    python3 scripts/seed_courses.py                  # seed all programs
    python3 scripts/seed_courses.py --dry-run        # validate JSON, no DB writes
    python3 scripts/seed_courses.py --embed          # also embed descriptions into document_chunks
    python3 scripts/seed_courses.py --program BSCS   # one program only

Targets: inst_courses (migration 008), document_chunks (--embed only).
Conflict strategy: merge-duplicates on (tenant_id, code) — preserves specialization_id.

Requires:
    - supabase/migrations/008_curriculum_foundations.sql applied
    - SUPABASE_URL, SUPABASE_KEY env vars (plus OPENAI_API_KEY for --embed)
"""

from __future__ import annotations

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

SEU_TENANT_ID = os.environ.get("SEU_TENANT_ID", "00000000-0000-0000-0000-000000000001")
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
        f"{SUPABASE_URL}/rest/v1/inst_courses",
        headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=representation"},
        params={"on_conflict": "tenant_id,code"},
        json=row,
    )
    if r.status_code >= 400:
        log("COURSE_UPSERT_ERROR", code=row["code"], status=r.status_code, error=r.text[:120])
        return "error"
    return "upserted"


async def embed_description(
    ai: AsyncOpenAI,
    http: httpx.AsyncClient,
    course_code: str,
    course_title: str,
    description: str,
    language: str,
) -> bool:
    text = f"{course_title} ({course_code})\n\n{description}"
    try:
        resp = await ai.embeddings.create(model=EMBED_MODEL, input=text, dimensions=EMBED_DIMS)
        embedding = resp.data[0].embedding

        r = await http.post(
            f"{SUPABASE_URL}/rest/v1/document_chunks",
            headers=HEADERS,
            json={
                "tenant_id":    SEU_TENANT_ID,
                "course_code":  course_code,
                "source_type":  "course_description",
                "source_authority": "official",
                "content":      text,
                "embedding":    embedding,
                "chunk_index":  0,
                "total_chunks": 1,
                "language":     language,
            },
        )
        if r.status_code == 409:
            return True  # already embedded
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
    ai: AsyncOpenAI | None,
    program: dict,
    dry_run: bool,
    embed: bool,
) -> dict:
    prog_code = program["program"]
    courses = program["courses"]
    language = program.get("language", "en")
    log("PROGRAM_START", program=prog_code, courses=len(courses))

    upserted = 0
    embedded = 0
    errors = 0

    for course in courses:
        code = course["course_code"]
        title = course["course_title"]
        prereqs = course.get("prerequisites") or []
        description = (course.get("description") or "").strip()

        row = {
            "tenant_id":    SEU_TENANT_ID,
            "code":         code,
            "name_en":      title,
            "credit_hours": course["credit_hours"],
            "level":        course.get("level"),
            "prerequisites": prereqs,
            # specialization_id preserved via merge-duplicates; not overwritten
        }

        if dry_run:
            prereq_str = f"prereqs={prereqs}" if prereqs else ""
            print(f"  [DRY] {code} — {title} {prereq_str}".strip())
        else:
            result = await upsert_course(http, row)
            if result == "error":
                errors += 1
                continue
            upserted += 1

            if embed and description and ai:
                ok = await embed_description(ai, http, code, title, description, language)
                if ok:
                    embedded += 1

    log("PROGRAM_DONE", program=prog_code, upserted=upserted, embedded=embedded, errors=errors)
    return {"upserted": upserted, "embedded": embedded, "errors": errors}


async def main():
    parser = argparse.ArgumentParser(description="Seed SEU courses into inst_courses table")
    parser.add_argument("--dry-run", action="store_true", help="Validate data without DB writes")
    parser.add_argument("--embed", action="store_true", help="Embed descriptions into document_chunks")
    parser.add_argument("--program", type=str, default=None, help="Seed one program only (e.g. BSCS)")
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

    log("SEED_START", institution=data["institution"], programs=len(programs),
        dry_run=args.dry_run, embed=args.embed)

    ai = AsyncOpenAI(api_key=OPENAI_API_KEY) if args.embed else None

    async with httpx.AsyncClient(timeout=60) as http:
        totals: dict[str, int] = {"upserted": 0, "embedded": 0, "errors": 0}
        for program in programs:
            result = await seed_program(http, ai, program, args.dry_run, args.embed)
            for k in totals:
                totals[k] += result.get(k, 0)

    log("SEED_DONE", total_upserted=totals["upserted"],
        total_embedded=totals["embedded"], total_errors=totals["errors"])


if __name__ == "__main__":
    asyncio.run(main())
