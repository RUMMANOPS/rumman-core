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
from datetime import datetime
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

_BATCH_SYSTEM = """\
You are building a normalization dictionary for a Saudi university student assistant.
You will be given a specific vocabulary category to expand.

REQUIRED OUTPUT FORMAT — return exactly this JSON object, nothing else:
{
  "entries": [
    {
      "surface": "original Gulf/Saudi dialect form (word or short phrase)",
      "canonical": "clear MSA Arabic equivalent",
      "category": "category label",
      "notes": "one-line usage context"
    }
  ]
}

Rules:
- Each entry must have non-empty surface AND canonical
- Do not include terms already in standard MSA
- canonical must be MSA Arabic, never another dialect form
- For phrases: only include if the phrase as a whole deserves a different canonical than its parts\
"""

_BATCHES = [
    {
        "label": "grammar",
        "prompt": """\
Generate 70 entries for Gulf/Saudi Arabic grammar forms used by university students.
Include:
- Question words and their spelling variants (وش، شو، ايش، إيش، ليش، ليه، وين، فين، مين، متين، كيفاش، كيفها، امتى)
- Common Gulf verbs in all conjugations (يجي/جاء/جاب، يطلع/طلع، يشوف/شاف، يحط/حط، يقلّب، يدور)
- Desire/request verbs (ابغى/بغيت/بغى، بدي/بدك/بده، نبي، ودّي، اعطني، تعطني)
- Negation forms (مو، مافي، ماعندي، ما راح، مو صح، مو كذا)
- Intensifiers and fillers (والله، يالله، يعني، بس، كلش، زين، تمام، صح)
- Prepositions with attached definite articles (بالاختبار، بالميدترم، بالفاينل، بالكورس، بالمادة، بالفصل، عالكورس، عالاختبار، للاختبار، للمادة)
category label for all: use the specific sub-type (question_word, verb, negation, preposition, filler)"""
    },
    {
        "label": "exam_academic",
        "prompt": """\
Generate 70 entries for Gulf/Saudi Arabic exam and academic vocabulary.
Include:
- Exam types with and without definite article (ميدترم، الميدترم، فاينل، الفاينل، فينال، الفينال، كويز، الكويز، كويزات)
- Academic deliverables (اسايمنت، اسيانمنت، الاسايمنت، اسايمنتات، برجكت، بروجكت، البرجكت، برجكتات)
- Course materials (سلايد، سلايدات، سلايدز، نوتس، نوتز، شيت، شيتات، بوربوينت، بي بي)
- Course/program words (كورس، الكورس، كورسات، ترم، الترم، ترمات، سيميستر، فصل)
- Exam compilations (تجميع، التجميع، تجميعات، التجميعات)
- Study resources (ملخص، ملخصات، مراجعة، مراجعات، مذكرة، مذكرات)
- Platforms (بلاكبورد، بلاك بورد، منصة التعلم)
category label: exam_term or resource"""
    },
    {
        "label": "people_expressions",
        "prompt": """\
Generate 70 entries for Gulf/Saudi Arabic expressions about people, urgency, and comprehension.
Include:
- Professor/instructor titles with variants (دكتور، دكتورة، دكتوره، الدكتور، الدكتورة، الدكتوره، المدرس، المدرسة، استاذ، استاذة، الاستاذ، الاستاذة)
- Urgency and panic phrases (خلص ما في وقت، ما فاضل وقت، قريب الاختبار، قريب الميدترم، قريب الفاينل، ما عندي وقت، والله خايف، ضاغط)
- Emotional markers (يا الله، الله يعينني، خلاص، انتهيت، مو قادر، مو قادرة)
- Comprehension and confusion (ما فهمت، ما فهمت شي، يعني ايش، وش يقصد، ما ادري، ما ادري وش، شو يعني، شرح لي)
- Request patterns (ابغى اعرف، بدي افهم، ابغى شوف، وين الملخص، وين التجميعات، فيه سلايدات، فيه تجميعات)
category label: person, urgency, or comprehension"""
    },
    {
        "label": "telegram_phrases",
        "prompt": """\
Generate 70 entries for compressed Telegram expressions and deadline phrases.
Include:
- Deadline/schedule phrases (امتى التسليم، متى التسليم، قبل الميدترم، بعد الفاينل، آخر موعد، تاريخ التسليم، موعد التسليم)
- Common Telegram abbreviations and compressed phrases (هل فيه، وش صار، شو في، ايش في، وش جاب، شو جاب، ايش يجي، وش يجيب)
- Multi-word exam content phrases (وش يجي بالاختبار، شو يجي بالميدترم، وش جاء بالفاينل، ايش يطلع بالكويز، ما الي يجي، شو يحب يسأل، وش يجيب الدكتور)
- Context-framing phrases (في هالكورس، في هالمادة، في هالفصل، هالترم، هالاختبار، هالميدترم، هذا الترم، الفصل الحالي)
- Common compressed requests (ابي ملخص، بدي تجميع، نبي سلايدات، وش تنصح، شو اذاكر)
category label: deadline, phrase, or exam_topics"""
    },
    {
        "label": "mixed_morphological",
        "prompt": """\
Generate 70 entries for mixed Arabic-English terms and morphological variants.
Include:
- Mixed Arabic-English academic expressions (انا fail، كورس heavy، الداكتر strict، الكورس boring، اخذت A، رسبت بالكورس، pass وش يحتاج)
- Misspellings and variant spellings of academic transliterations (اسايمنت/اسيانمنت/اسيجنمنت، برجكت/بروجكت/بروجيكت، سلايد/سلايدة، ميدترم/ميدتيرم، فاينل/فينال/فينل)
- Morphological variants with different prefixes (وبالاختبار، وبالميدترم، فبالفاينل، للاسايمنت، بالاسايمنت، الاسايمنتات، الميدترمات)
- Gulf dialect constructions with pronouns (وشك، وشه، وشها، وشهم، ليشك، عندك، عنده، عندها، عندهم)
- Common student register expressions (الله يعطيك العافية، ياليت، حياك، الله يسعدك، ما قصرت)
category label: mixed, morphological, or social"""
    },
]


