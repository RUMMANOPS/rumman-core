#!/usr/bin/env python3
"""
gap_analyst.py — Knowledge gap detection from learning_events.

Reads all zero_result events from learning_events, clusters them by topic,
classifies each cluster as content_gap / retrieval_gap / coverage_gap,
and writes a structured report to analysis_runs + gap_items.

Usage:
    python3 scripts/gap_analyst.py                    # full run, all time
    python3 scripts/gap_analyst.py --days 30          # last 30 days only
    python3 scripts/gap_analyst.py --dry-run          # stdout only, no DB writes
    python3 scripts/gap_analyst.py --init-schema      # apply migration 026, then exit

Gap types:
    content_gap   — corpus has NO document covering this topic (sim < 0.20)
    retrieval_gap — content likely exists but similarity is low (0.20–0.40)
    coverage_gap  — partial coverage (sim > 0.40 but still zero result)

Requires: supabase/migrations/026_analysis_runs.sql applied first.
Not in Procfile — run on demand.
"""

import os
import re
import json
import asyncio
import argparse
import hashlib
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import Optional

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

SUPABASE_URL   = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

SEU_TENANT_ID = os.environ.get("SEU_TENANT_ID", "00000000-0000-0000-0000-000000000001")

ANALYST_TYPE   = "gap_analyst"
WORKER         = "gap_analyst_v1"
MODEL          = os.environ.get("GAP_ANALYST_MODEL", "gpt-4o-mini")

# Similarity bands
CONTENT_GAP_THRESHOLD   = 0.20   # nothing even close
RETRIEVAL_GAP_THRESHOLD = 0.40   # something nearby but not good enough
MIN_OCCURRENCES         = 1      # include all gaps regardless of frequency

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}


def log(event: str, **kwargs):
    parts = [event] + [f"{k}={v}" for k, v in kwargs.items()]
    print(" | ".join(parts), flush=True)


# ── Schema init ───────────────────────────────────────────────────────────────

MIGRATION_PATH = os.path.join(
    os.path.dirname(__file__),
    "..", "supabase", "migrations", "026_analysis_runs.sql"
)


async def init_schema(http: httpx.AsyncClient) -> bool:
    """Return True if analysis_runs table exists."""
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/analysis_runs",
        headers=HEADERS,
        params=[("limit", "0")],
    )
    if r.status_code == 200:
        log("SCHEMA_OK", table="analysis_runs")
        return True
    log("SCHEMA_MISSING", table="analysis_runs",
        hint="Apply supabase/migrations/026_analysis_runs.sql in Supabase Dashboard → SQL Editor")
    return False


# ── Data fetching ─────────────────────────────────────────────────────────────

async def fetch_zero_result_events(
    http: httpx.AsyncClient,
    since: Optional[datetime],
) -> list[dict]:
    params = [
        ("event_type", "eq.zero_result"),
        ("order", "occurred_at.desc"),
        ("limit", "500"),
    ]
    if since:
        params.append(("occurred_at", f"gte.{since.isoformat()}"))

    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/learning_events",
        headers=HEADERS,
        params=params,
    )
    if r.status_code >= 400:
        log("FETCH_ERROR", table="learning_events", status=r.status_code, error=r.text[:120])
        return []
    return r.json()


async def fetch_synthesis_events(
    http: httpx.AsyncClient,
    since: Optional[datetime],
) -> list[dict]:
    """Synthesis events where top_similarity was very low — near-miss cases."""
    params = [
        ("event_type", "eq.synthesis"),
        ("top_similarity", "lt.0.35"),
        ("order", "occurred_at.desc"),
        ("limit", "200"),
    ]
    if since:
        params.append(("occurred_at", f"gte.{since.isoformat()}"))

    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/learning_events",
        headers=HEADERS,
        params=params,
    )
    if r.status_code >= 400:
        return []
    return r.json()


# ── Clustering ────────────────────────────────────────────────────────────────

def _normalize_query(q: str) -> str:
    """Lowercase, strip punctuation and extra whitespace for dedup."""
    q = q.strip().lower()
    q = re.sub(r'[^\w\s؀-ۿ]', ' ', q)
    return re.sub(r'\s+', ' ', q).strip()


def _extract_course_codes(text: str) -> list[str]:
    return list(set(re.findall(r'\b([A-Z]{2,6}\d{3})\b', text.upper())))


def _query_fingerprint(q: str) -> str:
    """Stable key for deduplication across paraphrases."""
    return hashlib.md5(_normalize_query(q).encode()).hexdigest()[:8]


