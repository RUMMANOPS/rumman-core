#!/usr/bin/env python3
"""
question_extraction_worker.py — Structured exam question extraction.

Reads exam source_documents, calls GPT-4o to extract individual questions,
stores results in exam_questions table.

WHY THIS EXISTS:
  embed_worker.py chunks exam PDFs into paragraphs — it doesn't know where
  one question ends and the next begins. exam_intelligence stores course-level
  topic summaries only. Neither supports "show me questions from chapters 1–5
  only" or auto-graded MCQ practice. This worker bridges that gap.

TWO-PASS DESIGN:
  Pass 1 (this worker):
    - Extract question text, type, answer options, topic tags
    - Identify exam_type (midterm/final/quiz) from document context
    - Verify course attribution with a second model call
    Stored as: attribution_verified=True, chapter_verified=False

  Pass 2 (future chapter_attribution_worker.py):
    - Map topic_tags → chapter_numbers using course syllabus
    - Requires syllabi indexed as source_documents
    Stored as: chapter_verified=True

ENABLE: set QUESTION_EXTRACTION_WORKER_ENABLED=true in Railway environment.

BUDGET:
  GPT-4o is used (not mini) — exam extraction requires high accuracy.
  Default daily cap: 200 documents/day.
  A typical exam PDF ≈ 1 GPT-4o call (extraction) + 1 mini call (verification).
  At $5/1M input + $15/1M output, a 3000-token exam ≈ $0.06. 200/day ≈ $12/day max.
  Tune QUESTION_EXTRACTION_MAX_DAILY_DOCS to control spend.

SOURCE PRIORITY:
  1. source_documents where source_type='exam' AND question_extraction_status='pending'
     AND extracted_text IS NOT NULL
  2. (Phase 2) media_files from Telegram where file_type='image' and OCR suggests exam
"""
from __future__ import annotations

import os
import re
import json
import asyncio
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL   = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY   = os.environ.get("SUPABASE_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

SEU_TENANT_ID = "00000000-0000-0000-0000-000000000001"

# Models
EXTRACT_MODEL = "gpt-4o"          # high accuracy for question extraction
VERIFY_MODEL  = "gpt-4o-mini"     # cheaper for course attribution verification

# Operational parameters
BATCH_SIZE      = int(os.getenv("QUESTION_EXTRACTION_BATCH_SIZE", "5"))
SLEEP_SECONDS   = int(os.getenv("QUESTION_EXTRACTION_SLEEP_SECONDS", "60"))
MAX_DAILY_DOCS  = int(os.getenv("QUESTION_EXTRACTION_MAX_DAILY_DOCS", "200"))
MIN_CONFIDENCE  = float(os.getenv("QUESTION_EXTRACTION_MIN_CONFIDENCE", "0.60"))
VERIFY_THRESHOLD = float(os.getenv("QUESTION_EXTRACTION_VERIFY_THRESHOLD", "0.80"))

# Hard gate — must be explicitly set in Railway to prevent accidental activation.
_ENABLED = os.getenv("QUESTION_EXTRACTION_WORKER_ENABLED", "").strip().lower() == "true"

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_EXTRACT_SYSTEM = """\
You are an academic exam analyzer for Saudi Electronic University (SEU).

Extract EVERY individual exam question from the provided document text.
The text may be Arabic, English, or mixed. OCR quality may vary.

For each question return:
- question_text: full question text, exactly as written (preserve Arabic/English)
- question_type: "mcq" | "essay" | "calculation" | "true_false" | "unknown"
- answer_options: for MCQ only — array of {"key": "أ", "text": "..."} objects. null for all other types.
- model_answer: if an answer key is embedded in the document, extract it. null otherwise.
- topic_tags: 1–3 specific academic topic tags in the SAME LANGUAGE as the question.
  Be specific: "نظرية ناش" not "إدارة". "TCP/IP Model" not "Networking".
- confidence: 0.0–1.0 for extraction quality.
  0.9+ = clean, complete question
  0.7–0.9 = minor OCR noise, question intent clear
  0.5–0.7 = significant noise, question partially recoverable
  below 0.5 = skip — too damaged to be useful

Also return:
- exam_type_hint: "midterm" | "final" | "quiz" | "general" — infer from document context
  (headers, question count, coverage breadth). null if unclear.
- course_code_hint: SEU course code if identifiable from context (e.g. "MGT312"). null if unclear.
- exam_year_hint: academic year string if visible (e.g. "2024-2025" or "1446"). null if unclear.

Rules:
- Extract ONLY actual exam questions, not instructions, headers, or grading rubrics.
- Include ALL questions even if OCR quality is imperfect — set confidence accordingly.
- For MCQ: always include ALL answer choices, not just the question stem.
- topic_tags must be academically meaningful. Max 3 per question.
- If the same question appears twice (duplicate from poor OCR), include it once.
- An empty questions array is valid if the document contains no extractable questions.

Return ONLY valid JSON — no text outside JSON:
{
  "questions": [
    {
      "question_text": "...",
      "question_type": "mcq",
      "answer_options": [{"key": "أ", "text": "..."}, ...],
      "model_answer": null,
      "topic_tags": ["نظرية الألعاب"],
      "confidence": 0.92
    }
  ],
  "exam_type_hint": "final",
  "course_code_hint": "MGT312",
  "exam_year_hint": "2024-2025"
}
"""

_VERIFY_SYSTEM = """\
You are verifying course attribution for an exam question from Saudi Electronic University (SEU).

Given a question text and a proposed course code, confirm whether this question
plausibly belongs to that course based on its academic content.

SEU course code formats: IT488, CS251, MGT311, ACCT201, ECOM301, FIN201, HCI101

Respond ONLY with valid JSON — no text outside JSON:
{"confirmed": true, "confidence": 0.91}

Rules:
- confirmed=true ONLY if the question content is clearly aligned with the course subject area.
- confidence >= 0.80: strong topical match.
- confidence 0.60–0.79: plausible match, some doubt.
- confirmed=false if the question content is unrelated to the course.
- When in doubt, confirmed=false is safer than a false positive.
"""


def log(event: str, **kwargs):
    parts = [event] + [f"{k}={v}" for k, v in kwargs.items()]
    print(" | ".join(parts), flush=True)


def _seconds_until_utc_midnight() -> int:
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return max(60, int((tomorrow - now).total_seconds()))


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------

async def _fetch_pending_docs(http: httpx.AsyncClient) -> list[dict]:
    """Fetch exam documents that haven't been processed yet."""
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/source_documents",
        headers=HEADERS,
        params={
            "source_type":                  "eq.exam",
            "question_extraction_status":   "eq.pending",
            "extracted_text":               "not.is.null",
            "tenant_id":                    f"eq.{SEU_TENANT_ID}",
            "select":                       "id,file_name,course_code,extracted_text,language",
            "order":                        "created_at.asc",
            "limit":                        str(BATCH_SIZE),
        },
    )
    if r.status_code >= 400:
        log("FETCH_PENDING_ERROR", status=r.status_code, error=r.text[:200])
        return []
    return r.json()


