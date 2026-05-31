#!/usr/bin/env python3
"""
extract_exam_signals.py — Extract recurring exam topics from exam-tagged chunks.

Uses gpt-4o-mini to analyze exam archive chunks per (course_code, exam_type) and
identify the top recurring academic topics. Results stored in exam_intelligence table
and injected into the synthesis context bundle.

Cost: ~$0.001/course × courses with exam content ≈ $0.10-0.20 total (one-time).
Refresh: monthly, or after significant new exam content is ingested.

Usage:
    python3 scripts/extract_exam_signals.py [--dry-run] [--course IT362] [--min-chunks 5]
"""

import os
import sys
import json
import httpx
import argparse
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL   = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
SEU_TENANT_ID  = "00000000-0000-0000-0000-000000000001"

REST_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}

openai = OpenAI(api_key=OPENAI_API_KEY)

EXTRACT_SYSTEM = """\
You are an academic analyst. You will receive a collection of exam-related text chunks from a university course.
Your task: identify the top 5-8 recurring academic topics or concepts that appear most frequently across these chunks.

Rules:
- Focus on substantive academic content: theories, concepts, procedures, formulas, systems, models
- Ignore administrative content (dates, instructor names, grading policies)
- Use short, specific topic names (e.g. "OSI Model", "Net Present Value", "Database Normalization")
- Return ONLY a JSON object with this exact structure:
  {"topics": ["Topic 1", "Topic 2", ...], "confidence": "low|medium|high"}
- confidence = "high" if 10+ chunks, "medium" if 5-9, "low" if fewer than 5
- No explanation, no markdown, just the JSON object."""

EXAM_TYPES = {
    "midterm": ["midterm", "ميدترم", "mid"],
    "final":   ["final", "فاينل", "فينال", "نهائي"],
    "quiz":    ["quiz", "كويز"],
}


def fetch_exam_chunks(http: httpx.Client, course_code: str) -> list[dict]:
    """Fetch all exam-tagged chunks for a course."""
    r = http.get(
        f"{SUPABASE_URL}/rest/v1/document_chunks",
        headers={**REST_HEADERS, "Prefer": ""},
        params={
            "course_code": f"eq.{course_code}",
            "source_type": "eq.exam",
            "tenant_id":   f"eq.{SEU_TENANT_ID}",
            "select":      "content,metadata",
            "limit":       "300",
        },
        timeout=30,
    )
    if r.status_code >= 400:
        print(f"  ERROR fetching chunks for {course_code}: {r.status_code}")
        return []
    return r.json()


def classify_exam_type(chunk: dict) -> str:
    """Infer exam type from content or metadata."""
    meta    = chunk.get("metadata") or {}
    content = (chunk.get("content") or "").lower()
    source  = (meta.get("source_name") or "").lower()

    combined = content[:200] + " " + source
    for etype, keywords in EXAM_TYPES.items():
        if any(kw in combined for kw in keywords):
            return etype
    return "general"


def extract_topics_for_type(course_code: str, exam_type: str, chunks: list[dict], dry_run: bool):
    """Call gpt-4o-mini to extract recurring topics from a set of chunks."""
    if not chunks:
        return None

    # Sample up to 60 chunks (token budget: ~12K input tokens at ~200 chars each)
    sample = chunks[:60]
    combined_text = "\n\n---\n\n".join(
        (c.get("content") or "")[:400].strip() for c in sample
    )

    confidence = "high" if len(chunks) >= 10 else ("medium" if len(chunks) >= 5 else "low")

    if dry_run:
        print(f"    [DRY RUN] Would extract topics for {course_code}/{exam_type} ({len(chunks)} chunks)")
        return {
            "topics": ["[dry run — no extraction]"],
            "confidence": confidence,
            "source_count": len(chunks),
        }

    try:
        resp = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": EXTRACT_SYSTEM},
                {"role": "user",   "content": f"Course: {course_code}\nExam type: {exam_type}\n\nChunks:\n{combined_text[:10000]}"},
            ],
            max_tokens=200,
            temperature=0.1,
        )
        raw = resp.choices[0].message.content.strip()
        data = json.loads(raw)
        topics = data.get("topics", [])
        if not isinstance(topics, list):
            topics = []
        return {
            "topics":       topics[:8],
            "confidence":   confidence,
            "source_count": len(chunks),
            "tokens_used":  resp.usage.total_tokens if resp.usage else 0,
        }
    except Exception as exc:
        print(f"    ERROR extracting {course_code}/{exam_type}: {exc}")
        return None


