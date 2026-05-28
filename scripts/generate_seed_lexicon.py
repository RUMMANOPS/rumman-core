#!/usr/bin/env python3
"""
generate_seed_lexicon.py — One-time seed generation for normalization_dict.json.

Uses GPT-4o to generate Gulf/Saudi academic student vocabulary candidates.
Output goes to improvement_candidates — nothing is auto-promoted.

Usage:
    python3 scripts/generate_seed_lexicon.py

Writes:
    data/seed_candidates_YYYYMMDD.json   — raw GPT-4o output for review
    Inserts rows into improvement_candidates table (status='pending')

Review with:
    python3 scripts/review_candidates.py

Cost estimate: ~$0.30–0.60 for one full generation run (GPT-4o, ~3000 tokens).
Run once. Never run automatically.
"""

import os
import sys
import json
import asyncio
import httpx
from datetime import date
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

GENERATION_PROMPT = """\
You are helping build a normalization dictionary for a Saudi university student assistant.

Generate a comprehensive list of Gulf/Saudi Arabic academic student vocabulary.
Focus on language used in Saudi Telegram channels by university students.

Categories to cover:
1. Exam panic and urgency phrases ("ما راح يجي في الفاينل", "وش يجي عندكم")
2. Resource requests ("ابغى ملخص", "وين الشيت", "فيه سلايدات")
3. Course-related slang (mixed Arabic-English, abbreviations)
4. Professor behavior references ("الدكتور يحب يجيب", "عادةً الاستاذ يسأل")
5. Deadline and schedule phrases ("امتى التسليم", "قريب الميدترم")
6. Comprehension and confusion ("ما فهمت وش يقول", "يعني ايش")
7. Common misspellings of academic terms in Arabic/transliteration
8. Telegram-compressed expressions (dropped words, abbreviated sentences)
9. Emotional urgency markers ("خلص والله ما عندي وقت")
10. Mixed Arabic-English terms used in academic context

For EACH entry, return:
{
  "surface": "original Gulf/dialect form",
  "canonical": "MSA Arabic equivalent (clear, unambiguous)",
  "category": "one of: question_word | verb | negation | exam_term | resource | deadline | person | urgency | mixed | preposition | filler",
  "notes": "brief note on usage context (optional)"
}

Return a JSON array of 250-350 entries. Prioritize terms you are confident are genuinely used by Saudi university students. Do not invent terms. Do not include terms already in standard MSA.\
"""


async def generate() -> list[dict]:
    ai = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

    print("Calling GPT-4o for seed generation (~$0.30–0.60)...")
    resp = await ai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": GENERATION_PROMPT}],
        temperature=0.3,
        max_tokens=6000,
        response_format={"type": "json_object"},
    )

    raw = json.loads(resp.choices[0].message.content)

    # Handle both {"entries": [...]} and bare array
    if isinstance(raw, list):
        entries = raw
    elif isinstance(raw, dict):
        entries = raw.get("entries", []) or raw.get("items", []) or list(raw.values())[0]
    else:
        entries = []

    print(f"Generated {len(entries)} candidates.")
    return entries


async def insert_candidates(entries: list[dict]) -> int:
    inserted = 0
    async with httpx.AsyncClient(timeout=30) as http:
        for entry in entries:
            surface = (entry.get("surface") or "").strip()
            canonical = (entry.get("canonical") or "").strip()
            if not surface or not canonical:
                continue

            row = {
                "surface_form":  surface,
                "canonical_form": canonical,
                "category":      entry.get("category", "normalization"),
                "source":        "generated",
                "frequency":     1,
                "example_query": entry.get("notes"),
            }
            r = await http.post(
                f"{SUPABASE_URL}/rest/v1/improvement_candidates",
                headers=HEADERS,
                json=row,
            )
            if r.status_code in (200, 201):
                inserted += 1
            elif r.status_code == 409:
                pass  # already exists, skip
            else:
                print(f"  WARN: insert failed for '{surface}': {r.status_code} {r.text[:80]}")

    return inserted


async def main():
    output_path = os.path.join(
        os.path.dirname(__file__), "..", "data",
        f"seed_candidates_{date.today().isoformat()}.json"
    )

    entries = await generate()

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    print(f"Saved raw output to {output_path}")

    inserted = await insert_candidates(entries)
    print(f"Inserted {inserted} candidates into improvement_candidates (status=pending).")
    print(f"\nNext step: python3 scripts/review_candidates.py")


if __name__ == "__main__":
    asyncio.run(main())