async def _generate_batch(ai: AsyncOpenAI, batch: dict) -> list[dict]:
    """Run one generation batch. Returns list of entry dicts."""
    resp = await asyncio.wait_for(
        ai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": _BATCH_SYSTEM},
                {"role": "user",   "content": batch["prompt"]},
            ],
            temperature=0.3,
            max_tokens=5000,
            response_format={"type": "json_object"},
        ),
        timeout=120,
    )

    raw = json.loads(resp.choices[0].message.content)

    if isinstance(raw, list):
        entries = raw
    elif isinstance(raw, dict):
        entries = raw.get("entries") or raw.get("items") or raw.get("lexicon") or []
        if not entries:
            list_values = [v for v in raw.values() if isinstance(v, list)]
            entries = list_values[0] if list_values else []
    else:
        entries = []

    return [e for e in entries if isinstance(e, dict)]


async def generate() -> list[dict]:
    ai = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

    print(f"Running {len(_BATCHES)} parallel batches (~$0.30–0.50 total)...")
    tasks = [_generate_batch(ai, b) for b in _BATCHES]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_entries: list[dict] = []
    for batch, result in zip(_BATCHES, results):
        if isinstance(result, Exception):
            print(f"  WARN: batch '{batch['label']}' failed: {result}")
            continue
        print(f"  batch '{batch['label']}': {len(result)} entries")
        all_entries.extend(result)

    # Deduplicate by surface form (keep first occurrence)
    seen: set[str] = set()
    deduped = []
    for e in all_entries:
        key = (e.get("surface") or "").strip()
        if key and key not in seen:
            seen.add(key)
            deduped.append(e)

    print(f"Total after dedup: {len(deduped)} candidates.")
    return deduped


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
        f"seed_candidates_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
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
