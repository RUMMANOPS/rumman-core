#!/usr/bin/env python3
"""
media_worker.py — Telegram media extraction worker (privacy-safe).

Handles job_type='telegram_media'. For each job:
  1. Downloads the file from Telegram via Telethon (temp dir only — never to Storage).
  2. Extracts text:
       PDF with digital text → PyMuPDF
       PDF image-only / photo / screenshot → GPT-4o Vision (OCR)
  3. Creates a source_documents row (storage_path=NULL — no raw file stored).
  4. Queues an embed_chunk job for embed_worker.py.
  5. Temp file is gone when the TemporaryDirectory context exits.

Privacy contract:
  - Raw files never touch Supabase Storage.
  - source_documents carries NO sender identity — only course_code, source_type,
    institution, and chat metadata (anonymized chat name).
  - document_chunks inherits this: no user attribution, no source group exposure.

Requires: supabase/migrations/003_knowledge_layer.sql + 004_media_lifecycle.sql applied.
Add to Procfile as 'media' process once backfill jobs exist at scale.
"""

import os
import re
import asyncio
import base64
import tempfile
from pathlib import Path
from typing import Optional

import httpx
import fitz  # PyMuPDF
from dotenv import load_dotenv
from openai import AsyncOpenAI
from telethon import TelegramClient
from telethon.sessions import StringSession

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

MAX_RETRIES = 5
SLEEP_SECONDS = 5
INSTITUTION = "SEU"

# GPT-4o Vision is used for image OCR and image-only PDFs
VISION_MODEL = "gpt-4o"
# Max pages to send to Vision per PDF (cost control)
MAX_VISION_PAGES = 10

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

# Course code pattern for auto-detection from captions/file names
COURSE_CODE_RE = re.compile(
    r'\b([A-Z]{2,6}\s*\d{3,4})\b',
    re.IGNORECASE,
)

EXAM_KEYWORDS = re.compile(
    r'\b(final|midterm|quiz|exam|اختبار|نهائي|منتصف|كويز)\b',
    re.IGNORECASE,
)


def log(event: str, **kwargs):
    parts = [event] + [f"{k}={v}" for k, v in kwargs.items()]
    print(" | ".join(parts), flush=True)


def detect_course_code(text: str) -> Optional[str]:
    if not text:
        return None
    m = COURSE_CODE_RE.search(text)
    return m.group(1).upper().replace(" ", "") if m else None


def detect_exam_type(text: str) -> Optional[str]:
    if not text:
        return None
    t = text.lower()
    if any(k in t for k in ("final", "نهائي")):
        return "final"
    if any(k in t for k in ("midterm", "mid", "منتصف")):
        return "midterm"
    if any(k in t for k in ("quiz", "كويز")):
        return "quiz"
    return None


def detect_language(text: str) -> Optional[str]:
    if not text:
        return None
    arabic = sum(1 for c in text if '؀' <= c <= 'ۿ')
    latin = sum(1 for c in text if c.isalpha() and c.isascii())
    if arabic == 0 and latin == 0:
        return None
    if arabic > latin * 2:
        return "ar"
    if latin > arabic * 2:
        return "en"
    return "mixed"


async def get_jobs(http: httpx.AsyncClient) -> list[dict]:
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/processing_jobs",
        headers=HEADERS,
        params={
            "job_type": "eq.telegram_media",
            "status": "in.(pending,failed)",
            "retry_count": f"lt.{MAX_RETRIES}",
            "limit": "5",
            "order": "created_at.asc",
        },
    )
    r.raise_for_status()
    return r.json()


async def update_job(http: httpx.AsyncClient, job_id: str, status: str,
                     result=None, error=None, retry_count=None):
    payload = {"status": status}
    if result is not None:
        payload["result"] = result
    if error is not None:
        payload["error"] = error
    if retry_count is not None:
        payload["retry_count"] = retry_count
    r = await http.patch(
        f"{SUPABASE_URL}/rest/v1/processing_jobs?id=eq.{job_id}",
        headers=HEADERS,
        json=payload,
    )
    if r.status_code >= 400:
        log("UPDATE_JOB_ERROR", id=job_id, status=r.status_code, error=r.text[:120])


async def create_source_document(http: httpx.AsyncClient, row: dict) -> Optional[str]:
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/source_documents",
        headers=HEADERS,
        json=row,
    )
    if r.status_code == 409:
        log("SOURCE_DOC_DUPLICATE", content_hash=row.get("content_hash", "")[:16])
        rows = await http.get(
            f"{SUPABASE_URL}/rest/v1/source_documents",
            headers=HEADERS,
            params={"content_hash": f"eq.{row['content_hash']}", "limit": "1"},
        )
        existing = rows.json()
        return existing[0]["id"] if existing else None
    if r.status_code >= 400:
        log("SOURCE_DOC_CREATE_ERROR", status=r.status_code, error=r.text[:200])
        return None
    rows = r.json()
    return rows[0]["id"] if rows else None


