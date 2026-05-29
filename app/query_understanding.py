"""
query_understanding.py — Query normalization, intent classification, and search routing.

Pipeline (each step falls back gracefully if it fails):

  1. Static normalization  — applies normalization_dict.json phrase/word substitutions.
                             Free, deterministic, zero latency.

  2. Intent hints          — checks intent_hints.json for keyword signals.
                             Passed as context to the classifier (gives it a head start).

  3. Intent classification — calls gpt-4o-mini with a strict parser prompt.
                             Returns structured IntentResult or None on any failure.

  4. Search param builder  — translates IntentResult into targeted search parameters.
                             Single search when confident; multi-search when ambiguous.

Design constraints:
  - OpenAI classifies intent, never answers the question.
  - Corpus remains the sole source of truth for SEU facts.
  - Every fallback path still executes a valid search.
  - Logging is fire-and-forget; logging failure never blocks a response.
"""

import os
import json
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

from openai import AsyncOpenAI

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

INTENT_TYPES = frozenset({
    "exam_topics",    # what is covered in an exam
    "exam_schedule",  # when is an exam
    "resource",       # summaries, notes, slides, past papers
    "deadline",       # when is something due
    "course_info",    # course description, plan, requirements
    "clarify",        # too ambiguous to classify — ask student
    "unknown",        # cannot determine intent
})


@dataclass
class IntentResult:
    normalized_text: str
    english_query: Optional[str]      # English translation for cross-language corpus search
    intent_type: str
    course_codes: list[str]
    exam_type: Optional[str]          # "midterm" | "final" | "quiz" | None
    source_type_filter: Optional[str] # "exam" | "study_plan" | "upload" | None
    confidence: float
    clarification_needed: bool
    clarification_question: Optional[str]


@dataclass
class QueryUnderstanding:
    query_raw: str
    query_normalized: str             # after static normalization
    hints_triggered: list[dict]       # which intent_hints patterns fired
    intent: Optional[IntentResult]    # None if classifier failed or was skipped
    classifier_used: bool = False


@dataclass
class SearchParams:
    query: str
    course_code: Optional[str]
    source_type: Optional[str]
    limit: int


# ---------------------------------------------------------------------------
# Dictionary loading (module-level, loaded once at startup)
# ---------------------------------------------------------------------------

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

_NORM_WORDS:   dict[str, str] = {}
_NORM_PHRASES: list[tuple[str, str]] = []  # sorted longest-first
_INTENT_HINTS: list[dict] = []


def load_dicts() -> None:
    """Load normalization_dict.json and intent_hints.json. Call once at startup."""
    global _NORM_WORDS, _NORM_PHRASES, _INTENT_HINTS

    norm_path  = os.path.join(_DATA_DIR, "normalization_dict.json")
    hints_path = os.path.join(_DATA_DIR, "intent_hints.json")

    try:
        with open(norm_path, encoding="utf-8") as f:
            raw = json.load(f)
        words = {k: v for k, v in raw.get("words", {}).items()
                 if not k.startswith("_comment")}
        phrases_raw = raw.get("phrases", {})
        _NORM_WORDS   = words
        _NORM_PHRASES = sorted(phrases_raw.items(), key=lambda x: len(x[0]), reverse=True)
        log.info("normalization_dict loaded: %d words, %d phrases",
                 len(_NORM_WORDS), len(_NORM_PHRASES))
    except Exception as exc:
        log.warning("Could not load normalization_dict.json: %s", exc)

    try:
        with open(hints_path, encoding="utf-8") as f:
            raw = json.load(f)
        _INTENT_HINTS = [p for p in raw.get("patterns", []) if not p.get("id", "").startswith("_")]
        log.info("intent_hints loaded: %d patterns", len(_INTENT_HINTS))
    except Exception as exc:
        log.warning("Could not load intent_hints.json: %s", exc)


# ---------------------------------------------------------------------------
# Step 1 — Static normalization
# ---------------------------------------------------------------------------