async def _claim_document(http: httpx.AsyncClient, doc_id: str) -> bool:
    """Atomically claim a document for processing (prevent double-processing)."""
    r = await http.patch(
        f"{SUPABASE_URL}/rest/v1/source_documents",
        headers={**HEADERS, "Prefer": "return=minimal"},
        params={
            "id":                           f"eq.{doc_id}",
            "question_extraction_status":   "eq.pending",  # only claim if still pending
        },
        json={"question_extraction_status": "running"},
    )
    # 204 = success, anything else = already claimed by another worker
    return r.status_code == 204


async def _mark_document(http: httpx.AsyncClient, doc_id: str, status: str):
    await http.patch(
        f"{SUPABASE_URL}/rest/v1/source_documents",
        headers={**HEADERS, "Prefer": "return=minimal"},
        params={"id": f"eq.{doc_id}"},
        json={"question_extraction_status": status},
    )


async def _insert_questions(http: httpx.AsyncClient, rows: list[dict]) -> int:
    if not rows:
        return 0
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/exam_questions",
        headers={**HEADERS, "Prefer": "return=minimal,resolution=ignore-duplicates"},
        json=rows,
    )
    if r.status_code >= 400:
        log("INSERT_ERROR", status=r.status_code, error=r.text[:300])
        return 0
    return len(rows)


async def _log_ai_run(
    http: httpx.AsyncClient,
    worker: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    subject_id: str,
):
    cost = (input_tokens / 1_000_000) * (5.0 if "gpt-4o" in model and "mini" not in model else 0.15)
    cost += (output_tokens / 1_000_000) * (15.0 if "gpt-4o" in model and "mini" not in model else 0.60)
    await http.post(
        f"{SUPABASE_URL}/rest/v1/ai_runs",
        headers={**HEADERS, "Prefer": "return=minimal"},
        json={
            "tenant_id":      SEU_TENANT_ID,
            "worker":         worker,
            "model":          model,
            "input_tokens":   input_tokens,
            "output_tokens":  output_tokens,
            "cost_usd":       round(cost, 6),
            "subject_type":   "source_document",
            "subject_id":     subject_id,
        },
    )


