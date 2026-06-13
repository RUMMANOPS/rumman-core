#!/usr/bin/env python3
"""
syllabus_parser_worker.py — Parse course syllabi → kg_syllabi + kg_chapters.

WHY THIS EXISTS:
  chapter_numbers in exam_questions is NULL because we have no Chapter entity.
  This worker creates the Chapter spine by parsing official course syllabi.
  Without this, "show questions from chapters 1-5 only" is impossible.

TWO MODES:

  Mode A — Bootstrap (local files, run once):
    Reads DOCX/PDF directly from the university knowledge repository.
    Use: SYLLABUS_BOOTSTRAP_DIR=/path/to/university/4.CourseContent
    Recursively finds .docx and .pdf files, extracts text, parses chapters.

  Mode B — Live (from Supabase source_documents):
    Polls source_documents WHERE source_type IN ('course_description','study_plan')
    AND extracted_text IS NOT NULL AND syllabus_id IS NULL.
    Triggered automatically when new syllabi are ingested via ingest_document.py.

ENABLE: SYLLABUS_PARSER_WORKER_ENABLED=true
        SYLLABUS_BOOTSTRAP_DIR=/path/to/dir  (for Mode A)
"""
from __future__ import annotations

import os
import re
import json
import asyncio
from pathlib import Path
from typing import Optional

