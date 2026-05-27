# Prompt Registry

This file tracks all extraction prompts used by RUMMAN workers. Treat prompts as versioned artifacts — changing a prompt is a schema change for the AI outputs it produces.

When you change a prompt: increment the version, add an entry here, and update PROMPT_VERSION in the worker.

---

## daily_brief_v1 — Operational Intelligence Extraction

**Worker:** `app/daily_brief.py`
**Version:** v1
**Model:** gpt-4o-mini
**Introduced:** 2026-05-27

### Purpose

Extract operational items (tasks, deadlines, decisions, risks, follow-ups) from Arabic/English university Telegram group messages. Designed for conservatism — empty output is correct when nothing operational is present.

### Input

Indexed message list per chat with sender name, date, and text. Window is configurable (default 24h).

### Output schema

```json
{
  "context_summary": "one sentence",
  "items": [
    {
      "type": "task|deadline|decision|risk|follow_up",
      "content": "description",
      "confidence": 0.85,
      "source_indices": [0, 2],
      "due_date": "2026-05-30",
      "course_code": "IT484"
    }
  ]
}
```

### Quality notes

- Arabic colloquial university chat is the primary target domain
- Course codes (IT484, CS001, STAT201) are strong signals — prompt explicitly calls these out
- Archive channel messages are often forwards without sender name — handled via "—" placeholder
- Threshold: items below 0.65 confidence are dropped at storage time (not at extraction time — both stored in raw_output for analysis)

### Known limitations (v1)

- No deduplication across runs — same deadline may appear across multiple days' runs
- No temporal reasoning across multiple messages — each chat window is processed independently
- No follow-up tracking — a follow_up extracted on day 1 has no link to its resolution on day 5

### Iteration notes

To improve extraction quality: run with `--dry-run` against real data, compare raw_output to expected results, adjust prompt, increment version before deploying.

---

## Prompt Versioning Rules

1. A prompt version is immutable once deployed. Change → new version.
2. `prompt_version` is stored on every `brief_runs` row — this links every extracted item back to the exact prompt that produced it.
3. Comparing extraction quality across prompt versions: query `extracted_items JOIN brief_runs ON brief_run_id WHERE brief_runs.prompt_version = 'v1'` vs `'v2'`.
4. Never change the prompt text without updating PROMPT_VERSION in the worker source.
