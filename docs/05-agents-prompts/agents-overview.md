# Agents and Prompts Overview

How RUMMAN uses AI models — where prompts live, what they do, and how they are governed.

*Last updated: 2026-05-29*

---

## Prompt Inventory

RUMMAN uses LLMs in five distinct contexts. Each prompt has a specific job and governance rule.

| Context | File | Model | Type | When it runs |
|---|---|---|---|---|
| Intent classification | `app/query_understanding.py` | gpt-4o-mini | Structured output | Every student query |
| Answer synthesis | `app/search_api.py` | gpt-4o-mini | Text completion | Every `/synthesize` call |
| Audio transcription | `app/telegram_download_worker.py` | gpt-4o-mini-transcribe | Audio | When audio job is claimed |
| Image/PDF OCR | `app/telegram_download_worker.py` | gpt-4o (Vision) | Vision | When image/image-only PDF job is claimed |
| Intelligence extraction | `app/intelligence_worker.py` | gpt-4o-mini | Structured output | **Disabled** — Phase 2 boundary |

---

## Intent Classification Prompt

**Location:** `app/query_understanding.py` — the `_CLASSIFY_PROMPT` constant.

**Job:** Classify a normalized Arabic student query into a structured intent object with fields: `primary_intent`, `detected_course_code`, `search_modifiers`, `language`.

**Constraints:** The prompt is instructed to return only valid enum values. Structured output ensures parseable output even on edge cases.

**Cost:** ~$0.0001 per query at gpt-4o-mini pricing.

---

## Synthesis Prompt

**Location:** `app/search_api.py` — the synthesis system/user prompt constants.

**Job:** Given retrieved document chunks, synthesize a grounded Arabic answer. The prompt enforces:
1. Only use facts present in the provided chunks
2. Never use GPT training knowledge about SEU
3. Always cite which chunk the answer came from
4. If answer is not in chunks, say "لم أجد معلومات كافية"
5. Be concise — prefer one clear sentence over a paragraph

**Anti-hallucination gate:** The prompt explicitly forbids synthesis of facts not in the retrieved corpus. This is the core guarantee that makes RUMMAN trustworthy.

**Cost:** ~$0.0005 per synthesis call at gpt-4o-mini pricing.

**Fallback:** If synthesis times out or fails, the API returns raw chunk text without synthesis (graceful degradation — never blocks student).

---

## Vision OCR Prompt

**Location:** `app/telegram_download_worker.py` — the `_OCR_PROMPT` constant.

**Job:** Extract all text from an image or scanned PDF page. Output only extracted text, no commentary. Handle Arabic and English in the same document. Do not infer or correct content — extract verbatim.

**Cost:** ~$0.004 per image page at gpt-4o pricing with `detail: high`.

---

## Intelligence Extraction Prompt (Disabled)

**Location:** `app/intelligence_worker.py` — disabled pending Phase 2.

**Planned job:** Extract structured entities from Telegram messages: exam dates, deadlines, assignment announcements, important notices.

**Blocking issues before enabling:**
1. No cursor tracking — re-processes same messages on every run
2. No dedup constraint on output table — duplicate extractions accumulate
3. No cost ceiling — can spend $50-200+/month unconstrained

See `RUMMAN-Ops/AUDIT.md` → OI-008.

---

## Prompt Registry

See `docs/05-agents-prompts/prompt-registry.md` for the complete history of prompt versions and changes.

---

## Model Governance

**Default model for RUMMAN work:** Sonnet (Claude). OpenAI models are used only in deployed workers for specific inference functions.

**Do not upgrade synthesis to gpt-4o** without cost analysis — at 10,000 student queries/day, upgrading from gpt-4o-mini would increase OpenAI costs by ~10x.

**Embedding model lock:** `text-embedding-3-large` at 3072 dims. Changing the embedding model requires re-embedding all `document_chunks`. This is a destructive migration — requires deprecating the current index and rebuilding from scratch.

---

## Quality Monitoring

`query_logs` captures `detected_intent`, `synthesis_tokens`, `response_time_ms`, `result_count`. Watch `result_count = 0` as a knowledge gap signal. `feedback` table provides direct student quality signal.
