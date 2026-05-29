#!/usr/bin/env python3
"""
attribution_worker.py — AI-assisted course attribution for untagged document chunks.

Processes document_chunks where attribution_status='original' AND course_code IS NULL.
Calls GPT-4o-mini to suggest a course code. High-confidence (>=0.85) results are
written as 'machine_asserted' with a full ai_runs provenance trail.

All attributions remain machine_asserted until confirmed by downstream validation.
False attribution (wrong course) is worse than no attribution — threshold is intentionally high.

Enable: set ATTRIBUTION_WORKER_ENABLED=true in Railway environment.
"""

import os
import json
import asyncio
import time
import httpx
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL  = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY  = os.environ.get("SUPABASE_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

SEU_TENANT_ID = "00000000-0000-0000-0000-000000000001"
MODEL         = "gpt-4o-mini"
BATCH_SIZE    = int(os.getenv("ATTRIBUTION_BATCH_SIZE", "20"))
SLEEP_SECONDS = int(os.getenv("ATTRIBUTION_SLEEP_SECONDS", "30"))
CONFIDENCE_THRESHOLD = 0.85
MAX_TOKENS_PER_RUN   = int(os.getenv("ATTRIBUTION_MAX_TOKENS_PER_RUN", "500_000"))

_ENABLED = os.getenv("ATTRIBUTION_WORKER_ENABLED", "").strip().lower() == "true"

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

_ATTRIBUTION_SYSTEM = """\
You are an academic content classifier for Saudi Electronic University (SEU).

Given a text excerpt from the SEU knowledge corpus, identify which course it belongs to.

SEU course code formats:
  Latin:  IT488, CS251, MGT311, ACCT201, ENG103, TRA330, BUSA101
  Arabic: قنن121, قنن211, قنن311  (Arabic prefix + 3 digits)

Respond ONLY with valid JSON — no text outside JSON:
  {"course_code": "IT488", "confidence": 0.92}

Confidence rules:
  >= 0.85  ONLY when the code is explicitly present in the text, OR content is
           unambiguously specific to exactly one course (e.g. a named syllabus)
  < 0.50   (and course_code: null) when content is general or multi-course
  Never guess based on topic alone — algorithms could be CS101, CS201, or IT201.\
"""


def log(event: str, **kwargs):
    parts = [event] + [f"{k}={v}" for k, v in kwargs.items()]
    print(" | ".join(parts), flush=True)


async def fetch_unattributed(http: httpx.AsyncClient) -> list[dict]:
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/document_chunks",
        headers=HEADERS,
        params={
            "attribution_status": "eq.original",
            "course_code":        "is.null",
            "embedding":          "not.is.null",
            "tenant_id":          f"eq.{SEU_TENANT_ID}",
            "select":             "id,content,source_type,authority_tier",
            "order":              "id.asc",
            "limit":              str(BATCH_SIZE),
        },
    )
    if r.status_code >= 400:
        log("FETCH_ERROR", status=r.status_code, error=r.text[:120])
        return []
    return r.json()


async def classify_chunk(ai: AsyncOpenAI, content: str) -> tuple[str | None, float, int, int]:
    """Returns (course_code, confidence, input_tokens, output_tokens)."""
    resp = await ai.chat.completions.create(
        model=MODEL,
        temperature=0.0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _ATTRIBUTION_SYSTEM},
            {"role": "user",   "content": (content or "")[:800]},
        ],
    )
    raw = resp.choices[0].message.content or "{}"
    parsed = json.loads(raw)
    course_code = parsed.get("course_code")
    confidence  = float(parsed.get("confidence", 0.0))
    in_tok  = resp.usage.prompt_tokens     if resp.usage else 0
    out_tok = resp.usage.completion_tokens if resp.usage else 0
    return course_code, confidence, in_tok, out_tok


