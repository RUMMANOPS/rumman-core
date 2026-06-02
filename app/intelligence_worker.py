"""
intelligence_worker.py — Phase 2 intelligence extraction.

Reads new messages (cursor-tracked), calls gpt-4o-mini to extract operational
items (assignments, deadlines, decisions, etc.), writes to intelligence_items.

Deployed in Procfile. Gated by INTELLIGENCE_WORKER_ENABLED=true (default: off).
Set the env var in Railway to activate.

Preconditions (already satisfied as of Phase 2):
  1. supabase/migrations/011_intelligence_layer.sql applied
  2. INTELLIGENCE_WORKER_ENABLED=true in Railway environment
  3. Review INTELLIGENCE_MAX_TOKENS_PER_RUN budget for current message volume
"""

import os
import asyncio
import json
import httpx
from openai import AsyncOpenAI

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

SEU_TENANT_ID = os.environ.get("SEU_TENANT_ID", "00000000-0000-0000-0000-000000000001")
WORKER_ID     = f"intelligence_worker_{SEU_TENANT_ID}"
BATCH_SIZE    = int(os.getenv("INTELLIGENCE_BATCH_SIZE", "50"))
SLEEP_SECONDS = int(os.getenv("INTELLIGENCE_SLEEP_SECONDS", "60"))
# Token budget per run — prevents runaway spend on large backlogs.
# gpt-4o-mini: ~$0.15/1M input + $0.60/1M output.
# At default 200K limit: max ~$0.03–$0.12 per run depending on input/output mix.
MAX_TOKENS_PER_RUN = int(os.getenv("INTELLIGENCE_MAX_TOKENS_PER_RUN", "200_000"))
CONFIDENCE_THRESHOLD = 0.65
MODEL = "gpt-4o-mini"

# Hard gate — must be explicitly set to prevent accidental activation.
_ENABLED = os.getenv("INTELLIGENCE_WORKER_ENABLED", "").strip().lower() == "true"

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

SYSTEM_PROMPT = """\
You are رمّان (Rummaan) Intelligence Engine analyzing Saudi university Telegram messages.

Extract ONLY items with clear operational value:
- assignment: homework, project, deliverable
- quiz: announced quiz or test
- exam: exam date or announcement
- deadline: any submission or registration deadline
- meeting: study group, lecture change, or office hours announcement
- decision: official change affecting students (exam moved online, cancelled class)
- reminder: explicit reminder about an upcoming item
- announcement: important academic news with no other fitting type

Rules:
1. Conservative — when in doubt, omit.
2. Skip: greetings, reactions, religious content, social chat, jokes.
3. Course codes (IT484, MGT311) signal academic importance.
4. Confidence: 0.9+ = explicitly stated; 0.7–0.9 = clearly implied; below 0.7 = omit.
5. An empty items array is valid and often correct.
6. due_date: ISO YYYY-MM-DD only if a specific date is stated, else null.
7. course_code: exact code if present (e.g. "IT484"), else null.

Return ONLY valid JSON in this exact format (no text outside JSON):
{"items": [
  {
    "item_type": "deadline",
    "title": "Short title (max 80 chars)",
    "description": "Full detail from the message",
    "confidence": 0.88,
    "due_date": "2026-06-01",
    "course_code": "IT484"
  }
]}
If nothing qualifies, return {"items": []}\
"""


def log(event: str, **kwargs):
    parts = [event] + [f"{k}={v}" for k, v in kwargs.items()]
    print(" | ".join(parts), flush=True)


async def get_cursor(http: httpx.AsyncClient) -> str | None:
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/worker_cursors",
        headers=HEADERS,
        params={"worker_id": f"eq.{WORKER_ID}", "select": "last_cursor", "limit": "1"},
    )
    if r.status_code >= 400 or not r.json():
        return None
    return r.json()[0].get("last_cursor")


async def save_cursor(http: httpx.AsyncClient, cursor: str) -> None:
    await http.post(
        f"{SUPABASE_URL}/rest/v1/worker_cursors",
        headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
        params={"on_conflict": "worker_id"},
        json={"worker_id": WORKER_ID, "tenant_id": SEU_TENANT_ID,
              "last_cursor": cursor, "updated_at": "now()"},
    )


async def fetch_messages(http: httpx.AsyncClient, after_id: str | None) -> list[dict]:
    params: list[tuple] = [
        ("select", "id,platform_chat_id,chat_name,sender_name,message_text,message_date"),
        ("message_type", "eq.text"),
        ("tenant_id", f"eq.{SEU_TENANT_ID}"),
        ("order", "id.asc"),
        ("limit", str(BATCH_SIZE)),
    ]
    if after_id:
        params.append(("id", f"gt.{after_id}"))

    r = await http.get(f"{SUPABASE_URL}/rest/v1/messages", headers=HEADERS, params=params)
    if r.status_code >= 400:
        log("FETCH_ERROR", status=r.status_code)
        return []
    return [m for m in r.json() if (m.get("message_text") or "").strip()]


async def extract_items(ai: AsyncOpenAI, message_text: str) -> tuple[list[dict], int]:
    """Call gpt-4o-mini. Returns (items, total_tokens)."""
    resp = await asyncio.wait_for(
        ai.chat.completions.create(
            model=MODEL,
            temperature=0.1,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": message_text},
            ],
        ),
        timeout=30,
    )
    raw = resp.choices[0].message.content or "{}"
    tokens = resp.usage.total_tokens if resp.usage else 0
    # Model returns a JSON object with an "items" key or a bare JSON array
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return [], tokens
    items = parsed if isinstance(parsed, list) else parsed.get("items", [])
    return items, tokens


