#!/usr/bin/env python3
"""
extract_concepts.py — Two-phase concept extraction for RUMMAN.

Phase 1 — discovery:
  Sample chunks from document_chunks → ask GPT-4o-mini to identify
  academic concepts → seed the concepts table.
  Cost: ~$0.03 for 2000 chunks (100 GPT-4o-mini calls × ~2K tokens each).

Phase 2 — associate:
  For each concept, embed it and find the most similar chunks via pgvector
  → insert into chunk_concepts (chunk_id, concept_id, relevance_weight).
  Cost: ~N_concepts × $0.0001 for embeddings (negligible).

Design:
  - Concepts are vocabulary items: specific, testable, recurring academic noun phrases.
  - Similarity is done corpus-side: embed concept name, find nearest chunks.
  - Phase 2 is idempotent: ON CONFLICT DO NOTHING protects re-runs.
  - Both phases report progress and can be stopped and resumed.

Usage:
  python3 scripts/extract_concepts.py --phase discovery [--sample 2000] [--dry-run]
  python3 scripts/extract_concepts.py --phase associate [--min-sim 0.40] [--dry-run]
  python3 scripts/extract_concepts.py --phase both
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import re
import sys
import time

import httpx
from openai import AsyncOpenAI

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
OPENAI_KEY   = os.environ["OPENAI_API_KEY"]

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}

SEU_TENANT_ID = "00000000-0000-0000-0000-000000000001"
EMBED_MODEL   = "text-embedding-3-large"
EMBED_DIMS    = 1536

# Course prefix → subject area
_SUBJECT_MAP: dict[str, str] = {
    "IT": "it", "CS": "it", "CIS": "it", "MIS": "it", "SE": "it",
    "MGT": "management", "BUS": "management", "HRM": "management",
    "FIN": "finance", "ACC": "finance", "ACCT": "finance", "ECO": "finance",
    "MATH": "engineering", "PHYS": "engineering", "STAT": "engineering",
    "ENGT": "engineering", "CET": "engineering",
    "ARA": "general", "ENG": "general", "GEN": "general", "TRA": "general",
}


def _subject_area(course_code: str | None) -> str:
    if not course_code:
        return "general"
    prefix = re.sub(r"\d", "", (course_code or "").upper()).strip()
    return _SUBJECT_MAP.get(prefix, "general")


# ---------------------------------------------------------------------------
# Phase 1 — Discovery
# ---------------------------------------------------------------------------

_DISCOVERY_SYSTEM = """\
You are an academic concept extractor for a Gulf Arab university corpus.

Given text chunks from university course materials, identify specific academic
concepts covered across them.

Rules:
- Each concept is a noun phrase, 2–5 words, in English, lowercase
- Must be testable/examinable — something that could appear on a midterm or final
- Specific enough to be meaningful, broad enough to recur across multiple questions
- No proper nouns (no professor names, no university names)
- No trivial/obvious terms ("exam", "study", "question")