def _cluster_by_course_and_topic(events: list[dict]) -> dict[str, dict]:
    """
    Simple rule-based clustering:
      1. Events with a course_code → grouped by code
      2. Events without course_code → grouped by first 3 Arabic/English words
         (captures "وش هو عقد" → same bucket regardless of ending)

    Returns dict[cluster_key → cluster_info]
    """
    clusters: dict[str, dict] = {}

    for ev in events:
        query = (ev.get("query_raw") or "").strip()
        if not query:
            continue

        # Skip classifier artifacts that should never reach search:
        # greetings, identity questions, meta queries without academic content.
        intent = (ev.get("intent_type") or "").lower()
        if intent in ("greeting", "ack", "identity_bot", "identity_user", "off_topic"):
            continue
        # Also skip if the raw query looks like a pure greeting/ack (classifier may have mislabeled)
        if len(query) < 15 and not re.search(r'[A-Z]{2,6}\d{3}', query.upper()):
            words = _normalize_query(query).split()
            if not any(w in {"exam", "quiz", "اختبار", "امتحان", "ملخص", "شرح", "مادة"} for w in words):
                continue

        codes = ev.get("course_codes") or _extract_course_codes(query)
        sim   = float(ev.get("top_similarity") or 0.0)

        if codes:
            key = f"course:{codes[0].upper()}"
            course = codes[0].upper()
        else:
            words = _normalize_query(query).split()[:4]
            key = "topic:" + "_".join(words)
            course = None

        if key not in clusters:
            clusters[key] = {
                "key":         key,
                "course_code": course,
                "queries":     [],
                "sims":        [],
                "count":       0,
            }

        clusters[key]["queries"].append(query)
        clusters[key]["sims"].append(sim)
        clusters[key]["count"] += 1

    return clusters


# ── Gap classification ────────────────────────────────────────────────────────

def _classify_gap(cluster: dict) -> tuple[str, str]:
    """
    Returns (gap_type, severity).
    Uses average similarity of the cluster's events.
    """
    sims = [s for s in cluster["sims"] if s > 0]
    avg_sim = sum(sims) / len(sims) if sims else 0.0
    count   = cluster["count"]

    if avg_sim < CONTENT_GAP_THRESHOLD:
        gap_type = "content_gap"
    elif avg_sim < RETRIEVAL_GAP_THRESHOLD:
        gap_type = "retrieval_gap"
    else:
        gap_type = "coverage_gap"

    # Severity: frequency × severity of gap type
    base = {"content_gap": 3, "retrieval_gap": 2, "coverage_gap": 1}[gap_type]
    score = base * count
    if score >= 6:
        severity = "high"
    elif score >= 3:
        severity = "medium"
    else:
        severity = "low"

    return gap_type, severity


def _make_cluster_label(cluster: dict) -> str:
    """Human-readable label. For course gaps: course code. For topics: first unique query."""
    if cluster["course_code"]:
        return cluster["course_code"]
    # Pick shortest query as the representative label
    queries = sorted(set(cluster["queries"]), key=len)
    return queries[0][:80] if queries else cluster["key"]


# ── LLM labelling (optional enrichment) ──────────────────────────────────────

LABEL_SYSTEM = """\
You are a knowledge gap analyst for a Saudi university student assistant (RUMMAN).
Given a list of student queries that returned zero results, provide:
1. A concise Arabic/English topic label (≤8 words) that describes what students were looking for.
2. A one-sentence explanation of why the corpus likely lacks this content.

Return JSON: {"label": "...", "explanation": "..."}
No markdown, no extra text.\
"""


async def enrich_cluster_label(ai: AsyncOpenAI, cluster: dict) -> dict:
    """Use LLM to generate a more descriptive label for non-course clusters."""
    if cluster["course_code"]:
        return cluster  # course-code clusters are already well-labeled

    sample = cluster["queries"][:5]
    prompt = f"Student queries (all returned zero results):\n" + "\n".join(f"- {q}" for q in sample)

    try:
        resp = await asyncio.wait_for(
            ai.chat.completions.create(
                model=MODEL,
                temperature=0.2,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": LABEL_SYSTEM},
                    {"role": "user",   "content": prompt},
                ],
                max_tokens=150,
            ),
            timeout=30,
        )
        parsed = json.loads(resp.choices[0].message.content)
        cluster["label"]       = parsed.get("label", cluster["label"])
        cluster["explanation"] = parsed.get("explanation", "")
        tokens = resp.usage.total_tokens if resp.usage else 0
        cost   = tokens * 0.000_000_150  # gpt-4o-mini input ~150/1M
        cluster["label_cost"] = cost
    except Exception as e:
        log("LABEL_ERROR", key=cluster["key"], error=str(e)[:80])
        cluster["explanation"] = ""
        cluster["label_cost"]  = 0.0

    return cluster


