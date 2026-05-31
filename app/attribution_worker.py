#!/usr/bin/env python3
"""
attribution_worker.py — AI-assisted course attribution for untagged document chunks.

Processes document_chunks where attribution_status='original' AND course_code IS NULL.
Calls GPT-4o-mini to suggest a course code. High-confidence (>=0.85) results are
written as 'machine_asserted' with a full ai_runs provenance trail.

All attributions remain machine_asserted until confirmed by downstream validation.
False attribution (wrong course) is worse than no attribution — threshold is intentionally high.

Enable: set ATTRIBUTION_WORKER_ENABLED=true in Railway environment.

RPD budget: ATTRIBUTION_MAX_DAILY_CALLS (default 3000) caps gpt-4o-mini requests per
UTC day, leaving room for search API synthesis and daily_brief runs.
"""
from __future__ import annotations

import os
import re
import json
import asyncio
import time
from datetime import datetime, timezone, timedelta
import httpx
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL  = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY  = os.environ.get("SUPABASE_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

SEU_TENANT_ID = "00000000-0000-0000-0000-000000000001"
MODEL         = "gpt-4o-mini"
BATCH_SIZE           = int(os.getenv("ATTRIBUTION_BATCH_SIZE", "20"))
SLEEP_SECONDS        = int(os.getenv("ATTRIBUTION_SLEEP_SECONDS", "120"))
CONFIDENCE_THRESHOLD = 0.85
MAX_TOKENS_PER_RUN   = int(os.getenv("ATTRIBUTION_MAX_TOKENS_PER_RUN", "500_000"))
# Hard cap on gpt-4o-mini RPD usage. OpenAI free tier: 10K/day shared with search API
# and daily_brief. At 3K/day attribution worker finishes 11K remaining in ~4 days.
MAX_DAILY_CALLS      = int(os.getenv("ATTRIBUTION_MAX_DAILY_CALLS", "3000"))

# Regex-first: attribute chunks where a single SEU course code is explicit in the text.
# Saves AI calls; pattern: 2-6 uppercase letters followed by exactly 3 digits.
_COURSE_CODE_RE = re.compile(r'\b([A-Z]{2,6}\d{3})\b')

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


def regex_attribute(content: str) -> tuple[str | None, float]:
    """Return (course_code, 1.0) if exactly one SEU code appears explicitly in the text."""
    codes = list(set(_COURSE_CODE_RE.findall((content or "")[:1200])))
    if len(codes) == 1:
        return codes[0], 1.0
    return None, 0.0


def seconds_until_utc_midnight() -> int:
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return max(60, int((tomorrow - now).total_seconds()))


async def fetch_unattributed(http: httpx.AsyncClient) -> list[dict]:
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/document_chunks",
        headers=HEADERS,
        params={
            "attribution_status": "eq.original",
            "course_code":        "is.null",
            "embedding":          "not.is.null",
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
    resp = await asyncio.wait_for(
        ai.chat.completions.create(
            model=MODEL,
            temperature=0.0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _ATTRIBUTION_SYSTEM},
                {"role": "user",   "content": (content or "")[:800]},
            ],
        ),
        timeout=30,
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
            "run_type":       "course_attribution",
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
        "tenant_id":              SEU_TENANT_ID,
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
        while True:
            await asyncio.sleep(86400)

    ai = AsyncOpenAI(api_key=OPENAI_API_KEY)
    log("ATTRIBUTION_WORKER_START", batch_size=BATCH_SIZE, threshold=CONFIDENCE_THRESHOLD,
        max_tokens=MAX_TOKENS_PER_RUN, daily_call_cap=MAX_DAILY_CALLS)

    # Per-day API call counter — resets at UTC midnight.
    # Prevents the attribution worker from exhausting the shared gpt-4o-mini RPD
    # that is also used by search_api synthesis and daily_brief.
    daily_calls = 0
    daily_calls_day: str | None = None

    async with httpx.AsyncClient(timeout=30) as http:
        # Recover daily_calls from last heartbeat so restart within same day
        # doesn't reset the budget counter and allow over-spending.
        try:
            r = await http.get(
                f"{SUPABASE_URL}/rest/v1/worker_heartbeats",
                headers=HEADERS,
                params={"worker_id": "eq.attribution_worker", "select": "last_seen_at,metadata"},
            )
            if r.status_code == 200 and r.json():
                row = r.json()[0]
                last_seen = (row.get("last_seen_at") or "")[:10]  # YYYY-MM-DD
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if last_seen == today:
                    recovered = int((row.get("metadata") or {}).get("daily_calls", 0))
                    if recovered > 0:
                        daily_calls = recovered
                        daily_calls_day = today
                        log("DAILY_CALLS_RECOVERED", daily_calls=daily_calls, limit=MAX_DAILY_CALLS)
        except Exception as e:
            log("DAILY_CALLS_RECOVERY_FAILED", error=str(e)[:80])

        try:
            from heartbeat import Heartbeat
            hb = Heartbeat(http, worker_id="attribution_worker", process="attribution", interval_s=60)
        except Exception as e:
            log("HEARTBEAT_IMPORT_ERROR", error=str(e))
            hb = None

        while True:
            try:
                # Reset daily counter at UTC day boundary
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if daily_calls_day != today:
                    daily_calls_day = today
                    daily_calls = 0
                    log("DAILY_COUNTER_RESET", date=today, limit=MAX_DAILY_CALLS)

                if daily_calls >= MAX_DAILY_CALLS:
                    wait = seconds_until_utc_midnight()
                    log("DAILY_BUDGET_EXHAUSTED", calls=daily_calls,
                        limit=MAX_DAILY_CALLS, sleep_s=wait)
                    if hb:
                        await hb.beat(status="budget_paused",
                                      metadata={"daily_calls": daily_calls, "limit": MAX_DAILY_CALLS})
                    await asyncio.sleep(wait)
                    continue

                chunks = await fetch_unattributed(http)

                if not chunks:
                    log("IDLE", msg="no unattributed chunks remaining")
                    if hb:
                        await hb.beat(status="idle")
                    await asyncio.sleep(SLEEP_SECONDS)
                    continue

                tokens_used  = 0
                attributed   = 0
                regex_attributed = 0
                unattributed = 0

                for chunk in chunks:
                    if tokens_used >= MAX_TOKENS_PER_RUN:
                        log("TOKEN_BUDGET_REACHED", tokens_used=tokens_used, limit=MAX_TOKENS_PER_RUN)
                        break
                    if daily_calls >= MAX_DAILY_CALLS:
                        log("DAILY_BUDGET_REACHED", calls=daily_calls, limit=MAX_DAILY_CALLS)
                        break

                    chunk_id = chunk["id"]
                    content  = chunk.get("content") or ""

                    # Regex-first: if exactly one course code is explicit in the text,
                    # skip the AI call — same outcome, zero cost.
                    regex_code, regex_conf = regex_attribute(content)
                    if regex_code:
                        await update_chunk(http, chunk_id, regex_code, regex_conf, None)
                        log("ATTRIBUTED_REGEX", id=chunk_id[:8], course=regex_code)
                        regex_attributed += 1
                        continue

                    t0 = time.monotonic()
                    try:
                        course_code, confidence, in_tok, out_tok = await classify_chunk(
                            ai, content
                        )
                    except Exception as exc:
                        log("CLASSIFY_ERROR", id=chunk_id, error=str(exc)[:120])
                        continue

                    daily_calls  += 1
                    tokens_used  += in_tok + out_tok
                    duration_ms   = int((time.monotonic() - t0) * 1000)

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

                log("BATCH_DONE", attributed_ai=attributed, attributed_regex=regex_attributed,
                    unattributable=unattributed, tokens_used=tokens_used,
                    daily_calls=daily_calls, daily_limit=MAX_DAILY_CALLS)
                if hb:
                    await hb.beat(
                        status="running",
                        metadata={"attributed": attributed + regex_attributed,
                                   "unattributable": unattributed,
                                   "tokens": tokens_used,
                                   "daily_calls": daily_calls},
                    )

            except Exception as exc:
                log("WORKER_ERROR", error=str(exc)[:200])
                if hb:
                    await hb.beat(status="error", metadata={"error": str(exc)[:200]})

            await asyncio.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
