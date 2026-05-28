#!/usr/bin/env python3
"""
ingest_document.py — Admin CLI to ingest a local file into the RUMMAN knowledge layer.

Usage:
    python3 scripts/ingest_document.py <file_path> \\
        --source-type exam \\
        --course-code MGT311 \\
        [--exam-type final] \\
        [--academic-year 2024] \\
        [--semester first] \\
        [--professor "Dr. Smith"] \\
        [--language ar] \\
        [--dry-run]

Source types: exam, study_plan, regulation, course_description, telegram_export, upload

What this does:
  1. Computes SHA256 of the file (dedup guard).
  2. Uploads to Supabase Storage bucket 'rumman-content'.
  3. Inserts a source_documents row (status=pending).
  4. Creates a processing_jobs row (job_type=pdf_extract) for pdf_worker.py.

Requires: SUPABASE_URL, SUPABASE_KEY env vars.
"""

import os
import sys
import hashlib
import argparse
import asyncio
import mimetypes
from pathlib import Path

from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
STORAGE_BUCKET = "rumman-content"
INSTITUTION = "SEU"

VALID_SOURCE_TYPES = ("exam", "study_plan", "regulation", "course_description", "telegram_export", "upload")
VALID_EXAM_TYPES = ("final", "midterm", "quiz")
VALID_SEMESTERS = ("first", "second", "summer")
VALID_LANGUAGES = ("ar", "en", "mixed")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}


def log(event: str, **kwargs):
    parts = [event] + [f"{k}={v}" for k, v in kwargs.items()]
    print(" | ".join(parts), flush=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


async def upload_to_storage(http: httpx.AsyncClient, file_path: Path, storage_path: str) -> bool:
    mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    with open(file_path, "rb") as f:
        data = f.read()

    r = await http.post(
        f"{SUPABASE_URL}/storage/v1/object/{STORAGE_BUCKET}/{storage_path}",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": mime,
        },
        content=data,
    )

    if r.status_code == 200:
        return True
    if r.status_code == 409:
        log("STORAGE_ALREADY_EXISTS", path=storage_path)
        return True

    log("STORAGE_UPLOAD_ERROR", status=r.status_code, error=r.text[:200])
    return False


async def insert_source_document(http: httpx.AsyncClient, row: dict) -> Optional[str]:
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/source_documents",
        headers=HEADERS,
        json=row,
    )
    if r.status_code == 409:
        log("DOCUMENT_DUPLICATE", content_hash=row["content_hash"])
        return "duplicate"
    if r.status_code >= 400:
        log("DOCUMENT_INSERT_ERROR", status=r.status_code, error=r.text[:200])
        return None
    rows = r.json()
    return rows[0]["id"] if rows else None


async def create_extract_job(http: httpx.AsyncClient, source_document_id: str, course_code: Optional[str]) -> bool:
    payload = {
        "job_type": "pdf_extract",
        "status": "pending",
        "payload": {
            "source_document_id": source_document_id,
            "course_code": course_code,
        },
        "retry_count": 0,
    }
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/processing_jobs",
        headers=HEADERS,
        json=payload,
    )
    if r.status_code >= 400:
        log("JOB_CREATE_ERROR", status=r.status_code, error=r.text[:200])
        return False
    return True


async def main():
    parser = argparse.ArgumentParser(description="Ingest a document into the RUMMAN knowledge layer")
    parser.add_argument("file_path", help="Path to the file to ingest")
    parser.add_argument("--source-type", required=True, choices=VALID_SOURCE_TYPES, help="Document source type")
    parser.add_argument("--course-code", default=None, help="Course code (e.g. MGT311)")
    parser.add_argument("--exam-type", default=None, choices=VALID_EXAM_TYPES, help="Exam type if source_type=exam")
    parser.add_argument("--academic-year", default=None, help="Academic year (e.g. 2024)")
    parser.add_argument("--semester", default=None, choices=VALID_SEMESTERS, help="Semester")
    parser.add_argument("--professor", default=None, help="Professor name")
    parser.add_argument("--language", default=None, choices=VALID_LANGUAGES, help="Document language")
    parser.add_argument("--institution", default=INSTITUTION, help="Institution code (default: SEU)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without writing to DB")
    args = parser.parse_args()

    file_path = Path(args.file_path)
    if not file_path.exists():
        print(f"ERROR: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    content_hash = sha256_file(file_path)
    file_size = file_path.stat().st_size
    file_name = file_path.name
    mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"

    folder = f"{args.institution}/{args.source_type}"
    if args.course_code:
        folder += f"/{args.course_code}"
    storage_path = f"{folder}/{content_hash[:8]}_{file_name}"

    log(
        "INGEST_PLAN",
        file=file_name,
        size_kb=f"{file_size // 1024}KB",
        content_hash=content_hash[:16] + "...",
        source_type=args.source_type,
        course_code=args.course_code,
        storage_path=storage_path,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        log("DRY_RUN_DONE")
        return

    async with httpx.AsyncClient(timeout=120) as http:
        log("UPLOADING_TO_STORAGE", bucket=STORAGE_BUCKET, path=storage_path)
        ok = await upload_to_storage(http, file_path, storage_path)
        if not ok:
            log("ABORT", reason="storage_upload_failed")
            sys.exit(1)

        doc_row = {
            "content_hash": content_hash,
            "storage_path": f"{STORAGE_BUCKET}/{storage_path}",
            "file_name": file_name,
            "mime_type": mime,
            "file_size_bytes": file_size,
            "source_type": args.source_type,
            "institution": args.institution,
            "course_code": args.course_code,
            "exam_type": args.exam_type,
            "academic_year": args.academic_year,
            "semester": args.semester,
            "professor": args.professor,
            "language": args.language,
            "processing_status": "pending",
        }

        log("INSERTING_DOCUMENT")
        doc_id = await insert_source_document(http, doc_row)

        if doc_id == "duplicate":
            log("DONE", status="already_ingested", content_hash=content_hash[:16])
            return

        if not doc_id:
            log("ABORT", reason="document_insert_failed")
            sys.exit(1)

        log("DOCUMENT_CREATED", id=doc_id)

        log("CREATING_EXTRACT_JOB")
        ok = await create_extract_job(http, doc_id, args.course_code)
        if not ok:
            log("WARNING", reason="job_create_failed_but_document_inserted", doc_id=doc_id)
            sys.exit(1)

        log("INGEST_DONE", doc_id=doc_id, next_step="pdf_worker.py will pick up job_type=pdf_extract")


if __name__ == "__main__":
    asyncio.run(main())