def normalize_static(query: str) -> str:
    """
    Apply phrase substitutions (longest first) then per-word substitutions.
    Strips empty tokens produced by filler-word removal (e.g. "والله" → "").
    Returns original query unchanged if dicts are empty (safe fallback).
    """
    if not _NORM_PHRASES and not _NORM_WORDS:
        return query

    result = query

    # Phrases first (longest match wins, word-boundary padded to prevent partial overlap)
    padded = " " + result + " "
    for surface, canonical in _NORM_PHRASES:
        padded = padded.replace(" " + surface + " ", " " + canonical + " ")
    result = padded.strip()

    # Per-word substitutions
    tokens = result.split()
    tokens = [_NORM_WORDS.get(t, t) for t in tokens]
    tokens = [t for t in tokens if t]  # remove blanks from filler removal

    return " ".join(tokens)


# ---------------------------------------------------------------------------
# Step 2 — Intent hints
# ---------------------------------------------------------------------------

def apply_hints(query: str) -> list[dict]:
    """
    Return any hint patterns whose triggers appear in the query.
    Called with the post-normalization query so triggers match canonical forms.
    """
    matched = []
    for pattern in _INTENT_HINTS:
        for trigger in pattern.get("triggers", []):
            if trigger in query:
                matched.append(pattern)
                break
    return matched


# ---------------------------------------------------------------------------
# Step 3 — Intent classification (gpt-4o-mini, structured output)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a query parser for a Saudi university assistant.

Your ONLY job is to extract structured intent from the student's query.
- Do NOT answer the question.
- Do NOT use any knowledge about Saudi universities, courses, or professors.
- Do NOT invent course codes, exam content, or professor names.
- If course codes appear in the query, extract them exactly as written.

Return ONLY valid JSON matching this schema (no extra keys, no explanation):
{
  "normalized_text": "Full MSA Arabic rewrite of the query. Remove dialect. Keep all meaningful content.",
  "english_query": "Concise English translation of the query intent. Used to search English-language course content.",
  "intent_type": "<one of: exam_topics | exam_schedule | resource | deadline | course_info | clarify | unknown>",
  "course_codes": ["array of course codes found in query, uppercase, e.g. CS241"],
  "exam_type": "<midterm | final | quiz | null>",
  "source_type_filter": "<exam | study_plan | upload | null>",
  "confidence": <float 0.0–1.0>,
  "clarification_needed": <true | false>,
  "clarification_question": "<Gulf Arabic question to ask student for clarification, or null>"
}

Intent type guide:
  exam_topics    — student wants to know what topics/content appear in an exam
  exam_schedule  — student wants to know when an exam occurs
  resource       — student wants study materials: summaries, notes, slides, past papers
  deadline       — student wants to know when an assignment or project is due
  course_info    — student wants course description, study plan, or requirements
  clarify        — query is too ambiguous to classify with confidence ≥ 0.50
  unknown        — cannot determine intent even with clarification\