async def update_source_document(http: httpx.AsyncClient, doc_id: str, updates: dict):
    r = await http.patch(
        f"{SUPABASE_URL}/rest/v1/source_documents?id=eq.{doc_id}",
        headers=HEADERS,
        json={"updated_at": "now()", **updates},
    )
    if r.status_code >= 400:
        log("UPDATE_DOC_ERROR", id=doc_id, status=r.status_code, error=r.text[:120])


async def create_embed_job(http: httpx.AsyncClient, source_document_id: str) -> bool:
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/processing_jobs",
        headers=HEADERS,
        json={
            "job_type": "embed_chunk",
            "status": "pending",
            "payload": {"source_document_id": source_document_id},
            "retry_count": 0,
        },
    )
    if r.status_code >= 400:
        log("EMBED_JOB_CREATE_ERROR", status=r.status_code, error=r.text[:120])
        return False
    return True


def extract_pdf_text(file_path: str) -> tuple[str, int, str, float]:
    """Returns (text, page_count, method, confidence). method: digital|mixed|needs_ocr"""
    doc = fitz.open(file_path)
    page_count = len(doc)
    pages_text = []
    digital = 0
    image_only = 0

    for page in doc:
        text = page.get_text().strip()
        if text:
            pages_text.append(text)
            digital += 1
        else:
            pages_text.append(f"[PAGE {page.number + 1}: image-only]")
            image_only += 1

    doc.close()

    if image_only == 0:
        return "\n\n".join(pages_text), page_count, "digital", 1.0
    if digital == 0:
        return "\n\n".join(pages_text), page_count, "needs_ocr", 0.0
    confidence = digital / page_count
    return "\n\n".join(pages_text), page_count, "mixed", confidence


async def ocr_with_vision(ai: AsyncOpenAI, file_path: str, hint: str = "") -> str:
    """
    Uses GPT-4o Vision to extract text from an image or image-only PDF.
    For PDFs, converts pages to PNG and sends up to MAX_VISION_PAGES.
    """
    path = Path(file_path)
    mime = "image/jpeg"
    images_b64 = []

    if path.suffix.lower() == ".pdf":
        doc = fitz.open(file_path)
        pages_to_ocr = min(len(doc), MAX_VISION_PAGES)
        for i in range(pages_to_ocr):
            page = doc[i]
            pix = page.get_pixmap(dpi=150)
            img_bytes = pix.tobytes("png")
            images_b64.append(base64.b64encode(img_bytes).decode())
        doc.close()
        mime = "image/png"
    else:
        with open(file_path, "rb") as f:
            raw = f.read()
        images_b64 = [base64.b64encode(raw).decode()]
        ext = path.suffix.lower()
        if ext in (".jpg", ".jpeg"):
            mime = "image/jpeg"
        elif ext == ".png":
            mime = "image/png"
        elif ext == ".webp":
            mime = "image/webp"

    content = []
    system_hint = (
        "Extract all text from this academic document. "
        "Preserve structure: question numbers, answer choices, tables. "
        "If the document is in Arabic, output Arabic text faithfully. "
        "Output only the extracted text, no commentary."
    )
    if hint:
        system_hint += f" Context: {hint}"

    content.append({"type": "text", "text": system_hint})
    for b64 in images_b64:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "high"},
        })

    response = await ai.chat.completions.create(
        model=VISION_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": content}],
    )
    return response.choices[0].message.content or ""