async def create_ai_run(
    http: httpx.AsyncClient,
    chunk_id: str,
    course_code: str | None,
    confidence: float,
    input_tokens: int,
    output_tokens: int,
    duration_ms: int,
) -> str | None:
    """Create an ai_runs provenance row. Returns the new run id."""
    cost = (input_tokens * 0.15 + output_tokens * 0.60) / 1_000_000
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/ai_runs",
        headers=HEADERS,
        json={
            "tenant_id":      SEU_TENANT_ID,
            "worker":         "attribution_worker",
            "model":          MODEL,
            "prompt_version": "1.0",
            "job_type":       "course_attribution",
            "input_tokens":   input_tokens,
            "output_tokens":  output_tokens,
            "cost_usd":       round(cost, 6),
            "duration_ms":    duration_ms,
            "subject_type":   "document_chunk",
            "subject_id":     chunk_id,
            "output_summary": f"course_code={course_code} confidence={confidence:.2f}",
            "status":         "completed",
            "completed_at":   "now()",
        },
    )
    if r.status_code >= 400:
        log("AI_RUN_CREATE_ERROR", status=r.status_code, error=r.text[:120])
        return None
    rows = r.json()
    return rows[0]["id"] if rows else None


async def update_chunk(
    http: httpx.AsyncClient,
    chunk_id: str,
    course_code: str | None,
    confidence: float,
    ai_run_id: str | None,
) -> None:
    patch = {
        "attribution_status":     "machine_asserted",
        "attribution_confidence": confidence,
        "attribution_ai_run_id":  ai_run_id,
    }
    if course_code:
        patch["course_code"] = course_code

    r = await http.patch(
        f"{SUPABASE_URL}/rest/v1/document_chunks?id=eq.{chunk_id}",
        headers=HEADERS,
        json=patch,
    )
    if r.status_code >= 400:
        log("CHUNK_UPDATE_ERROR", id=chunk_id, status=r.status_code, error=r.text[:120])


async def main():
    if not _ENABLED:
        log(
            "ATTRIBUTION_WORKER_DISABLED",
            hint="set ATTRIBUTION_WORKER_ENABLED=true in Railway env",
        )
        return

    ai = AsyncOpenAI(api_key=OPENAI_API_KEY)
    log("ATTRIBUTION_WORKER_START", batch_size=BATCH_SIZE, threshold=CONFIDENCE_THRESHOLD,
        max_tokens=MAX_TOKENS_PER_RUN)

    async with httpx.AsyncClient(timeout=30) as http:
        while True:
            try:
                chunks = await fetch_unattributed(http)

                if not chunks:
                    log("IDLE", msg="no unattributed chunks remaining")
                    await asyncio.sleep(SLEEP_SECONDS)
                    continue

                tokens_used  = 0
                attributed   = 0
                unattributed = 0

                for chunk in chunks:
                    if tokens_used >= MAX_TOKENS_PER_RUN:
                        log("BUDGET_REACHED", tokens_used=tokens_used, limit=MAX_TOKENS_PER_RUN)
                        break

                    chunk_id = chunk["id"]
                    t0 = time.monotonic()

                    try:
                        course_code, confidence, in_tok, out_tok = await classify_chunk(
                            ai, chunk.get("content") or ""
                        )
                    except Exception as exc:
                        log("CLASSIFY_ERROR", id=chunk_id, error=str(exc)[:120])
                        continue

                    tokens_used += in_tok + out_tok
                    duration_ms  = int((time.monotonic() - t0) * 1000)

                    ai_run_id = await create_ai_run(
                        http, chunk_id, course_code, confidence,
                        in_tok, out_tok, duration_ms,
                    )

                    await update_chunk(http, chunk_id, course_code if confidence >= CONFIDENCE_THRESHOLD else None,
                                       confidence, ai_run_id)

                    if course_code and confidence >= CONFIDENCE_THRESHOLD:
                        log("ATTRIBUTED", id=chunk_id[:8], course=course_code,
                            confidence=f"{confidence:.2f}", tokens=in_tok + out_tok)
                        attributed += 1
                    else:
                        log("UNATTRIBUTABLE", id=chunk_id[:8],
                            confidence=f"{confidence:.2f}", tokens=in_tok + out_tok)
                        unattributed += 1

                log("BATCH_DONE", attributed=attributed, unattributable=unattributed,
                    tokens_used=tokens_used)

            except Exception as exc:
                log("WORKER_ERROR", error=str(exc)[:200])

            await asyncio.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