async def save_item(http: httpx.AsyncClient, item: dict, msg: dict) -> str:
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/intelligence_items",
        headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
        params={"on_conflict": "tenant_id,source_platform,source_message_id,item_type"},
        json={
            "tenant_id":         SEU_TENANT_ID,
            "source_platform":   "telegram",
            "source_chat_id":    str(msg.get("platform_chat_id", "")),
            "source_message_id": str(msg.get("id", "")),
            "item_type":         item["item_type"],
            "title":             (item.get("title") or "")[:200],
            "description":       item.get("description"),
            "due_date":          item.get("due_date"),
            "course_code":       item.get("course_code"),
            "confidence":        float(item.get("confidence", 0.5)),
            "metadata":          {"chat_name": msg.get("chat_name"), "sender": msg.get("sender_name")},
        },
    )
    if r.status_code == 409:
        return "duplicate"
    if r.status_code >= 400:
        log("SAVE_ERROR", status=r.status_code, msg_id=msg.get("id"), error=r.text[:120])
        return "error"
    return "saved"


_EXTRACT_CONCURRENCY = int(os.getenv("INTELLIGENCE_CONCURRENCY", "15"))


async def _process_one(
    ai: AsyncOpenAI,
    sem: asyncio.Semaphore,
    msg: dict,
) -> tuple[dict, list[dict], int]:
    """Extract intelligence items from one message under a concurrency semaphore.
    Always returns (msg, items, tokens) — never raises."""
    text = (msg.get("message_text") or "").strip()
    if not text:
        return msg, [], 0
    async with sem:
        try:
            items, tokens = await extract_items(ai, text)
            return msg, items, tokens
        except Exception as exc:
            log("EXTRACT_ERROR", msg_id=msg["id"], error=str(exc)[:120])
            return msg, [], 0


async def main():
    if not _ENABLED:
        log(
            "INTELLIGENCE_WORKER_DISABLED",
            hint="set INTELLIGENCE_WORKER_ENABLED=true in Railway env",
            precondition_1="migration 011_intelligence_layer.sql must be applied first",
            precondition_2="review INTELLIGENCE_MAX_TOKENS_PER_RUN budget before enabling",
        )
        # Stay alive so Railway keeps service in SUCCESS state (same pattern as audio_worker).
        while True:
            await asyncio.sleep(86400)

    ai = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    sem = asyncio.Semaphore(_EXTRACT_CONCURRENCY)
    log("INTELLIGENCE_WORKER_START", batch_size=BATCH_SIZE, max_tokens=MAX_TOKENS_PER_RUN,
        concurrency=_EXTRACT_CONCURRENCY)

    async with httpx.AsyncClient(timeout=60) as http:
        try:
            from heartbeat import Heartbeat
            hb = Heartbeat(http, worker_id="intelligence_worker", process="intelligence", interval_s=60)
        except Exception as e:
            log("HEARTBEAT_IMPORT_ERROR", error=str(e))
            hb = None

        tokens_used_this_run = 0

        while True:
            try:
                # Budget check between batches — reset each sleep interval.
                if tokens_used_this_run >= MAX_TOKENS_PER_RUN:
                    log("BUDGET_REACHED", tokens_used=tokens_used_this_run, limit=MAX_TOKENS_PER_RUN)
                    if hb:
                        await hb.beat(status="idle", metadata={"budget_exhausted": True})
                    await asyncio.sleep(SLEEP_SECONDS)
                    tokens_used_this_run = 0
                    continue

                cursor = await get_cursor(http)
                messages = await fetch_messages(http, cursor)

                if not messages:
                    log("IDLE", cursor=cursor or "none")
                    if hb:
                        await hb.beat(status="idle", metadata={"cursor": (cursor or "")[:8]})
                    await asyncio.sleep(SLEEP_SECONDS)
                    continue

                # Process entire batch concurrently — all messages start simultaneously,
                # semaphore caps in-flight API calls to _EXTRACT_CONCURRENCY.
                results: list[tuple[dict, list[dict], int]] = await asyncio.gather(
                    *[_process_one(ai, sem, m) for m in messages]
                )

                items_saved = 0
                items_duplicate = 0
                batch_tokens = 0
                last_id = cursor

                for msg, items, tokens in results:
                    batch_tokens += tokens
                    for item in items:
                        if item.get("confidence", 0) < CONFIDENCE_THRESHOLD:
                            continue
                        result = await save_item(http, item, msg)
                        if result == "saved":
                            items_saved += 1
                        elif result == "duplicate":
                            items_duplicate += 1
                    last_id = msg["id"]

                tokens_used_this_run += batch_tokens

                if last_id and last_id != cursor:
                    try:
                        await save_cursor(http, last_id)
                    except Exception as exc:
                        log("CURSOR_SAVE_ERROR", error=str(exc)[:120])
                    log("BATCH_DONE", processed=len(messages), saved=items_saved,
                        duplicate=items_duplicate, tokens=batch_tokens,
                        run_total=tokens_used_this_run, cursor=last_id)
                    if hb:
                        await hb.beat(
                            status="running",
                            metadata={"processed": len(messages), "saved": items_saved,
                                      "tokens": batch_tokens},
                        )

            except Exception as exc:
                log("WORKER_ERROR", error=str(exc))
                if hb:
                    await hb.beat(status="error", metadata={"error": str(exc)[:200]})

            await asyncio.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
