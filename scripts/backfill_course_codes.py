#!/usr/bin/env python3
"""
backfill_course_codes.py — Infer and backfill course_code for source_documents where it is NULL.

Strategy (cheapest-first):
  1. Regex extraction from file_name  — free, handles "IT362_exam.pdf" etc.
  2. GPT-4o-mini inference            — from filename + first chunk content,
                                        only for docs that passed regex with no match.

Updates both source_documents.course_code AND all related document_chunks.course_code.

Usage:
    python3 scripts/backfill_course_codes.py [--dry-run] [--limit N] [--llm-only]
"""

import os
import re
import json
import asyncio
import argparse
from typing import Optional

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}
HEADERS_REP = {**HEADERS, "Prefer": "return=representation"}

# Course code pattern: 2-4 uppercase letters + 3 digits + optional letter
# Matches: IT362, MGT425, ENG103, ECOM101, MATH150, CS231, DS242, etc.
_COURSE_RE = re.compile(r'\b([A-Z]{2,4}\d{3}[A-Z]?)\b')

# Known-good codes from the corpus (extracted from document_chunks)
KNOWN_CODES = {
    "IT362","IT488","MGT425","IT363","ENG103","IT476","IT474","IT353","IT478",
    "IT484","IT475","CS231","ECOM101","IT365","MATH150","IT245","IT361","STAT101",
    "IT487","CS364","ECON101","MGT311","IT351","MGT211","IT364","DS242","CS242",
    "CS251","MGT323","IT352","CS363","IT486","FIN405","ACCT321","IT370","MGT321",
    "IT380","CS361","IT489","CS252","IT471","CS244","CS245","MATH251","ECON201",
    "IT240","CS362","IT481","CS354","IT368","CS241","IT460","CS350","IT490",
    "MGT331","CS353","IT461","CS355","IT462","CS356","FIN101","STAT201","MGT201",
    "ACCT101","ECOM201","IT371","CS465","IT481","CS466","IT483","CS467",
}


def log(msg: str, **kw):
    parts = [msg] + [f"{k}={v}" for k, v in kw.items()]
    print(" | ".join(parts), flush=True)


def regex_extract(text: str) -> Optional[str]:
    """Extract a course code via regex from filename or text snippet."""
    if not text:
        return None
    matches = _COURSE_RE.findall(text.upper())
    for m in matches:
        if m in KNOWN_CODES:
            return m
    # Accept any match even if not in known list (new courses)
    return matches[0] if matches else None


async def get_docs_needing_codes(http: httpx.AsyncClient, limit: int, offset: int = 0) -> list[dict]:
    params = {
        "course_code": "is.null",
        "processing_status": "eq.chunked",
        "select": "id,file_name,source_type",
        "limit": str(limit),
        "order": "created_at.asc",
    }
    if offset:
        params["offset"] = str(offset)
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/source_documents",
        headers=HEADERS,
        params=params,
    )
    if r.status_code >= 400:
        log("FETCH_ERROR", status=r.status_code, body=r.text[:120])
        return []
    return r.json()


async def get_first_chunk(http: httpx.AsyncClient, doc_id: str) -> Optional[str]:
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/document_chunks",
        headers=HEADERS,
        params={
            "source_document_id": f"eq.{doc_id}",
            "order": "chunk_index.asc",
            "limit": "1",
            "select": "content",
        },
    )
    if r.status_code >= 400 or not r.json():
        return None
    return (r.json()[0].get("content") or "")[:600]


async def llm_infer_course(ai: AsyncOpenAI, filename: str, snippet: str) -> Optional[str]:
    known_sample = ", ".join(sorted(KNOWN_CODES)[:40])
    prompt = (
        f"You are identifying the SEU course code for a student document.\n\n"
        f"File name: {filename or 'unknown'}\n"
        f"Content snippet (first 600 chars):\n{snippet or '(empty)'}\n\n"
        f"Known course codes include: {known_sample}, and others following the pattern XYYY999.\n\n"
        f"Reply with ONLY the course code (e.g. IT362) or the word NULL if you cannot determine it. "
        f"No explanation."
    )
    try:
        resp = await ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=12,
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip().upper()
        if raw == "NULL" or not raw:
            return None
        # Validate format
        if _COURSE_RE.fullmatch(raw):
            return raw
        # Maybe GPT added punctuation
        m = _COURSE_RE.search(raw)
        return m.group(0) if m else None
    except Exception as e:
        log("LLM_ERROR", error=str(e)[:80])
        return None


async def apply_course_code(http: httpx.AsyncClient, doc_id: str, code: str, dry_run: bool):
    if dry_run:
        return
    # Update source_document
    await http.patch(
        f"{SUPABASE_URL}/rest/v1/source_documents?id=eq.{doc_id}",
        headers=HEADERS,
        json={"course_code": code},
    )
    # Update all its chunks
    await http.patch(
        f"{SUPABASE_URL}/rest/v1/document_chunks?source_document_id=eq.{doc_id}",
        headers=HEADERS,
        json={"course_code": code},
    )


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--llm-only", action="store_true", help="Skip regex, use LLM for all")
    args = parser.parse_args()

    log("BACKFILL_COURSE_CODES_START", limit=args.limit, offset=args.offset, dry_run=args.dry_run)

    ai = AsyncOpenAI(api_key=OPENAI_API_KEY)

    async with httpx.AsyncClient(timeout=60) as http:
        docs = await get_docs_needing_codes(http, args.limit, args.offset)
        log("DOCS_FETCHED", count=len(docs))

        regex_hits = 0
        llm_hits = 0
        llm_null = 0
        no_content = 0
        errors = 0

        for i, doc in enumerate(docs):
            doc_id = doc["id"]
            filename = doc.get("file_name") or ""
            source = filename

            # Step 1: regex on filename/caption
            code = None if args.llm_only else regex_extract(source)

            if code:
                regex_hits += 1
                log("REGEX_HIT", doc=doc_id[:8], file=filename[:40], code=code)
                await apply_course_code(http, doc_id, code, args.dry_run)
                continue

            # Step 2: LLM with content snippet
            snippet = await get_first_chunk(http, doc_id)
            if not snippet and not source.strip():
                no_content += 1
                continue

            # Also try regex on snippet before calling LLM
            if not args.llm_only:
                code = regex_extract((snippet or "") + " " + source)
                if code:
                    regex_hits += 1
                    log("REGEX_CONTENT_HIT", doc=doc_id[:8], code=code)
                    await apply_course_code(http, doc_id, code, args.dry_run)
                    continue

            code = await llm_infer_course(ai, filename, snippet or "")
            if code:
                llm_hits += 1
                log("LLM_HIT", doc=doc_id[:8], file=filename[:40], code=code)
                await apply_course_code(http, doc_id, code, args.dry_run)
            else:
                llm_null += 1
                if i < 5:
                    log("LLM_NULL", doc=doc_id[:8], file=filename[:30])

        log(
            "BACKFILL_DONE",
            total=len(docs),
            regex_hits=regex_hits,
            llm_hits=llm_hits,
            llm_null=llm_null,
            no_content=no_content,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    asyncio.run(main())