Return ONLY valid JSON: {"concepts": ["concept one", "concept two", ...]}
Return 5–20 concepts. Quality over quantity.\
"""

_DISCOVERY_USER = """\
Course: {course_code}
Chunks:
{chunks}
"""


async def _call_discovery_batch(
    ai: AsyncOpenAI,
    chunks: list[dict],
) -> list[str]:
    """Ask GPT-4o-mini to extract concepts from a batch of chunks. Returns concept names."""
    course_code = chunks[0].get("course_code") or "unknown"
    chunk_texts = "\n---\n".join(
        (r.get("content") or "")[:400].strip() for r in chunks
    )
    try:
        resp = await asyncio.wait_for(
            ai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _DISCOVERY_SYSTEM},
                    {"role": "user",   "content": _DISCOVERY_USER.format(
                        course_code=course_code, chunks=chunk_texts
                    )},
                ],
                temperature=0,
                max_tokens=500,
                response_format={"type": "json_object"},
            ),
            timeout=15.0,
        )
        raw = json.loads(resp.choices[0].message.content)
        concepts = [
            c.strip().lower()
            for c in raw.get("concepts", [])
            if isinstance(c, str) and c.strip()
        ]
        return concepts
    except Exception as exc:
        log.warning("discovery_batch_failed: %s", exc)
        return []


async def run_discovery(
    sample_size: int = 2000,
    batch_size:  int = 20,
    dry_run:     bool = False,
) -> int:
    """
    Phase 1: Sample chunks → extract concepts → seed concepts table.
    Returns number of new concepts inserted.
    """
    log.info("DISCOVERY_START | sample=%d batch=%d dry_run=%s", sample_size, batch_size, dry_run)
    ai = AsyncOpenAI(api_key=OPENAI_KEY)

    async with httpx.AsyncClient(timeout=30) as http:
        # Fetch sampled chunks
        r = await http.get(
            f"{SUPABASE_URL}/rest/v1/document_chunks",
            headers=HEADERS,
            params={
                "select": "id,content,course_code,source_type,language",
                "limit":  str(sample_size),
                "order":  "id",  # stable order for reproducibility
            },
        )
        if r.status_code >= 400:
            log.error("fetch_chunks_failed: %s", r.text[:200])
            return 0
        chunks = r.json()
        log.info("CHUNKS_FETCHED | count=%d", len(chunks))

    # Shuffle for diverse sampling within the ordered batch
    random.shuffle(chunks)

    # Group chunks by course_code for contextually coherent batches
    by_course: dict[str, list[dict]] = {}
    for c in chunks:
        code = c.get("course_code") or "unknown"
        by_course.setdefault(code, []).append(c)

    all_concept_names: set[str] = set()
    # Track which course each concept first appeared in (for subject_area inference)
    concept_course: dict[str, str] = {}

    total_batches = 0
    for course_code, course_chunks in by_course.items():
        for i in range(0, len(course_chunks), batch_size):
            batch = course_chunks[i : i + batch_size]
            concepts = await _call_discovery_batch(ai, batch)
            for c in concepts:
                if c not in all_concept_names:
                    all_concept_names.add(c)
                    concept_course[c] = course_code
            total_batches += 1
            if total_batches % 10 == 0:
                log.info("DISCOVERY_PROGRESS | batches=%d concepts_so_far=%d",
                         total_batches, len(all_concept_names))
            await asyncio.sleep(0.1)  # light rate-limit courtesy

    log.info("DISCOVERY_COMPLETE | total_batches=%d raw_concepts=%d",
             total_batches, len(all_concept_names))

    if dry_run:
        log.info("DRY_RUN — would insert %d concepts. Sample:", len(all_concept_names))
        for name in sorted(all_concept_names)[:20]:
            log.info("  • %s  [%s]", name, _subject_area(concept_course.get(name)))
        return 0

    # Insert concepts — ON CONFLICT DO NOTHING (canonical_name, tenant_id unique)
    inserted = 0
    async with httpx.AsyncClient(timeout=30) as http:
        for name in sorted(all_concept_names):
            row = {
                "tenant_id":      SEU_TENANT_ID,
                "canonical_name": name,
                "display_name":   name.title(),
                "subject_area":   _subject_area(concept_course.get(name)),
                "language":       "en",
            }
            r = await http.post(
                f"{SUPABASE_URL}/rest/v1/concepts",
                headers={**HEADERS, "Prefer": "return=minimal"},
                json=row,
            )
            if r.status_code in (200, 201):
                inserted += 1
            elif r.status_code == 409:
                pass  # already exists — fine
            else:
                log.warning("concept_insert_failed | name=%s | %s", name, r.text[:100])

    log.info("DISCOVERY_INSERTED | new=%d / total=%d", inserted, len(all_concept_names))
    return inserted


# ---------------------------------------------------------------------------
# Phase 2 — Association
# ---------------------------------------------------------------------------

async def run_association(
    min_sim:    float = 0.40,
    top_k:      int   = 200,
    dry_run:    bool  = False,
) -> int:
    """
    Phase 2: For each concept, embed it → find similar chunks → insert chunk_concepts.
    Returns total chunk_concepts rows inserted.
    """
    log.info("ASSOCIATION_START | min_sim=%.2f top_k=%d dry_run=%s", min_sim, top_k, dry_run)
    ai = AsyncOpenAI(api_key=OPENAI_KEY)

    concepts: list[dict] = []
    async with httpx.AsyncClient(timeout=30) as http:
        # Paginate past PostgREST's 1000-row default cap
        for offset in range(0, 10000, 500):
            r = await http.get(
                f"{SUPABASE_URL}/rest/v1/concepts",
                headers={**HEADERS, "Range-Unit": "items", "Range": f"{offset}-{offset+499}"},
                params={
                    "tenant_id": f"eq.{SEU_TENANT_ID}",
                    "select":    "id,canonical_name,subject_area",
                    "order":     "canonical_name",
                },
            )
            if r.status_code >= 400:
                log.error("fetch_concepts_failed: %s", r.text[:200])
                return 0
            batch = r.json()
            if not batch:
                break
            concepts.extend(batch)
            if len(batch) < 500:
                break
    log.info("CONCEPTS_LOADED | count=%d", len(concepts))

    total_inserted = 0

    async with httpx.AsyncClient(timeout=60) as http:
        for i, concept in enumerate(concepts):
            concept_id   = concept["id"]
            concept_name = concept["canonical_name"]

            # Embed the concept name
            try:
                resp = await asyncio.wait_for(
                    ai.embeddings.create(
                        model=EMBED_MODEL,
                        input=concept_name,
                        dimensions=EMBED_DIMS,
                    ),
                    timeout=30,
                )
                embedding = resp.data[0].embedding
            except Exception as exc:
                log.warning("embed_failed | concept=%s | %s", concept_name, exc)
                continue

            # Find similar chunks via pgvector
            r = await http.post(
                f"{SUPABASE_URL}/rest/v1/rpc/match_documents",
                headers=HEADERS,
                json={
                    "query_embedding": embedding,
                    "match_count":     top_k,
                    "filter_course":   None,
                    "filter_type":     None,
                },
            )
            if r.status_code >= 400:
                log.warning("match_failed | concept=%s | %s", concept_name, r.text[:100])
                continue

            matches = [
                row for row in r.json()
                if (row.get("similarity") or 0) >= min_sim
            ]

            if not matches:
                log.debug("NO_MATCHES | concept=%s", concept_name)
                continue

            if dry_run:
                log.info("DRY_RUN | concept=%s | matches=%d | top_sim=%.3f",
                         concept_name, len(matches), matches[0].get("similarity", 0))
                continue

            # Batch-insert chunk_concepts
            rows = [
                {
                    "chunk_id":           row["id"],
                    "concept_id":         concept_id,
                    "relevance_weight":   round(min(float(row.get("similarity", 0)), 1.0), 4),
                    "extraction_method":  "embedding_similarity",
                }
                for row in matches
            ]
            r = await http.post(
                f"{SUPABASE_URL}/rest/v1/chunk_concepts",
                headers={**HEADERS, "Prefer": "resolution=merge-duplicates"},
                json=rows,
            )
            if r.status_code >= 400:
                log.warning("chunk_concepts_insert_failed | concept=%s | %s",
                            concept_name, r.text[:100])
            else:
                total_inserted += len(rows)

            if (i + 1) % 20 == 0:
                log.info("ASSOCIATION_PROGRESS | concepts=%d/%d inserted=%d",
                         i + 1, len(concepts), total_inserted)

            await asyncio.sleep(0.05)  # avoid hammering the DB

    log.info("ASSOCIATION_COMPLETE | total_chunk_concepts=%d", total_inserted)
    return total_inserted


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser(description="RUMMAN concept extraction pipeline")
    parser.add_argument(
        "--phase",
        choices=["discovery", "associate", "both"],
        required=True,
        help="Which phase to run",
    )
    parser.add_argument("--sample",   type=int,   default=2000,  help="Chunks to sample in discovery (default 2000)")
    parser.add_argument("--batch",    type=int,   default=20,    help="Chunks per GPT batch in discovery (default 20)")
    parser.add_argument("--min-sim",  type=float, default=0.40,  help="Min similarity for chunk_concepts (default 0.40)")
    parser.add_argument("--top-k",    type=int,   default=200,   help="Max chunks per concept in association (default 200)")
    parser.add_argument("--dry-run",  action="store_true",       help="Report what would be inserted without writing")
    args = parser.parse_args()

    if args.phase in ("discovery", "both"):
        n = await run_discovery(
            sample_size=args.sample,
            batch_size=args.batch,
            dry_run=args.dry_run,
        )
        log.info("DISCOVERY_DONE | inserted=%d", n)

    if args.phase in ("associate", "both"):
        n = await run_association(
            min_sim=args.min_sim,
            top_k=args.top_k,
            dry_run=args.dry_run,
        )
        log.info("ASSOCIATION_DONE | inserted=%d", n)


if __name__ == "__main__":
    asyncio.run(main())