import httpx
import fitz          # PyMuPDF
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL   = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY   = os.environ.get("SUPABASE_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
TENANT_ID      = "00000000-0000-0000-0000-000000000001"

_ENABLED      = os.getenv("SYLLABUS_PARSER_WORKER_ENABLED", "").strip().lower() == "true"
_BOOTSTRAP_DIR = os.getenv("SYLLABUS_BOOTSTRAP_DIR", "")
SLEEP_SECONDS  = int(os.getenv("SYLLABUS_PARSER_SLEEP_SECONDS", "300"))
PARSE_MODEL    = "gpt-4o"

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

# Course code extraction — English (IT353) and Arabic (قنن315) patterns
_COURSE_CODE_RE    = re.compile(r'\b([A-Z]{2,6}\d{3,4})\b')
_AR_COURSE_CODE_RE = re.compile(r'([؀-ۿ]{2,6}\d{3,4})')

_PARSE_SYSTEM = """\
You are an academic syllabus analyst for Saudi Electronic University (SEU).

Extract the complete chapter structure from the provided course syllabus text.
The text may be Arabic, English, or mixed. Be tolerant of formatting variation.

For each chapter/unit/week return:
- chapter_number: integer starting from 1
- chapter_title: exact title as written (English if available)
- chapter_title_ar: Arabic title if available, null otherwise
- topics_raw: list of specific topics covered (exact phrases from document)
- learning_outcomes: list of learning objectives for this chapter (exact phrases)
- week_start: starting week number (integer), null if not specified
- week_end: ending week number (integer), null if not specified

Also return:
- course_code: SEU course code (e.g. "IT353"), null if not identifiable
- academic_year: e.g. "2025-2026" or "1447", null if not found
- total_chapters: integer count of chapters extracted

Rules:
- Include EVERY chapter/unit/module, even if minimally described
- If chapters are labeled as "Week 1", "Unit 2", "Chapter 3", "الفصل الأول" — all count
- topic_raw items should be specific concepts, not generic descriptions
- An empty chapters array means no chapter structure was found (document is not a syllabus)

Return ONLY valid JSON — no text outside JSON:
{
  "course_code": "IT353",
  "academic_year": "2025-2026",
  "total_chapters": 7,
  "chapters": [
    {
      "chapter_number": 1,
      "chapter_title": "Introduction to Computer Networks",
      "chapter_title_ar": "مقدمة في شبكات الحاسب",
      "topics_raw": ["OSI Model", "Network Types", "Protocols"],
      "learning_outcomes": ["Define computer network", "Explain OSI layers"],
      "week_start": 1,
      "week_end": 2
    }
  ]
}
"""


def log(event: str, **kwargs):
    parts = [event] + [f"{k}={v}" for k, v in kwargs.items()]
    print(" | ".join(parts), flush=True)


def _extract_text_from_pdf(path: Path) -> str:
    """Extract text from PDF using PyMuPDF."""
    try:
        doc = fitz.open(str(path))
        pages = []
        for page in doc:
            pages.append(page.get_text())
        doc.close()
        return "\n".join(pages)
    except Exception as e:
        log("PDF_EXTRACT_ERROR", path=str(path)[-60:], error=str(e)[:80])
        return ""


def _extract_text_from_docx(path: Path) -> str:
    """Extract text from DOCX using python-docx if available, else skip."""
    try:
        import docx  # type: ignore
        doc = docx.Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except ImportError:
        log("DOCX_SKIP", reason="python-docx not installed", path=str(path)[-60:])
        return ""
    except Exception as e:
        log("DOCX_EXTRACT_ERROR", path=str(path)[-60:], error=str(e)[:80])
        return ""


def _infer_course_code(text: str, filename: str) -> Optional[str]:
    """Try to extract course code from filename or text (English then Arabic)."""
    for candidate in [filename, text[:500]]:
        m = _COURSE_CODE_RE.search(candidate)
        if m:
            return m.group(1)
    # Fallback: Arabic course codes like قنن315, نظم201
    for candidate in [filename, text[:500]]:
        m = _AR_COURSE_CODE_RE.search(candidate)
        if m:
            return m.group(1)
    return None


async def _parse_syllabus(client: AsyncOpenAI, text: str, filename: str) -> Optional[dict]:
    """Call GPT-4o to extract chapter structure from syllabus text."""
    text_truncated = text[:15_000]
    try:
        resp = await client.chat.completions.create(
            model=PARSE_MODEL,
            messages=[
                {"role": "system", "content": _PARSE_SYSTEM},
                {"role": "user",   "content": f"Filename: {filename}\n\n---\n{text_truncated}"},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
            max_tokens=8192,
        )
        raw = resp.choices[0].message.content or "{}"
        result = json.loads(raw)
        result["_usage"] = {
            "input_tokens":  resp.usage.prompt_tokens,
            "output_tokens": resp.usage.completion_tokens,
        }
        return result
    except json.JSONDecodeError as e:
        log("PARSE_JSON_ERROR", file=filename[:60], error=str(e))
        return None
    except Exception as e:
        log("PARSE_API_ERROR", file=filename[:60], error=str(e)[:200])
        return None


async def _store_syllabus(http: httpx.AsyncClient, course_code: str, parsed: dict,
                           raw_text: str, source_doc_id: Optional[str]) -> Optional[str]:
    """Store kg_syllabi row, return syllabus id."""
    payload = {
        "tenant_id":          TENANT_ID,
        "course_code":        course_code,
        "academic_year":      parsed.get("academic_year"),
        "total_chapters":     parsed.get("total_chapters"),
        "is_current":         True,
        "parsing_confidence": min(1.0, len(parsed.get("chapters", [])) / max(parsed.get("total_chapters", 1), 1)),
        "raw_text":           raw_text[:50_000],   # cap storage
        "source_doc_id":      source_doc_id,
        "source_type":        "official",
    }
    resp = await http.post(
        f"{SUPABASE_URL}/rest/v1/kg_syllabi",
        headers=HEADERS,
        content=json.dumps(payload),
    )
    if resp.status_code in (200, 201):
        data = resp.json()
        return data[0]["id"] if isinstance(data, list) else data.get("id")
    log("STORE_SYLLABUS_ERROR", status=resp.status_code, body=resp.text[:200])
    return None


async def _store_chapters(http: httpx.AsyncClient, syllabus_id: str,
                           course_code: str, chapters: list[dict]) -> int:
    """Upsert kg_chapters rows, return count stored."""
    if not chapters:
        return 0

    rows = []
    for ch in chapters:
        rows.append({
            "tenant_id":      TENANT_ID,
            "syllabus_id":    syllabus_id,
            "course_code":    course_code,
            "chapter_number": ch.get("chapter_number", 0),
            "chapter_title":  ch.get("chapter_title"),
            "chapter_title_ar": ch.get("chapter_title_ar"),
            "topics_raw":     ch.get("topics_raw") or [],
            "learning_outcomes": ch.get("learning_outcomes") or [],
            "week_start":     ch.get("week_start"),
            "week_end":       ch.get("week_end"),
            "confidence":     0.85,   # parsed from official document
        })

    resp = await http.post(
        f"{SUPABASE_URL}/rest/v1/kg_chapters"
        f"?on_conflict=course_code,chapter_number,syllabus_id",
        headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
        content=json.dumps(rows),
    )
    if resp.status_code in (200, 201, 204):
        return len(rows)
    log("STORE_CHAPTERS_ERROR", status=resp.status_code, body=resp.text[:200])
    return 0


async def process_local_file(client: AsyncOpenAI, http: httpx.AsyncClient, path: Path) -> bool:
    """Process a single local DOCX or PDF syllabus file."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = _extract_text_from_pdf(path)
    elif suffix in (".docx", ".doc"):
        text = _extract_text_from_docx(path)
    else:
        return False

    if not text or len(text) < 200:
        log("SKIP_EMPTY", path=str(path)[-60:])
        return False

    course_code = _infer_course_code(text, path.name)
    if not course_code:
        log("SKIP_NO_COURSE_CODE", path=str(path)[-60:])
        return False

    log("PARSING", course=course_code, file=path.name[:60])

    parsed = await _parse_syllabus(client, text, path.name)
    if not parsed or not parsed.get("chapters"):
        log("NO_CHAPTERS", course=course_code, file=path.name[:60])
        return False

    # Trust filename-inferred code over GPT output (OCR artifacts corrupt Arabic codes)
    actual_code = course_code or parsed.get("course_code")
    syllabus_id = await _store_syllabus(http, actual_code, parsed, text, None)
    if not syllabus_id:
        return False

    chapters_stored = await _store_chapters(http, syllabus_id, actual_code, parsed["chapters"])
    log("DONE", course=actual_code, chapters=chapters_stored,
        tokens=parsed.get("_usage", {}).get("input_tokens", 0))
    return True


async def _fetch_pending_source_docs(http: httpx.AsyncClient) -> list[dict]:
    """Fetch source_documents with extracted text, not yet parsed as syllabi."""
    resp = await http.get(
        f"{SUPABASE_URL}/rest/v1/source_documents",
        headers=HEADERS,
        params={
            "select":          "id,file_name,course_code,extracted_text",
            "source_type":     "in.(course_description,study_plan)",
            "extracted_text":  "not.is.null",
            "processing_status": "eq.chunked",
            "limit":           "10",
        },
    )
    if resp.status_code == 200:
        return [d for d in resp.json() if d.get("extracted_text")]
    return []


async def run_bootstrap(client: AsyncOpenAI, http: httpx.AsyncClient, bootstrap_dir: str):
    """Mode A: process all DOCX/PDF files in a directory tree."""
    base = Path(bootstrap_dir)
    if not base.exists():
        log("BOOTSTRAP_DIR_NOT_FOUND", path=bootstrap_dir)
        return

    files = list(base.rglob("*.pdf")) + list(base.rglob("*.docx")) + list(base.rglob("*.doc"))
    log("BOOTSTRAP_START", files_found=len(files), dir=bootstrap_dir[-60:])

    success = 0
    for f in files:
        ok = await process_local_file(client, http, f)
        if ok:
            success += 1

    log("BOOTSTRAP_DONE", processed=success, total=len(files))


async def run_live(client: AsyncOpenAI, http: httpx.AsyncClient):
    """Mode B: poll source_documents for un-parsed syllabi."""
    log("LIVE_START", sleep_seconds=SLEEP_SECONDS)
    while True:
        docs = await _fetch_pending_source_docs(http)
        if not docs:
            await asyncio.sleep(SLEEP_SECONDS)
            continue

        for doc in docs:
            text = doc.get("extracted_text", "")
            course_code = doc.get("course_code") or _infer_course_code(text, doc.get("file_name", ""))
            if not course_code:
                continue

            parsed = await _parse_syllabus(client, text, doc.get("file_name", "unknown"))
            if not parsed or not parsed.get("chapters"):
                continue

            syllabus_id = await _store_syllabus(http, course_code, parsed, text, doc["id"])
            if syllabus_id:
                chapters_stored = await _store_chapters(http, syllabus_id, course_code, parsed["chapters"])
                log("LIVE_DONE", course=course_code, chapters=chapters_stored)


async def main():
    if not _ENABLED:
        print("DISABLED — set SYLLABUS_PARSER_WORKER_ENABLED=true", flush=True)
        return

    log("START", model=PARSE_MODEL, bootstrap_dir=_BOOTSTRAP_DIR or "none")

    client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    async with httpx.AsyncClient(timeout=60) as http:
        if _BOOTSTRAP_DIR:
            await run_bootstrap(client, http, _BOOTSTRAP_DIR)
        await run_live(client, http)


if __name__ == "__main__":
    asyncio.run(main())
