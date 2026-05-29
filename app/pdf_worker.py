#!/usr/bin/env python3
"""
pdf_worker.py — PDF text extraction worker.

Polls processing_jobs for job_type='pdf_extract'. For each job:
  1. Downloads the file from Supabase Storage.
  2. Extracts text via PyMuPDF (digital PDFs).
  3. Marks image-only pages for OCR (flags them — OCR not implemented here, use GPT-4o Vision).
  4. Updates source_documents: extracted_text, page_count, extraction_method, processing_status='extracted'.
  5. Creates a new processing_jobs row (job_type='embed_chunk') for embed_worker.py.

Requires: supabase/migrations/003_knowledge_layer.sql applied.
Not in Procfile — run on demand or add as a Railway process when knowledge ingestion is active.
"""

import os
import asyncio
import tempfile
from pathlib import Path

from typing import Optional

import httpx
import fitz  # PyMuPDF
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

MAX_RETRIES = 5
SLEEP_SECONDS = 5  # polling interval when no jobs

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}


def log(event: str, **kwargs):
    parts = [event] + [f"{k}={v}" for k, v in kwargs.items()]
    print(" | ".join(parts), flush=True)


async def get_jobs(http: httpx.AsyncClient) -> list[dict]:
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/processing_jobs",
        headers=HEADERS,
        params={
            "job_type": "eq.pdf_extract",
            "status": "in.(pending,failed)",
            "retry_count": f"lt.{MAX_RETRIES}",
            "limit": "10",
            "order": "created_at.asc",
        },
    )
    r.raise_for_status()
    return r.json()


async def update_job(http: httpx.AsyncClient, job_id: str, status: str, result=None, error=None, retry_count=None):
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


async def get_source_document(http: httpx.AsyncClient, doc_id: str) -> Optional[dict]:
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/source_documents",
        headers=HEADERS,
        params={"id": f"eq.{doc_id}", "limit": "1"},
    )
    if r.status_code >= 400 or not r.json():
        return None
    return r.json()[0]


async def update_source_document(http: httpx.AsyncClient, doc_id: str, updates: dict):
    r = await http.patch(
        f"{SUPABASE_URL}/rest/v1/source_documents?id=eq.{doc_id}",
        headers=HEADERS,
        json={"updated_at": "now()", **updates},
    )
    if r.status_code >= 400:
        log("UPDATE_DOCUMENT_ERROR", id=doc_id, status=r.status_code, error=r.text[:120])


async def download_from_storage(http: httpx.AsyncClient, storage_path: str, dest_path: str) -> bool:
    # storage_path is stored as "bucket/path" — split off bucket
    parts = storage_path.split("/", 1)
    if len(parts) != 2:
        log("STORAGE_PATH_INVALID", path=storage_path)
        return False
    bucket, obj_path = parts[0], parts[1]

    r = await http.get(
        f"{SUPABASE_URL}/storage/v1/object/{bucket}/{obj_path}",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
    )
    if r.status_code >= 400:
        log("STORAGE_DOWNLOAD_ERROR", status=r.status_code, path=storage_path)
        return False

    with open(dest_path, "wb") as f:
        f.write(r.content)
    return True


def extract_text_from_pdf(file_path: str) -> tuple[str, int, str, float]:
    """
    Returns: (extracted_text, page_count, extraction_method, ocr_confidence)
    extraction_method: 'digital' | 'mixed' | 'needs_ocr'
    """
    doc = fitz.open(file_path)
    page_count = len(doc)

    pages_text = []
    digital_pages = 0
    image_only_pages = 0

    for page in doc:
        text = page.get_text().strip()
        if text:
            pages_text.append(text)
            digital_pages += 1
        else:
            # Page has no extractable text — likely image-only (scanned)
            pages_text.append(f"[PAGE {page.number + 1}: image-only, requires OCR]")
            image_only_pages += 1

    doc.close()

    full_text = "\n\n".join(pages_text)

    if image_only_pages == 0:
        method = "digital"
        confidence = 1.0
    elif digital_pages == 0:
        method = "needs_ocr"
        confidence = 0.0
    else:
        method = "mixed"
        confidence = digital_pages / page_count

    return full_text, page_count, method, confidence


async def delete_from_storage(http: httpx.AsyncClient, storage_path: str) -> bool:
    parts = storage_path.split("/", 1)
    if len(parts) != 2:
        return False
    bucket, obj_path = parts[0], parts[1]
    import json as _json
    r = await http.request(
        "DELETE",
        f"{SUPABASE_URL}/storage/v1/object/{bucket}",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
                 "Content-Type": "application/json"},
        content=_json.dumps({"prefixes": [obj_path]}).encode(),
    )
    if r.status_code >= 400:
        log("STORAGE_DELETE_ERROR", status=r.status_code, path=storage_path)
        return False
    log("RAW_FILE_DELETED", path=storage_path)
    return True


