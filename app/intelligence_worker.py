"""
intelligence_worker.py — DISABLED. NOT SAFE TO RUN.

This worker has three unresolved problems that make it economically dangerous:

  1. NO CURSOR TRACKING — fetches the same 20 rows every 30 seconds. The same
     messages are re-processed indefinitely: 2,880+ LLM calls per hour at steady state.

  2. NO DEDUPLICATION — intelligence_items has no idempotency key. Each run inserts
     duplicate rows for the same messages.

  3. NO COST CEILING — no per-tenant or per-run budget check before API calls.

DO NOT add this to the Procfile or run it manually until all three are resolved.
DO NOT set INTELLIGENCE_WORKER_ENABLED=true in any environment until:
  - cursor/watermark tracking is implemented
  - source_message_id uniqueness is enforced in intelligence_items
  - a cost ceiling is implemented and tested

See docs/constraints/hard-boundaries.md and the AI runtime audit for full context.
Estimated worst-case cost if enabled today: $50–$200+/month on low traffic.
"""

import os
import asyncio
import json
import httpx
from openai import AsyncOpenAI

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

# Hard gate — must be explicitly set to "true" to allow this worker to run.
# The default is disabled. Setting this to true without resolving the issues
# above is an explicit choice to accept the cost risk.
_ENABLED = os.getenv("INTELLIGENCE_WORKER_ENABLED", "").strip().lower() == "true"

# Client is intentionally not initialized at module level — credentials are only
# required when the worker is actually enabled.
client = None

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

SYSTEM_PROMPT = """
You are RUMMAN Intelligence Engine.

Analyze university/group chat messages.

Extract ONLY meaningful operational intelligence such as:
- assignment
- quiz
- exam
- deadline
- meeting
- decision
- reminder
- important academic announcement

Return valid JSON array only.

Example:
[
  {
    "item_type": "assignment",
    "title": "Marketing Assignment 3",
    "description": "Submit before Thursday",
    "confidence": 0.91
  }
]
"""

async def fetch_messages(http):
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/messages?select=*&order=created_at.desc&limit=20",
        headers=HEADERS
    )

    return r.json()

async def save_item(http, item, msg):
    payload = {
        "source_platform": "telegram",
        "source_chat_id": str(msg.get("platform_chat_id")),
        "source_message_id": str(msg.get("platform_message_id")),
        "item_type": item.get("item_type"),
        "title": item.get("title"),
        "description": item.get("description"),
        "confidence": item.get("confidence", 0.5),
        "metadata": {
            "raw_message": msg.get("content")
        }
    }

    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/intelligence_items",
        headers=HEADERS,
        json=payload
    )

    print("INTELLIGENCE_SAVED", r.status_code)

async def analyze_message(http, msg):
    content = msg.get("content")

    if not content:
        return

    try:
        response = await client.chat.completions.create(
            model="gpt-4.1-mini",
            temperature=0.1,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content}
            ]
        )

        raw = response.choices[0].message.content

        print("AI_RESPONSE", raw)

        items = json.loads(raw)

        for item in items:
            await save_item(http, item, msg)

    except Exception as e:
        print("INTELLIGENCE_ERROR", str(e))

async def main():
    if not _ENABLED:
        print(
            "INTELLIGENCE_WORKER_DISABLED | "
            "set INTELLIGENCE_WORKER_ENABLED=true to override | "
            "read module docstring for required preconditions before enabling",
            flush=True,
        )
        return

    global client
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

    print("INTELLIGENCE_WORKER_START | WARNING: cursor/dedup/cost-ceiling not implemented", flush=True)

    async with httpx.AsyncClient(timeout=60) as http:
        while True:
            try:
                messages = await fetch_messages(http)

                for msg in messages:
                    await analyze_message(http, msg)

            except Exception as e:
                print(f"WORKER_ERROR | error={str(e)}", flush=True)

            await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(main())
