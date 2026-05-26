import os
import asyncio
import json
import httpx
from openai import AsyncOpenAI

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

client = AsyncOpenAI(api_key=OPENAI_API_KEY)

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
    print("RUMMAN INTELLIGENCE WORKER STARTED")

    async with httpx.AsyncClient(timeout=60) as http:
        while True:
            try:
                messages = await fetch_messages(http)

                for msg in messages:
                    await analyze_message(http, msg)

            except Exception as e:
                print("WORKER_ERROR", str(e))

            await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(main())