async def create_embed_job(http: httpx.AsyncClient, source_document_id: str) -> bool:
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/processing_jobs",
        headers=HEADERS,
        json={
            "job_type": "embed_chunk",
            "status": "pending",
            "payload": {"source_document_id": source_document_id},
            "retry_count": 0,
            "target_table": "document_chunks",
            "target_key": source_document_id,
        },
    )
    if r.status_code >= 400:
        log("EMBED_JOB_CREATE_ERROR", status=r.status_code, error=r.text[:120])
        return False
    return True


async def process_job(http: httpx.AsyncClient, job: dict):
    job_id = job["id"]
    payload = job.get("payload") or {}
    retry_count = job.get("retry_count") or 0
    source_document_id = payload.get("source_document_id")

    if not source_document_id:
        log("INVALID_JOB_PAYLOAD", id=job_id)
        await update_job(http, job_id, "failed", error="missing source_document_id", retry_count=retry_count + 1)
        return

    log("JOB_START", id=job_id, doc_id=source_document_id, attempt=retry_count + 1)

    try:
        await update_job(http, job_id, "processing")
        await update_source_document(http, source_document_id, {"processing_status": "extracting"})

        doc = await get_source_document(http, source_document_id)
        if not doc:
            raise RuntimeError(f"source_document not found: {source_document_id}")

        storage_path = doc.get("storage_path")
        if not storage_path:
            raise RuntimeError("source_document has no storage_path")

        with tempfile.TemporaryDirectory() as tmpdir:
            dest = str(Path(tmpdir) / doc.get("file_name", "document.pdf"))
            log("DOWNLOADING", id=job_id, path=storage_path)
            ok = await download_from_storage(http, storage_path, dest)
            if not ok:
                raise RuntimeError("storage download failed")

            log("EXTRACTING", id=job_id, file=dest)
            extracted_text, page_count, method, confidence = extract_text_from_pdf(dest)

        char_count = len(extracted_text)
        log("EXTRACTED", id=job_id, pages=page_count, method=method, chars=char_count, confidence=f"{confidence:.2f}")

        await update_source_document(http, source_document_id, {
            "extracted_text": extracted_text,
            "page_count": page_count,
            "extraction_method": method if method != "needs_ocr" else "ocr_vision",
            "ocr_confidence": confidence if confidence > 0 else None,
            "processing_status": "extracted",
        })

        # Delete raw file from Storage — raw sources are transient, knowledge is permanent
        storage_path = doc.get("storage_path")
        if storage_path:
            deleted = await delete_from_storage(http, storage_path)
            if deleted:
                await update_source_document(http, source_document_id, {
                    "raw_file_deleted_at": "now()",
                })

        if method == "needs_ocr":
            log("NEEDS_OCR", id=job_id, doc_id=source_document_id,
                hint="Use GPT-4o Vision to extract text from image-only pages")
            await update_job(http, job_id, "processed",
                             result={"page_count": page_count, "method": method, "chars": char_count,
                                     "note": "image-only PDF — OCR required before embedding"})
            return

        ok = await create_embed_job(http, source_document_id)
        if not ok:
            log("WARNING", id=job_id, msg="embed job creation failed — extracted text saved but not queued for embedding")

        await update_job(http, job_id, "processed",
                         result={"page_count": page_count, "method": method, "chars": char_count})

        log("JOB_DONE", id=job_id, doc_id=source_document_id)

    except Exception as e:
        new_retry = retry_count + 1
        abandoned = new_retry >= MAX_RETRIES
        log("JOB_FAILED", id=job_id, attempt=new_retry, abandoned=abandoned, error=str(e)[:120])
        await update_job(http, job_id, "failed", error=str(e), retry_count=new_retry)
        await update_source_document(http, source_document_id, {
            "processing_status": "failed",
            "error": str(e)[:500],
        })


async def main():
    log("PDF_WORKER_START", max_retries=MAX_RETRIES)

    async with httpx.AsyncClient(timeout=120) as http:
        while True:
            jobs = await get_jobs(http)

            if not jobs:
                await asyncio.sleep(SLEEP_SECONDS)
                continue

            log("JOBS_FETCHED", count=len(jobs))
            for job in jobs:
                await process_job(http, job)


if __name__ == "__main__":
    asyncio.run(main())