def upsert_exam_intelligence(http: httpx.Client, course_code: str, exam_type: str, result: dict) -> None:
    row = {
        "course_code":  course_code,
        "tenant_id":    SEU_TENANT_ID,
        "exam_type":    exam_type,
        "top_topics":   result["topics"],
        "source_count": result["source_count"],
        "confidence":   result["confidence"],
        "extracted_at": "now()",
    }
    r = http.post(
        f"{SUPABASE_URL}/rest/v1/exam_intelligence",
        headers={**REST_HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
        params={"on_conflict": "course_code,tenant_id,exam_type"},
        json=row,
        timeout=15,
    )
    if r.status_code >= 400:
        print(f"    ERROR upserting exam_intelligence: {r.status_code} {r.text[:200]}")


def fetch_courses_with_exam_chunks(http: httpx.Client, min_chunks: int) -> list[str]:
    """Return course codes that have at least min_chunks exam-tagged chunks."""
    r = http.get(
        f"{SUPABASE_URL}/rest/v1/course_intelligence_profiles",
        headers={**REST_HEADERS, "Prefer": ""},
        params={
            "tenant_id":   f"eq.{SEU_TENANT_ID}",
            "exam_chunks": f"gte.{min_chunks}",
            "select":      "course_code,exam_chunks",
            "limit":       "500",
        },
        timeout=15,
    )
    if r.status_code >= 400:
        # Fallback: query document_chunks directly if profiles not yet built
        print("  course_intelligence_profiles not ready — falling back to direct chunk query")
        r2 = http.get(
            f"{SUPABASE_URL}/rest/v1/document_chunks",
            headers={**REST_HEADERS, "Prefer": ""},
            params={
                "source_type": "eq.exam",
                "tenant_id":   f"eq.{SEU_TENANT_ID}",
                "select":      "course_code",
                "limit":       "10000",
            },
            timeout=30,
        )
        if r2.status_code >= 400:
            print(f"  ERROR: {r2.status_code} {r2.text[:200]}")
            return []
        from collections import Counter
        counts = Counter(row["course_code"] for row in r2.json() if row.get("course_code"))
        return [code for code, cnt in counts.items() if cnt >= min_chunks]
    return [row["course_code"] for row in r.json()]


def main():
    parser = argparse.ArgumentParser(description="Extract exam topic signals")
    parser.add_argument("--dry-run",     action="store_true", help="Print plan without calling OpenAI")
    parser.add_argument("--course",      type=str, default=None, help="Process only one course")
    parser.add_argument("--min-chunks",  type=int, default=5,    help="Minimum exam chunks required (default: 5)")
    args = parser.parse_args()

    total_tokens = 0
    processed    = 0
    skipped      = 0

    with httpx.Client() as http:
        if args.course:
            courses = [args.course.upper()]
        else:
            print(f"Fetching courses with ≥{args.min_chunks} exam chunks...")
            courses = fetch_courses_with_exam_chunks(http, args.min_chunks)
            print(f"  {len(courses)} courses qualify: {', '.join(sorted(courses)[:20])}{'...' if len(courses)>20 else ''}")

        for course_code in sorted(courses):
            print(f"\n{course_code}")
            chunks = fetch_exam_chunks(http, course_code)
            if not chunks:
                print("  No exam chunks found, skipping.")
                skipped += 1
                continue

            # Separate chunks by exam type
            by_type: dict[str, list] = {}
            for chunk in chunks:
                etype = classify_exam_type(chunk)
                by_type.setdefault(etype, []).append(chunk)

            for exam_type, type_chunks in sorted(by_type.items()):
                print(f"  {exam_type}: {len(type_chunks)} chunks → ", end="", flush=True)
                result = extract_topics_for_type(course_code, exam_type, type_chunks, args.dry_run)
                if result:
                    topics_str = ", ".join(result["topics"][:5]) if result["topics"] else "(none)"
                    print(f"[{result['confidence']}] {topics_str}")
                    total_tokens += result.get("tokens_used", 0)
                    if not args.dry_run:
                        upsert_exam_intelligence(http, course_code, exam_type, result)
                    processed += 1
                else:
                    print("SKIP")
                    skipped += 1

    estimated_cost = total_tokens * 0.00000015  # gpt-4o-mini blended rate
    print(f"\n{'='*60}")
    print(f"Processed: {processed} | Skipped: {skipped}")
    print(f"Tokens used: {total_tokens:,} | Estimated cost: ${estimated_cost:.4f}")
    if args.dry_run:
        print("[DRY RUN] No writes or OpenAI calls performed.")


if __name__ == "__main__":
    main()
