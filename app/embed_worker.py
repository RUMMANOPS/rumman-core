#!/usr/bin/env python3
"""
embed_worker.py — Text chunking and embedding worker.

Polls processing_jobs for job_type='embed_chunk'. For each job:
  1. Reads extracted_text from source_documents.
  2. Chunks the text (question-aware for exams, paragraph-aware for everything else).
  3. Calls OpenAI text-embedding-3-large (3072 dims) for each chunk.
  4. Inserts chunks into document_chunks with embeddings.
  5. Updates source_documents processing_status='chunked'.

Requires: supabase/migrations/003_knowledge_layer.sql applied.
Not in Procfile — run on demand.
"""

import os
import asyncio
import re

from typing import Optional

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

MAX_RETRIES = 5
SLEEP_SECONDS = 5

EMBED_MODEL = "text-embedding-3-large"
EMBED_DIMS = 1536

PARAGRAPH_CHUNK_TOKENS = 500
PARAGRAPH_OVERLAP_TOKENS = 50

# text-embedding-3-large hard limit is 8192 tokens; ~4 chars/token on average
_MAX_EMBED_CHARS = 8000 * 4

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}


def log(event: str, **kwargs):
    parts = [event] + [f"{k}={v}" for k, v in kwargs.items()]
    print(" | ".join(parts), flush=True)


def chunk_exam_text(text: str) -> list[str]:
    """
    Split exam content on question boundaries.
    Patterns: "1.", "1)", "Q1", "Q1.", "Question 1", "السؤال الأول"
    Each question (including its stem) becomes one chunk.
    """
    # Match numbered question patterns — Arabic and English
    question_pattern = re.compile(
        r'(?m)^(?:'
        r'(?:Question|Q\.?)\s*\d+'           # "Question 1", "Q1", "Q.1"
        r'|(?:السؤال|سؤال)\s*(?:\d+|الأول|الثاني|الثالث|الرابع|الخامس)'  # Arabic
        r'|\d+\s*[.)\-]'                      # "1.", "1)", "1-"
        r')',
        re.IGNORECASE | re.UNICODE,
    )

    splits = [m.start() for m in question_pattern.finditer(text)]

    if len(splits) < 2:
        # Couldn't split by questions — fall back to paragraph chunking
        return chunk_paragraph_text(text)

    chunks = []
    for i, start in enumerate(splits):
        end = splits[i + 1] if i + 1 < len(splits) else len(text)
        chunk = text[start:end].strip()
        if len(chunk) >= 20:  # skip trivially short fragments
            chunks.append(chunk)

    return chunks


def chunk_paragraph_text(text: str, max_tokens: int = PARAGRAPH_CHUNK_TOKENS, overlap: int = PARAGRAPH_OVERLAP_TOKENS) -> list[str]:
    """
    Split on double newlines (paragraph boundaries), then merge paragraphs into
    chunks of roughly max_tokens words. Overlap re-includes last `overlap` words
    of the previous chunk to preserve context at boundaries.
    """
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]

    chunks = []
    current_words: list[str] = []

    for para in paragraphs:
        words = para.split()
        if not words:
            continue

        current_words.extend(words)

        if len(current_words) >= max_tokens:
            chunks.append(" ".join(current_words))
            current_words = current_words[-overlap:] if overlap else []

    if current_words:
        chunks.append(" ".join(current_words))

    return [c for c in chunks if len(c) >= 20]


def chunk_text(text: str, source_type: str) -> list[str]:
    if source_type == "exam":
        return chunk_exam_text(text)
    return chunk_paragraph_text(text)


async def get_jobs(http: httpx.AsyncClient) -> list[dict]:
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/processing_jobs",
        headers=HEADERS,
        params={
            "job_type": "eq.embed_chunk",
            "status": "in.(pending,failed)",
            "retry_count": f"lt.{MAX_RETRIES}",
            "limit": "5",
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


async def embed_and_insert_chunks(
    ai: AsyncOpenAI,
    http: httpx.AsyncClient,
    doc: dict,
    chunks: list[str],
) -> int:
    inserted = 0

    for i, chunk_text in enumerate(chunks):
        if len(chunk_text) > _MAX_EMBED_CHARS:
            log("CHUNK_TRUNCATED", doc=doc["id"], chunk=i, original=len(chunk_text), limit=_MAX_EMBED_CHARS)
            chunk_text = chunk_text[:_MAX_EMBED_CHARS]
        resp = await ai.embeddings.create(model=EMBED_MODEL, input=chunk_text, dimensions=EMBED_DIMS)
        embedding = resp.data[0].embedding

        row = {
            "source_document_id": doc["id"],
            "content": chunk_text,
            "embedding": embedding,
            "institution": doc.get("institution", "SEU"),
            "course_code": doc.get("course_code"),
            "source_type": doc["source_type"],
            "exam_type": doc.get("exam_type"),
            "academic_year": doc.get("academic_year"),
            "semester": doc.get("semester"),
            "professor": doc.get("professor"),
            "language": doc.get("language"),
            "chunk_index": i,
            "total_chunks": len(chunks),
            "ocr_confidence": doc.get("ocr_confidence"),
        }

        r = await http.post(
            f"{SUPABASE_URL}/rest/v1/document_chunks",
            headers=HEADERS,
            json=row,
        )
        if r.status_code >= 400:
            log("CHUNK_INSERT_ERROR", chunk=i, status=r.status_code, error=r.text[:120])
        else:
            inserted += 1

    return inserted


async def process_job(ai: AsyncOpenAI, http: httpx.AsyncClient, job: dict):
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
        await update_source_document(http, source_document_id, {"processing_status": "chunking"})

        doc = await get_source_document(http, source_document_id)
        if not doc:
            raise RuntimeError(f"source_document not found: {source_document_id}")

        extracted_text = doc.get("extracted_text", "")
        if not extracted_text or not extracted_text.strip():
            raise RuntimeError("source_document has no extracted_text — run pdf_extract first")

        source_type = doc["source_type"]
        chunks = chunk_text(extracted_text, source_type)

        log("CHUNKED", id=job_id, source_type=source_type, chunks=len(chunks))

        if not chunks:
            raise RuntimeError("chunking produced zero chunks")

        inserted = await embed_and_insert_chunks(ai, http, doc, chunks)
        log("EMBEDDED", id=job_id, chunks=len(chunks), inserted=inserted)

        await update_source_document(http, source_document_id, {"processing_status": "chunked"})
        await update_job(http, job_id, "processed",
                         result={"chunks": len(chunks), "inserted": inserted})

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
    log("EMBED_WORKER_START", model=EMBED_MODEL, dims=EMBED_DIMS, max_retries=MAX_RETRIES)

    ai = AsyncOpenAI(api_key=OPENAI_API_KEY)

    async with httpx.AsyncClient(timeout=120) as http:
        while True:
            jobs = await get_jobs(http)

            if not jobs:
                await asyncio.sleep(SLEEP_SECONDS)
                continue

            log("JOBS_FETCHED", count=len(jobs))
            for job in jobs:
                await process_job(ai, http, job)


if __name__ == "__main__":
    asyncio.run(main())
