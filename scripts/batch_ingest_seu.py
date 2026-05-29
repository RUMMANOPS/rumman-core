#!/usr/bin/env python3
"""
batch_ingest_seu.py — Batch-ingest all SEU knowledge repository documents.

Usage:
    python3 scripts/batch_ingest_seu.py [--dry-run] [--priority-only]

Priority ordering (highest impact first):
  1. Regulations + AcademicCalendar  → source_type=regulation
  2. StudyPlans                      → source_type=study_plan
  3. CourseContent                   → source_type=course_description
  4. Diplomas                        → source_type=study_plan
  5. OpenData                        → source_type=upload

After ingestion, pdf_worker.py must be run to extract text and trigger chunking.

Requires: SUPABASE_URL, SUPABASE_KEY, SEU_REPO_PATH env vars (or defaults below).
"""

import os
import re
import sys
import hashlib
import asyncio
import mimetypes
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
STORAGE_BUCKET = "rumman-content"
INSTITUTION = "SEU"
SEU_TENANT_ID = "00000000-0000-0000-0000-000000000001"

# Default: sibling directory of the repo
_DEFAULT_REPO = (
    Path(__file__).parent.parent.parent.parent.parent
    / "0-Universities"
    / "1- Saudi Electronic University"
)
SEU_REPO_PATH = Path(os.environ.get("SEU_REPO_PATH", str(_DEFAULT_REPO)))

SUPPORTED_EXTS = {".pdf", ".docx", ".txt", ".PDF"}

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}


@dataclass
class IngestTarget:
    path: Path
    source_type: str
    course_code: Optional[str] = None
    language: Optional[str] = None


@dataclass
class RunStats:
    total: int = 0
    ingested: int = 0
    duplicate: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list = field(default_factory=list)


def log(event: str, **kwargs):
    parts = [event] + [f"{k}={v}" for k, v in kwargs.items()]
    print(" | ".join(parts), flush=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ── Course code extraction ────────────────────────────────────────────────────
# Match patterns like: IT488, MGT311, قنن121, CS101, ACCT201
_COURSE_CODE_RE = re.compile(
    r"\b([A-Z]{2,6}\d{3}[A-Z]?)\b"           # Latin codes: IT488, ACCT201L
    r"|([^\W\d_]{2,4}\d{3})\b",               # Arabic prefix + 3 digits: قنن121
    re.UNICODE,
)

def extract_course_code(path: Path) -> Optional[str]:
    name = path.stem
    m = _COURSE_CODE_RE.search(name)
    if m:
        return m.group(1) or m.group(2)
    # Try parent directory names (CourseContent/college/dept/IT488-syllabus.pdf)
    for part in reversed(path.parts[-4:-1]):
        m = _COURSE_CODE_RE.search(part)
        if m:
            return m.group(1) or m.group(2)
    return None


def detect_language(path: Path) -> Optional[str]:
    """Heuristic: if filename/path contains Arabic text, likely Arabic doc."""
    text = str(path)
    arabic_chars = sum(1 for c in text if "؀" <= c <= "ۿ")
    if arabic_chars > 3:
        return "ar"
    return None


# ── Directory → source_type mapping ──────────────────────────────────────────

_DIR_RULES: list[tuple[str, str]] = [
    ("2. Regulations",      "regulation"),
    ("3. AcademicCalendar", "regulation"),
    ("1. StudyPlans",       "study_plan"),
    ("4. CourseContent",    "course_description"),
    ("5. Diplomas",         "study_plan"),
    ("0. OpenData",         "upload"),
]

# Priority order for batch processing (index = priority, lower = higher priority)
_PRIORITY_ORDER = [
    "2. Regulations",
    "3. AcademicCalendar",
    "1. StudyPlans",
    "4. CourseContent",
    "5. Diplomas",
    "0. OpenData",
]


def classify_file(path: Path) -> Optional[IngestTarget]:
    """Return an IngestTarget for path, or None if it should be skipped."""
    if path.suffix.lower() not in {".pdf", ".docx", ".txt"}:
        return None

    relative = path.relative_to(SEU_REPO_PATH)
    top_dir = relative.parts[0] if relative.parts else ""

    source_type = None
    for dir_prefix, stype in _DIR_RULES:
        if top_dir.startswith(dir_prefix):
            source_type = stype
            break

    if source_type is None:
        return None  # unknown directory — skip

    course_code = None
    if source_type in ("course_description", "study_plan"):
        course_code = extract_course_code(path)

    return IngestTarget(
        path=path,
        source_type=source_type,
        course_code=course_code,
        language=detect_language(path),
    )


def collect_targets(priority_only: bool = False) -> list[IngestTarget]:
    """Walk SEU_REPO_PATH in priority order, return classified IngestTargets."""
    targets: list[IngestTarget] = []
    ordered_dirs = _PRIORITY_ORDER if not priority_only else _PRIORITY_ORDER[:3]

    for dir_name in ordered_dirs:
        dir_path = SEU_REPO_PATH / dir_name
        if not dir_path.exists():
            log("DIR_NOT_FOUND", dir=dir_name)
            continue
        for path in sorted(dir_path.rglob("*")):
            if not path.is_file():
                continue
            target = classify_file(path)
            if target:
                targets.append(target)

    return targets


# ── Supabase I/O ──────────────────────────────────────────────────────────────

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
        timeout=120,
    )

    if r.status_code in (200, 201):
        return True
    if r.status_code == 409:
        return True  # already exists — idempotent

    log("STORAGE_UPLOAD_ERROR", status=r.status_code, file=file_path.name, error=r.text[:120])
    return False