# ---------------------------------------------------------------------------
# AI calls
# ---------------------------------------------------------------------------

async def _extract_questions(
    client: AsyncOpenAI,
    doc: dict,
) -> Optional[dict]:
    """
    Call GPT-4o to extract all questions from the document's extracted_text.
    Returns parsed JSON response or None on failure.
    """
    text = (doc.get("extracted_text") or "").strip()
    if not text:
        return None

    # Truncate to avoid token limits — most exam PDFs are < 6000 chars of meaningful content
    text_truncated = text[:12_000]

    # Add known course context if available
    course_hint = doc.get("course_code") or ""
    context_line = f"\nThis document is for course: {course_hint}" if course_hint else ""

    user_msg = f"Document filename: {doc.get('file_name', 'unknown')}{context_line}\n\n---\n{text_truncated}"

    try:
        resp = await client.chat.completions.create(
            model=EXTRACT_MODEL,
            messages=[
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.1,  # low temp for consistent extraction
            response_format={"type": "json_object"},
            max_tokens=4096,
        )
        raw = resp.choices[0].message.content or "{}"
        result = json.loads(raw)
        result["_usage"] = {
            "input_tokens":  resp.usage.prompt_tokens,
            "output_tokens": resp.usage.completion_tokens,
        }
        return result
    except json.JSONDecodeError as e:
        log("EXTRACT_JSON_ERROR", doc_id=doc["id"], error=str(e))
        return None
    except Exception as e:
        log("EXTRACT_API_ERROR", doc_id=doc["id"], error=str(e)[:200])
        return None


async def _verify_course_attribution(
    client: AsyncOpenAI,
    question_text: str,
    course_code: str,
) -> tuple[bool, float]:
    """
    Call GPT-4o-mini to verify that a question plausibly belongs to course_code.
    Returns (confirmed, confidence).
    """
    if not course_code or not question_text:
        return False, 0.0
    try:
        resp = await client.chat.completions.create(
            model=VERIFY_MODEL,
            messages=[
                {"role": "system", "content": _VERIFY_SYSTEM},
                {"role": "user",   "content": (
                    f"Course code: {course_code}\n\n"
                    f"Question:\n{question_text[:800]}"
                )},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
            max_tokens=64,
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)
        return bool(data.get("confirmed", False)), float(data.get("confidence", 0.0))
    except Exception:
        return False, 0.0


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------

async def _process_document(
    http: httpx.AsyncClient,
    client: AsyncOpenAI,
    doc: dict,
) -> int:
    """
    Process one exam document: extract questions, verify, store.
    Returns number of questions stored.
    """
    doc_id = doc["id"]

    # Claim atomically — skip if another worker got here first
    if not await _claim_document(http, doc_id):
        log("ALREADY_CLAIMED", doc_id=doc_id)
        return 0

    log("PROCESSING", doc_id=doc_id, file=doc.get("file_name", "?")[:60])

    # --- Pass 1: Extract questions ---
    extraction = await _extract_questions(client, doc)
    if extraction is None:
        await _mark_document(http, doc_id, "failed")
        log("EXTRACT_FAILED", doc_id=doc_id)
        return 0

    await _log_ai_run(
        http,
        worker="question_extraction_worker",
        model=EXTRACT_MODEL,
        input_tokens=extraction["_usage"]["input_tokens"],
        output_tokens=extraction["_usage"]["output_tokens"],
        subject_id=doc_id,
    )

    questions = extraction.get("questions") or []
    if not questions:
        await _mark_document(http, doc_id, "skipped")
        log("NO_QUESTIONS", doc_id=doc_id, file=doc.get("file_name", "?")[:60])
        return 0

    # Resolve course_code: prefer what's already on the document
    doc_course = (doc.get("course_code") or "").strip().upper()
    hint_course = (extraction.get("course_code_hint") or "").strip().upper()
    course_code = doc_course or hint_course or ""

    exam_type = extraction.get("exam_type_hint") or "general"
    exam_year = extraction.get("exam_year_hint")

    # --- Pass 2: Verify course attribution in batch ---
    # Verify once using a representative question (first high-confidence MCQ or first question)
    attribution_verified = False
    if course_code:
        representative = next(
            (q for q in questions if q.get("confidence", 0) >= 0.8),
            questions[0],
        )
        confirmed, conf = await _verify_course_attribution(
            client, representative["question_text"], course_code
        )
        attribution_verified = confirmed and conf >= VERIFY_THRESHOLD
        log("ATTRIBUTION_VERIFY",
            doc_id=doc_id,
            course=course_code,
            confirmed=confirmed,
            confidence=round(conf, 2))

    # --- Build rows ---
    rows = []
    for q in questions:
        confidence = float(q.get("confidence", 0.5))
        if confidence < MIN_CONFIDENCE:
            continue  # skip low-quality extractions

        rows.append({
            "tenant_id":             SEU_TENANT_ID,
            "course_code":           course_code or "UNKNOWN",
            "exam_type":             exam_type if exam_type in ("midterm", "final", "quiz", "general") else "general",
            "exam_year":             exam_year,
            "chapter_numbers":       None,   # Phase 2: chapter_attribution_worker
            "topic_tags":            q.get("topic_tags") or [],
            "question_text":         q["question_text"].strip(),
            "question_type":         q.get("question_type") or "unknown",
            "answer_options":        q.get("answer_options"),
            "model_answer":          q.get("model_answer"),
            "source_document_id":    doc_id,
            "extraction_confidence": round(confidence, 3),
            "attribution_verified":  attribution_verified,
            "chapter_verified":      False,
        })

    stored = await _insert_questions(http, rows)
    await _mark_document(http, doc_id, "completed")

    log("DONE",
        doc_id=doc_id,
        course=course_code or "?",
        exam_type=exam_type,
        extracted=len(questions),
        stored=stored,
        verified=attribution_verified)

    return stored


# ---------------------------------------------------------------------------
# Daily budget tracking
# ---------------------------------------------------------------------------

def _utc_day_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


_daily_counter: dict[str, int] = {}


def _docs_processed_today() -> int:
    return _daily_counter.get(_utc_day_key(), 0)


def _increment_daily_counter():
    key = _utc_day_key()
    _daily_counter[key] = _daily_counter.get(key, 0) + 1


def _reset_daily_if_new_day():
    """Keep only today's key — old keys just sit in memory (negligible)."""
    pass  # the get() with today's key is sufficient


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def main():
    if not _ENABLED:
        log("DISABLED", reason="QUESTION_EXTRACTION_WORKER_ENABLED not set to true")
        return

    log("START",
        model_extract=EXTRACT_MODEL,
        model_verify=VERIFY_MODEL,
        batch_size=BATCH_SIZE,
        max_daily_docs=MAX_DAILY_DOCS)

    client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    async with httpx.AsyncClient(timeout=60) as http:
        # Heartbeat registration
        await http.post(
            f"{SUPABASE_URL}/rest/v1/worker_heartbeats",
            headers={**HEADERS, "Prefer": "resolution=merge-duplicates"},
            json={
                "worker_id":  "question_extraction_worker",
                "tenant_id":  SEU_TENANT_ID,
                "status":     "running",
                "last_seen":  datetime.now(timezone.utc).isoformat(),
            },
        )

        while True:
            _reset_daily_if_new_day()

            if _docs_processed_today() >= MAX_DAILY_DOCS:
                sleep_secs = _seconds_until_utc_midnight()
                log("BUDGET_EXHAUSTED",
                    processed_today=_docs_processed_today(),
                    sleeping_until_midnight_in_secs=sleep_secs)
                await asyncio.sleep(sleep_secs)
                continue

            docs = await _fetch_pending_docs(http)

            if not docs:
                log("IDLE", sleeping_secs=SLEEP_SECONDS)
                await asyncio.sleep(SLEEP_SECONDS)
                continue

            for doc in docs:
                if _docs_processed_today() >= MAX_DAILY_DOCS:
                    break
                try:
                    await _process_document(http, client, doc)
                    _increment_daily_counter()
                except Exception as e:
                    log("PROCESS_ERROR", doc_id=doc.get("id"), error=str(e)[:300])
                    try:
                        await _mark_document(http, doc.get("id", ""), "failed")
                    except Exception:
                        pass

            # Update heartbeat
            await http.patch(
                f"{SUPABASE_URL}/rest/v1/worker_heartbeats",
                headers={**HEADERS, "Prefer": "return=minimal"},
                params={"worker_id": "eq.question_extraction_worker"},
                json={
                    "last_seen":      datetime.now(timezone.utc).isoformat(),
                    "docs_today":     _docs_processed_today(),
                },
            )

            await asyncio.sleep(2)  # brief pause between batches


if __name__ == "__main__":
    asyncio.run(main())
