# ADR-0010: Anti-Hallucination as Architecture

## Status

Accepted

## Context

RUMMAN synthesizes answers for students preparing for exams. A student who receives a wrong answer about which topics appear on an exam may study the wrong material and fail. The cost of a wrong answer is not inconvenience — it is a failed course.

Early synthesis implementations used a general "answer from context" prompt. In practice, the model would blend retrieved corpus content with its own training knowledge about Saudi universities, producing answers that were:
- Partially from the corpus (correct)
- Partially from the model's training data about Saudi universities, SEU, or specific courses (plausible but unverifiable)
- Presented with equal confidence, indistinguishable by the student

This is unacceptable. A student cannot distinguish "RUMMAN found this in the corpus" from "RUMMAN generated this from training data." If the model's training data is wrong, outdated, or from a different university context, the student has no way to know.

The fix must be architectural, not a prompt suggestion. Prompt suggestions ("try to use only the context") are easily overridden by the model's strong priors. Architecture-level constraints are not.

## Decision

Anti-hallucination is enforced at three architectural levels:

### Level 1 — Synthesis Prompt (Hard Constraint)

The synthesis system prompt contains an explicit, non-negotiable instruction:

```
أجب فقط من المحتوى أدناه.
لا تستخدم أي معلومات من تدريبك عن جامعة الملك سعود، جامعة الملك فهد، 
الجامعة السعودية الإلكترونية، أو أي جامعة سعودية أخرى.
إذا لم تجد الإجابة في المحتوى، قل: "لا أجد معلومة عن هذا في المحتوى المتاح"
```

The prompt does not say "prefer context" or "mainly use context." It says: only use what is below. If the answer is not there, say so.

**The explicit "I don't know" response is a success state, not a failure.** A student who hears "I don't find this in available content" can go look elsewhere. A student who hears a confident but wrong answer will stop looking.

### Level 2 — Source Citation (Verifiability)

Every synthesis response includes source attribution:
- `[رسمي]` — from official SEU documents
- `[مجتمع]` — from community (student-generated) content

This allows a student to assess source quality. An answer from official documents carries different weight than an answer from a student's Telegram message.

### Level 3 — Zero-Result Path (Graceful Degradation)

When vector search returns no chunks above the similarity threshold, the system:
1. Does NOT attempt synthesis
2. Returns: "لا أجد معلومات كافية عن هذا الموضوع"
3. Logs a `zero_result` learning event

Synthesis is never attempted on empty context. This eliminates the hallucination pathway entirely for zero-result queries.

### Level 4 — Corpus-as-Product (Architectural)

The anti-hallucination guarantee is ultimately backed by the corpus, not the prompt. A system where the corpus is the product creates structural incentive to make the corpus accurate and comprehensive rather than relying on the model to fill gaps. See `docs/08-product-strategy/product-doctrine.md` for why corpus quality, not model capability, is the primary product investment.

## Consequences

### Positive

- Students can trust that answers citing sources are grounded
- Wrong answers traceable to specific corpus chunks (auditable)
- Model improvements do not introduce new hallucination vectors
- Zero-result rate is a meaningful quality metric (measures corpus gap, not model confusion)
- Anti-hallucination is enforced across model upgrades — switching from gpt-4o-mini to gpt-4o does not weaken the guarantee

### Negative

- Higher zero-result rate than a system that allows generation
- Some queries produce "I don't know" when a model with training knowledge could give a correct answer
- Students must understand that the system's silence is intentional, not broken

## Operational Rules

1. The synthesis prompt must never be modified to allow training knowledge use. Any PR touching the synthesis prompt must explicitly verify this constraint is maintained.

2. Zero-result rate is tracked in `learning_events`. An increase in zero-result rate is a corpus coverage signal, not a model failure — it means students are asking about topics not yet in the corpus.

3. When a new synthesis model is introduced, the anti-hallucination instructions must be re-verified with test queries against known corpus gaps (queries where the correct answer is NOT in the corpus — the model must say "I don't know," not generate from training).

## Relationship to Claim Model

This ADR governs synthesis outputs. The claim model in `docs/01-architecture/claim-model.md` governs structured AI extractions (intelligence items, attributions). Both share the same principle: AI outputs are hypotheses until grounded in evidence. The synthesis constraint is the runtime application of that principle.