async def insert_source_document(http: httpx.AsyncClient, row: dict) -> Optional[str]:
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/source_documents",
        headers=HEADERS,
        json=row,
        timeout=30,
    )
    if r.status_code == 409:
        return "duplicate"
    if r.status_code >= 400:
        log("DOCUMENT_INSERT_ERROR", status=r.status_code, error=r.text[:120])
        return None
    rows = r.json()
    return rows[0]["id"] if rows else None


async def create_extract_job(http: httpx.AsyncClient, source_document_id: str, course_code: Optional[str]) -> bool:
    payload = {
        "job_type": "pdf_extract",
        "status": "pending",
        "target_key": source_document_id,
        "target_table": "source_documents",
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
        timeout=30,
    )
    if r.status_code >= 400:
        log("JOB_CREATE_ERROR", status=r.status_code, error=r.text[:120])
        return False
    return True


# ── Main ──────────────────────────────────────────────────────────────────────

async def ingest_one(http: httpx.AsyncClient, target: IngestTarget, stats: RunStats, dry_run: bool):
    stats.total += 1
    path = target.path
    file_name = path.name

    try:
        content_hash = sha256_file(path)
        file_size = path.stat().st_size
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    except Exception as e:
        log("FILE_READ_ERROR", file=file_name, error=str(e))
        stats.failed += 1
        stats.errors.append(f"{file_name}: {e}")
        return

    folder = f"{INSTITUTION}/{target.source_type}"
    if target.course_code:
        folder += f"/{target.course_code}"
    storage_path = f"{folder}/{content_hash[:8]}_{file_name}"

    if dry_run:
        log(
            "DRY_RUN",
            file=file_name,
            source_type=target.source_type,
            course_code=target.course_code or "-",
            size_kb=f"{file_size // 1024}KB",
            storage_path=storage_path,
        )
        stats.ingested += 1
        return

    ok = await upload_to_storage(http, path, storage_path)
    if not ok:
        stats.failed += 1
        stats.errors.append(f"{file_name}: storage upload failed")
        return

    doc_row = {
        "content_hash":     content_hash,
        "storage_path":     f"{STORAGE_BUCKET}/{storage_path}",
        "file_name":        file_name,
        "mime_type":        mime,
        "file_size_bytes":  file_size,
        "source_type":      target.source_type,
        "institution":      INSTITUTION,
        "course_code":      target.course_code,
        "language":         target.language,
        "processing_status": "pending",
        "tenant_id":        SEU_TENANT_ID,
    }

    doc_id = await insert_source_document(http, doc_row)

    if doc_id == "duplicate":
        log("DUPLICATE", file=file_name, hash=content_hash[:12])
        stats.duplicate += 1
        return

    if not doc_id:
        stats.failed += 1
        stats.errors.append(f"{file_name}: document insert failed")
        return

    ok = await create_extract_job(http, doc_id, target.course_code)
    if not ok:
        log("JOB_FAILED", file=file_name, doc_id=doc_id)
        stats.failed += 1
        stats.errors.append(f"{file_name}: extract job creation failed (doc inserted: {doc_id})")
        return

    log(
        "INGESTED",
        file=file_name,
        source_type=target.source_type,
        course_code=target.course_code or "-",
        doc_id=doc_id,
    )
    stats.ingested += 1


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Batch-ingest SEU knowledge repository")
    parser.add_argument("--dry-run", action="store_true", help="Show plan without writing to DB")
    parser.add_argument("--priority-only", action="store_true",
                        help="Only ingest Regulations, Calendar, StudyPlans (skip CourseContent + OpenData)")
    args = parser.parse_args()

    if not SEU_REPO_PATH.exists():
        print(f"ERROR: SEU_REPO_PATH not found: {SEU_REPO_PATH}", file=sys.stderr)
        print("Set SEU_REPO_PATH env var or ensure default path exists.", file=sys.stderr)
        sys.exit(1)

    log("BATCH_START", repo=str(SEU_REPO_PATH), dry_run=args.dry_run, priority_only=args.priority_only)

    targets = collect_targets(priority_only=args.priority_only)

    # Count by type for the plan summary
    by_type: dict[str, int] = {}
    for t in targets:
        by_type[t.source_type] = by_type.get(t.source_type, 0) + 1

    log("PLAN", total=len(targets), **{k: v for k, v in sorted(by_type.items())})

    if args.dry_run:
        for t in targets:
            stats = RunStats()
            await ingest_one(None, t, stats, dry_run=True)
        log("DRY_RUN_COMPLETE", total=len(targets))
        return

    stats = RunStats()
    # Single client for entire batch — connection pool reuse
    async with httpx.AsyncClient(timeout=120) as http:
        for target in targets:
            await ingest_one(http, target, stats, dry_run=False)

    log(
        "BATCH_DONE",
        total=stats.total,
        ingested=stats.ingested,
        duplicate=stats.duplicate,
        failed=stats.failed,
    )

    if stats.errors:
        print("\nFailed files:", flush=True)
        for e in stats.errors:
            print(f"  - {e}", flush=True)

    sys.exit(1 if stats.failed > 0 else 0)


if __name__ == "__main__":
    asyncio.run(main())