"""


async def classify_intent(
    query: str,
    hints: list[dict],
    ai: AsyncOpenAI,
    timeout: float = 6.0,
) -> Optional[IntentResult]:
    """
    Call gpt-4o-mini to classify query intent.
    Returns None on any failure (timeout, parse error, API error).
    Caller must handle None by falling back to plain vector search.
    """
    hint_lines = []
    for h in hints:
        if h.get("bias_intent"):
            hint_lines.append(f"Keyword signal → intent: {h['bias_intent']}")
        if h.get("bias_source_type"):
            hint_lines.append(f"Keyword signal → source type: {h['bias_source_type']}")

    user_content = query
    if hint_lines:
        user_content = (
            "Static keyword signals detected (use as hints, do not override if query contradicts):\n"
            + "\n".join(hint_lines)
            + "\n\nStudent query: " + query
        )
    else:
        user_content = "Student query: " + query

    try:
        resp = await asyncio.wait_for(
            ai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": user_content},
                ],
                temperature=0,
                max_tokens=400,
                response_format={"type": "json_object"},
            ),
            timeout=timeout,
        )

        raw: dict = json.loads(resp.choices[0].message.content)

        intent_type = raw.get("intent_type", "unknown")
        if intent_type not in INTENT_TYPES:
            intent_type = "unknown"

        codes = [
            c.upper().replace(" ", "")
            for c in raw.get("course_codes", [])
            if isinstance(c, str) and c.strip()
        ]

        english_query = (raw.get("english_query") or "").strip() or None

        return IntentResult(
            normalized_text=raw.get("normalized_text", query) or query,
            english_query=english_query,
            intent_type=intent_type,
            course_codes=codes,
            exam_type=raw.get("exam_type"),
            source_type_filter=raw.get("source_type_filter"),
            confidence=float(raw.get("confidence", 0.5)),
            clarification_needed=bool(raw.get("clarification_needed", False)),
            clarification_question=raw.get("clarification_question"),
        )

    except asyncio.TimeoutError:
        log.warning("intent classifier timed out for query: %.60s", query)
        return None
    except Exception as exc:
        log.warning("intent classifier failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Step 4 — Search parameter builder
# ---------------------------------------------------------------------------

def build_search_params(
    understanding: QueryUnderstanding,
    limit: int,
) -> list[SearchParams]:
    """
    Translate QueryUnderstanding into one or more SearchParams.

    Course-specific queries: search only within that course (lower threshold
    applied downstream — see MIN_SIMILARITY_COURSE in search_api.py). Avoids
    returning content from other courses that happens to be more similar.

    General queries (no course code): broad search, standard threshold.

    Always returns at least one SearchParams (never empty list).
    """
    intent = understanding.intent
    normalized = understanding.query_normalized

    # No classifier result → plain broad search
    if intent is None:
        return [SearchParams(query=normalized, course_code=None, source_type=None, limit=limit)]

    course_code  = intent.course_codes[0] if intent.course_codes else None
    source_type  = intent.source_type_filter
    intent_query = intent.normalized_text or normalized

    # Course-specific query → search within course only (no broad fallback to
    # prevent other courses' content bleeding in at higher similarity).
    # Run both Arabic and English queries — corpus may be in either language.
    if course_code:
        searches = [SearchParams(
            query=intent_query,
            course_code=course_code,
            source_type=source_type,
            limit=limit,
        )]
        if intent.english_query and intent.english_query != intent_query:
            searches.append(SearchParams(
                query=intent.english_query,
                course_code=course_code,
                source_type=source_type,
                limit=limit,
            ))
        return searches

    # No course code → broad search
    searches: list[SearchParams] = [
        SearchParams(query=intent_query, course_code=None, source_type=source_type, limit=limit)
    ]

    # Also search with English translation — Arabic terminology queries (e.g. عقد المرابحة)
    # embed at lower similarity in question form than in English; both runs are cheap.
    if intent.english_query and intent.english_query != intent_query:
        searches.append(SearchParams(
            query=intent.english_query, course_code=None, source_type=source_type, limit=limit
        ))

    # Low confidence: also try original normalized text if different from intent rewrite
    if intent.confidence < 0.65 and intent_query != normalized:
        searches.append(SearchParams(query=normalized, course_code=None, source_type=None, limit=limit))

    return searches


# ---------------------------------------------------------------------------
# Main pipeline entry point
# ---------------------------------------------------------------------------

async def understand_query(
    query: str,
    ai: Optional[AsyncOpenAI] = None,
    run_classifier: bool = True,
) -> QueryUnderstanding:
    """
    Full pipeline: normalize → hints → classify → return understanding.
    Safe to call with ai=None or run_classifier=False (returns normalization only).
    """
    normalized = normalize_static(query)
    hints      = apply_hints(normalized)

    intent          = None
    classifier_used = False

    if run_classifier and ai is not None:
        intent          = await classify_intent(normalized, hints, ai)
        classifier_used = True

    return QueryUnderstanding(
        query_raw=query,
        query_normalized=normalized,
        hints_triggered=hints,
        intent=intent,
        classifier_used=classifier_used,
    )