# ── DB writes ─────────────────────────────────────────────────────────────────

async def create_analysis_run(http: httpx.AsyncClient, payload: dict) -> Optional[str]:
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/analysis_runs",
        headers=HEADERS,
        json=payload,
    )
    if r.status_code >= 400:
        log("RUN_CREATE_ERROR", status=r.status_code, error=r.text[:200])
        return None
    rows = r.json()
    return rows[0]["id"] if rows else None


async def update_analysis_run(http: httpx.AsyncClient, run_id: str, update: dict):
    r = await http.patch(
        f"{SUPABASE_URL}/rest/v1/analysis_runs?id=eq.{run_id}",
        headers=HEADERS,
        json=update,
    )
    if r.status_code >= 400:
        log("RUN_UPDATE_ERROR", run_id=run_id, status=r.status_code)


async def store_gap_items(http: httpx.AsyncClient, items: list[dict]) -> int:
    if not items:
        return 0
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/gap_items",
        headers=HEADERS,
        json=items,
    )
    if r.status_code >= 400:
        log("GAP_ITEMS_ERROR", status=r.status_code, error=r.text[:200])
        return 0
    return len(r.json())


# ── Report formatting ─────────────────────────────────────────────────────────

SEP = "═" * 60

def format_report(clusters: list[dict], window_desc: str) -> str:
    lines = [
        "",
        SEP,
        "  RUMMAN GAP ANALYSIS REPORT",
        f"  Window: {window_desc}",
        SEP,
        "",
    ]

    by_type: dict[str, list] = defaultdict(list)
    for c in clusters:
        by_type[c["gap_type"]].append(c)

    labels = {
        "content_gap":   "CONTENT GAPS — Nothing in corpus",
        "retrieval_gap": "RETRIEVAL GAPS — Weak embedding coverage",
        "coverage_gap":  "COVERAGE GAPS — Partial content only",
    }

    for gap_type, section_label in labels.items():
        section = sorted(by_type.get(gap_type, []),
                         key=lambda c: (-{"high":3,"medium":2,"low":1}[c["severity"]], -c["count"]))
        if not section:
            continue

        lines.append(f"  ▸ {section_label}")
        lines.append("")
        for c in section:
            severity_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(c["severity"], "•")
            label = c.get("label") or c["cluster_label"]
            lines.append(f"    {severity_icon} [{c['severity'].upper()}] {label}")
            if c.get("explanation"):
                lines.append(f"       {c['explanation']}")
            lines.append(f"       Occurrences: {c['count']}  |  Avg similarity: {c['avg_sim']:.3f}")
            lines.append(f"       Example queries:")
            for q in list(set(c["queries"]))[:3]:
                lines.append(f"         • {q}")
            lines.append("")

    if not any(by_type.values()):
        lines.append("  No gaps found in this window.")
        lines.append("")

    lines.append(SEP)
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="RUMMAN Gap Analyst")
    parser.add_argument("--days",        type=int,  default=None,
                        help="Lookback window in days (default: all time)")
    parser.add_argument("--dry-run",     action="store_true",
                        help="No DB writes — print report to stdout only")
    parser.add_argument("--init-schema", action="store_true",
                        help="Check if migration 026 is applied, then exit")
    parser.add_argument("--no-enrich",   action="store_true",
                        help="Skip LLM label enrichment (faster, cheaper)")
    args = parser.parse_args()

    window_end   = datetime.now(timezone.utc)
    window_start = (window_end - timedelta(days=args.days)) if args.days else None
    window_desc  = f"last {args.days} days" if args.days else "all time"

    log("GAP_ANALYST_START", window=window_desc, dry_run=args.dry_run)

    ai = AsyncOpenAI(api_key=OPENAI_API_KEY)

    async with httpx.AsyncClient(timeout=60) as http:

        if args.init_schema:
            ok = await init_schema(http)
            return

        if not args.dry_run:
            ok = await init_schema(http)
            if not ok:
                log("ABORT", reason="Apply migration 026 first (Supabase Dashboard → SQL Editor)")
                return

        # ── Fetch events ──────────────────────────────────────────────────────
        zero_events = await fetch_zero_result_events(http, window_start)
        weak_events = await fetch_synthesis_events(http, window_start)

        log("EVENTS_FETCHED",
            zero_result=len(zero_events),
            weak_synthesis=len(weak_events))

        all_events = zero_events + weak_events

        if not all_events:
            log("NO_EVENTS", hint="No zero-result or weak-synthesis events in this window")
            return

        # ── Cluster ───────────────────────────────────────────────────────────
        raw_clusters = _cluster_by_course_and_topic(all_events)
        log("CLUSTERS_FOUND", count=len(raw_clusters))

        # Build full cluster objects
        total_label_cost = 0.0
        clusters = []

        for key, c in raw_clusters.items():
            if c["count"] < MIN_OCCURRENCES:
                continue

            gap_type, severity = _classify_gap(c)
            sims = [s for s in c["sims"] if s > 0]
            avg_sim = sum(sims) / len(sims) if sims else 0.0
            top_sim = max(sims) if sims else 0.0

            c["gap_type"]      = gap_type
            c["severity"]      = severity
            c["avg_sim"]       = avg_sim
            c["top_sim"]       = top_sim
            c["cluster_label"] = _make_cluster_label(c)
            c["label"]         = c["cluster_label"]

            clusters.append(c)

        # ── Enrich labels via LLM ─────────────────────────────────────────────
        if not args.no_enrich:
            topic_clusters = [c for c in clusters if not c["course_code"]]
            log("ENRICHING_LABELS", count=len(topic_clusters))
            for c in topic_clusters:
                await enrich_cluster_label(ai, c)
                total_label_cost += c.get("label_cost", 0.0)

        log("ANALYSIS_DONE",
            gaps=len(clusters),
            content_gaps=sum(1 for c in clusters if c["gap_type"] == "content_gap"),
            retrieval_gaps=sum(1 for c in clusters if c["gap_type"] == "retrieval_gap"),
            coverage_gaps=sum(1 for c in clusters if c["gap_type"] == "coverage_gap"),
            label_cost_usd=f"${total_label_cost:.4f}")

        # ── Print report ──────────────────────────────────────────────────────
        print(format_report(clusters, window_desc))

        if args.dry_run:
            return

        # ── Persist to DB ─────────────────────────────────────────────────────
        run_output = {
            "gap_count":       len(clusters),
            "event_count":     len(all_events),
            "clusters":        [
                {
                    "key":          c["key"],
                    "gap_type":     c["gap_type"],
                    "severity":     c["severity"],
                    "label":        c.get("label"),
                    "course_code":  c.get("course_code"),
                    "count":        c["count"],
                    "avg_sim":      c["avg_sim"],
                    "top_sim":      c["top_sim"],
                    "explanation":  c.get("explanation", ""),
                }
                for c in clusters
            ],
        }

        run_id = await create_analysis_run(http, {
            "tenant_id":    SEU_TENANT_ID,
            "analyst_type": ANALYST_TYPE,
            "ran_at":       window_end.isoformat(),
            "window_start": window_start.isoformat() if window_start else None,
            "window_end":   window_end.isoformat(),
            "event_count":  len(all_events),
            "output":       run_output,
            "cost_usd":     total_label_cost,
            "model":        MODEL,
            "worker":       WORKER,
        })

        if not run_id:
            log("PERSIST_FAILED", reason="Could not create analysis_run row")
            return

        log("RUN_CREATED", run_id=run_id)

        # ── Store gap_items ───────────────────────────────────────────────────
        gap_rows = []
        for c in clusters:
            unique_queries = list(dict.fromkeys(c["queries"]))  # preserve order, deduplicate
            gap_rows.append({
                "tenant_id":       SEU_TENANT_ID,
                "analysis_run_id": run_id,
                "gap_type":        c["gap_type"],
                "cluster_label":   c.get("label") or c["cluster_label"],
                "course_code":     c.get("course_code"),
                "example_queries": unique_queries[:10],
                "occurrence_count": c["count"],
                "severity":        c["severity"],
                "top_similarity":  c["top_sim"],
            })

        stored = await store_gap_items(http, gap_rows)
        log("GAP_ITEMS_STORED", count=stored, run_id=run_id)

        await update_analysis_run(http, run_id, {"notes": f"stored {stored} gap_items"})
        log("GAP_ANALYST_DONE", run_id=run_id, gaps=stored)


if __name__ == "__main__":
    asyncio.run(main())
