-- Migration 042: Add attribution tracking columns to exam_questions
--
-- WHY: The exam attribution fix process (June 2026) revealed that
-- course_code on 1,262+ questions was set by filename, guesswork, or
-- not set at all. When we fix these, we need to know HOW the fix was
-- determined so any future audit can reproduce or challenge the decision.
--
-- These columns are append-only audit fields. They do NOT replace
-- extraction_confidence (GPT's confidence in the extraction quality).
-- They record the ATTRIBUTION DECISION specifically.
--
-- attribution_method values (enforced by application, not CHECK constraint
-- to allow future extension without a migration):
--   'source_document_course_code'  — doc.course_code was already correct
--   'filename_regex'               — extracted from source_document.file_name
--   'question_text_regex'          — found in question_text body
--   'topic_tag_regex'              — found in a topic_tag string
--   'string_normalisation'         — corrected known misspelling (e.g. ISLM→ISLAM)
--   'gpt_content_inference'        — GPT inferred from question content
--   'manual'                       — human-verified override
--   'unresolvable'                 — no signal available; kept as-is or UNKNOWN
--
-- course_attribution_confidence:
--   0.95  source_document already had the code
--   0.90  unambiguous filename (e.g. "IT364CS364")
--   0.88  filename regex match
--   0.98  string normalisation (ISLM→ISLAM)
--   0.80+ GPT high-confidence
--   0.60–0.79  GPT medium-confidence
--   0.00  unresolvable

ALTER TABLE exam_questions
    ADD COLUMN IF NOT EXISTS attribution_method              TEXT,
    ADD COLUMN IF NOT EXISTS course_attribution_confidence   NUMERIC(4,3),
    ADD COLUMN IF NOT EXISTS attribution_note                TEXT;

-- Index for auditing: find all questions fixed by a specific method
CREATE INDEX IF NOT EXISTS eq_attribution_method_idx
    ON exam_questions (attribution_method)
    WHERE attribution_method IS NOT NULL;

COMMENT ON COLUMN exam_questions.attribution_method IS
    'How the course_code was determined: source_document_course_code | '
    'filename_regex | question_text_regex | topic_tag_regex | '
    'string_normalisation | gpt_content_inference | manual | unresolvable';

COMMENT ON COLUMN exam_questions.course_attribution_confidence IS
    'Confidence in the course_code attribution (0.0–1.0). '
    'Distinct from extraction_confidence which measures extraction quality.';

COMMENT ON COLUMN exam_questions.attribution_note IS
    'Human-readable note explaining the attribution decision. '
    'E.g. "ISLM101→ISLAM101 normalisation" or "filename: IT364CS364_mid2025.pdf"';