async def process_job(tg: TelegramClient, ai: AsyncOpenAI,
                      http: httpx.AsyncClient, job: dict):
    job_id = job["id"]
    payload = job.get("payload") or {}
    retry_count = job.get("retry_count") or 0

    platform_chat_id = payload.get("platform_chat_id")
    platform_message_id = payload.get("platform_message_id")
    media_type = payload.get("media_type", "unknown")  # "pdf" | "image"
    file_name = payload.get("file_name") or "unknown"
    mime_type = payload.get("mime_type") or ""
    caption = payload.get("caption") or ""

    # Metadata hints from the message
    course_code = payload.get("course_code") or detect_course_code(caption) or detect_course_code(file_name)
    exam_type = payload.get("exam_type") or detect_exam_type(caption) or detect_exam_type(file_name)
    academic_year = payload.get("academic_year")
    semester = payload.get("semester")
    source_type = "exam" if exam_type else ("study_plan" if "plan" in file_name.lower() else "upload")

    if not platform_chat_id or not platform_message_id:
        log("INVALID_JOB_PAYLOAD", id=job_id)
        await update_job(http, job_id, "failed", error="missing platform ids", retry_count=retry_count + 1)
        return

    log("JOB_START", id=job_id, chat_id=platform_chat_id, msg_id=platform_message_id,
        media_type=media_type, file=file_name, attempt=retry_count + 1)

    try:
        await update_job(http, job_id, "processing")

        entity = await tg.get_entity(int(platform_chat_id))
        msg = await tg.get_messages(entity, ids=int(platform_message_id))

        if not msg:
            raise RuntimeError("Telegram message not found")

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = await tg.download_media(msg, file=tmpdir)
            if not file_path:
                raise RuntimeError("Telegram download failed")

            file_path = str(file_path)
            log("DOWNLOADED", id=job_id, path=file_path)

            # --- Extract text ---
            extracted_text = ""
            page_count = None
            extraction_method = None
            ocr_confidence = None

            if media_type == "pdf" or file_path.lower().endswith(".pdf"):
                text, pages, method, conf = extract_pdf_text(file_path)
                page_count = pages
                ocr_confidence = conf if conf < 1.0 else None

                if method in ("digital", "mixed"):
                    extracted_text = text
                    extraction_method = method
                    if method == "mixed":
                        # Vision-fill image-only pages
                        ocr_text = await ocr_with_vision(
                            ai, file_path,
                            hint=f"Course: {course_code or 'unknown'}, type: {exam_type or 'unknown'}"
                        )
                        extracted_text = f"{text}\n\n[OCR PAGES]\n{ocr_text}"
                        extraction_method = "mixed"
                else:
                    # Fully image-based PDF
                    log("FULL_OCR", id=job_id, pages=pages)
                    extracted_text = await ocr_with_vision(
                        ai, file_path,
                        hint=f"Course: {course_code or 'unknown'}, type: {exam_type or 'unknown'}"
                    )
                    extraction_method = "ocr_vision"
                    ocr_confidence = None  # GPT-4o Vision doesn't give a confidence score

            elif media_type == "image" or any(
                file_path.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")
            ):
                log("IMAGE_OCR", id=job_id)
                extracted_text = await ocr_with_vision(
                    ai, file_path,
                    hint=f"Course: {course_code or 'unknown'}, type: {exam_type or 'unknown'}"
                )
                extraction_method = "ocr_vision"
                page_count = 1
            else:
                raise RuntimeError(f"Unsupported media type for OCR: {file_path}")

            # Detect language from extracted text if not known
            language = detect_language(extracted_text)

            char_count = len(extracted_text)
            log("EXTRACTED", id=job_id, method=extraction_method, chars=char_count)

            # Compute a content hash from the extracted text (not the raw file)
            import hashlib
            content_hash = hashlib.sha256(extracted_text.encode()).hexdigest()

        # Temp dir exits here — raw file is gone

        # Create source_documents row (storage_path=NULL — raw file was never stored)
        doc_row = {
            "content_hash": content_hash,
            "storage_path": None,          # raw file never stored
            "raw_file_deleted_at": "now()", # conceptually deleted (was only in temp)
            "file_name": file_name,
            "mime_type": mime_type,
            "source_type": source_type,
            "institution": INSTITUTION,
            "course_code": course_code,
            "exam_type": exam_type,
            "academic_year": academic_year,
            "semester": semester,
            "language": language,
            "page_count": page_count,
            "extraction_method": extraction_method,
            "extracted_text": extracted_text,
            "ocr_confidence": ocr_confidence,
            "processing_status": "extracted",
        }

        doc_id = await create_source_document(http, doc_row)
        if not doc_id:
            raise RuntimeError("Failed to create source_document row")

        log("SOURCE_DOC_CREATED", id=job_id, doc_id=doc_id, course=course_code)

        ok = await create_embed_job(http, doc_id)
        if not ok:
            log("WARNING", id=job_id, msg="embed job failed — extracted text saved but not queued")

        await update_job(http, job_id, "processed",
                         result={"doc_id": doc_id, "chars": char_count,
                                 "method": extraction_method, "course": course_code})

        log("JOB_DONE", id=job_id, doc_id=doc_id)

    except Exception as e:
        new_retry = retry_count + 1
        abandoned = new_retry >= MAX_RETRIES
        log("JOB_FAILED", id=job_id, attempt=new_retry, abandoned=abandoned, error=str(e)[:120])
        await update_job(http, job_id, "failed", error=str(e), retry_count=new_retry)


async def main():
    log("MEDIA_WORKER_START", max_retries=MAX_RETRIES, vision_model=VISION_MODEL)

    tg = TelegramClient(
        StringSession(os.environ["TELEGRAM_SESSION_STRING"]),
        int(os.environ["TELEGRAM_API_ID"]),
        os.environ["TELEGRAM_API_HASH"],
    )

    await asyncio.wait_for(tg.connect(), timeout=30)
    if not await tg.is_user_authorized():
        log("SESSION_NOT_AUTHORIZED")
        return

    me = await tg.get_me()
    log("MEDIA_WORKER_READY", user_id=me.id)

    ai = AsyncOpenAI(api_key=OPENAI_API_KEY)

    async with httpx.AsyncClient(timeout=120) as http:
        while True:
            jobs = await get_jobs(http)
            if not jobs:
                await asyncio.sleep(SLEEP_SECONDS)
                continue
            log("JOBS_FETCHED", count=len(jobs))
            for job in jobs:
                await process_job(tg, ai, http, job)


if __name__ == "__main__":
    asyncio.run(main())
