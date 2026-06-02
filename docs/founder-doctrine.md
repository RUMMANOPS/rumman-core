# Founder Doctrine Layer — RUMMAN

**Version:** 1.0
**Produced:** 2026-06-02
**Tier:** Maintained (Tier 2) — review and update at each major product stage transition
**Purpose:** Preserve the intellectual foundation of RUMMAN across contributor changes, session losses, and development pauses. This document captures founder-level reasoning, not implementation details.

**Classification of every item:**
- **[A]** — Confirmed Doctrine: a decision made, a principle established, a constraint that must hold
- **[B]** — Working Hypothesis: a bet being made that drives current decisions, but not yet validated
- **[C]** — Open Question: an unresolved question that will require a decision as the product evolves

---

## 1. Product Identity Doctrine

### What RUMMAN Is

**[A]** RUMMAN is an Academic Intelligence Platform. It crystallizes the collective intelligence of a student community and makes it legible through AI. The AI is the lens; the corpus is the product.

**[A]** RUMMAN is not a chatbot. A chatbot is reactive and stateless. RUMMAN is continuous, accumulates state, and grows more valuable with each interaction. The architectural decision to build around background workers, persistent queues, and accumulated signals (not request-response patterns) reflects this distinction. This is the founding reframing documented in ADR-0001.

**[A]** The product statement students will internalize by 2029: "RUMMAN is the collective academic intelligence of SEU students, made accessible." Not "RUMMAN is an AI academic assistant."

**[A]** RUMMAN is not a search engine. It does not return result lists. It synthesizes grounded answers with citations from accumulated knowledge. The synthesis layer exists to compress knowledge, not to generate it.

### What RUMMAN Is Not

**[A]** RUMMAN is not a course management system. It does not manage enrollment, grades, or assignments. It surfaces what the community knows about those things.

**[A]** RUMMAN is not a recommendation engine in Stage 1. It does not suggest content unprompted. The shift toward proactive suggestion happens at Stage 3 (Academic Copilot) only after understanding how an individual student studies.

**[A]** RUMMAN is not a replacement for professors. It surfaces what the community has said; it does not create new knowledge. RUMMAN can tell you what students historically say appears on the IT362 midterm; it cannot tell you what the professor decided to include this semester.

**[B]** RUMMAN may eventually become an operating system for academic life — a system you tell your constraints and it tells you what to do. This framing (Academic Operating System, Stage 4) is aspirational.

### Why AI Is Not the Product

**[A]** AI models are commodity inputs. OpenAI, Anthropic, Google — all substitutable in weeks. If OpenAI disappeared tomorrow, 60–70% of RUMMAN's value would survive through the corpus, the infrastructure, and the organizational intelligence that the corpus represents.

**[A]** The defensible value is not the synthesis. It is the accumulated SEU corpus: 127,000+ chunks, 800+ exam documents, years of community discussion crystallized into structured, searchable knowledge. This corpus cannot be replicated by pointing a generic AI at the same data without the ingestion infrastructure, attribution work, and signal extraction that gives it structure.

**[A]** Charging per query positions RUMMAN as a GPT wrapper and commoditizes it. If the answer to any RUMMAN question is replicable by typing the same question into ChatGPT, the product has failed. The value must be irreplicable.

### Why Intelligence Is the Product

**[A]** Intelligence in this context means: structured, accumulated, grounded knowledge about a specific institution that no other product possesses. The exam topic patterns across three years of student uploads. The confusion clusters that reveal what every cohort struggles with. The attribution of thousands of Telegram messages to specific courses. This is intelligence that cannot be bought or scraped; it must be built.

**[A]** The anti-GPT-wrapper test: could a student get this answer by asking ChatGPT the same question? If yes, RUMMAN has failed to differentiate. If no (because the answer requires the SEU corpus, the exam archives, or the community signal layer), RUMMAN is delivering on its product promise.

---

## 2. Community Intelligence Doctrine

### Why Communities Are Upstream Intelligence Sources

**[A]** RUMMAN does not generate intelligence. It crystallizes intelligence that already exists in communities. The intelligence is created by students through years of exam preparation discussions, file sharing, and mutual teaching. RUMMAN's job is to capture, structure, and surface it.

**[A]** This is not a metaphor. It is an architectural constraint. The upstream network model is:
```
Communities (generate)
     ↓
RUMMAN (crystallize)
     ↓
Students (access)
```
The intelligence flows top-down. Telegram groups are the source; RUMMAN is the crystallization layer; individual students are the beneficiaries. Any product decision that treats communities as competitors rather than sources is architecturally wrong.

**[A]** RUMMAN should strengthen communities, not disintermediate them. Students who share files in Telegram groups are feeding the corpus that makes RUMMAN more valuable. Community contribution must always be free. Any feature that discourages community contribution attacks the foundation.

### Why Telegram Groups Are Not Competitors

**[A]** Telegram groups cannot be replaced because they generate the raw intelligence RUMMAN crystallizes. If all SEU Telegram groups disappeared tomorrow, RUMMAN would have no living intelligence source. The existing corpus would decay in value within 18–24 months as courses, professors, and exam formats change.

**[B]** The Telegram dependency is the most dangerous strategic risk in the platform — more dangerous than OpenAI dependency. If Telegram were shut down, no immediate substitute exists for the organic community intelligence generation. If OpenAI disappeared, the synthesis layer could be replaced in weeks. The asymmetry of these risks means Telegram community health is a product concern, not just an infrastructure concern.

**[A]** The test for whether a feature strengthens or weakens the community: Does this feature incentivize students to share knowledge with the group, or does it incentivize them to only consume? Features that reward contribution are strategically sound. Features that make individual consumption better than collective contribution are strategically dangerous.

### The Bloomberg Terminal Analogy

**[A]** Bloomberg did not create financial intelligence. Financial markets created it. Bloomberg captured, structured, and made it accessible. RUMMAN's relationship to SEU Telegram communities is identical: the communities create the intelligence; RUMMAN captures, structures, and surfaces it.

**[B]** This analogy predicts the B2B direction: just as Bloomberg sells terminal access to institutions, RUMMAN will eventually sell institutional intelligence access to universities themselves. The university pays to understand what its students collectively know, struggle with, and ask.

### Community Intelligence as Moat

**[A]** The accumulated corpus is the competitive moat, not the technology. Any technically competent team could replicate the RUMMAN architecture in a month. No team could replicate three years of accumulated SEU community intelligence.

**[B]** The moat widens with every passing semester. Each exam cycle adds new exam papers, new signal extractions, new pattern data. The longer RUMMAN runs, the harder it is to replicate from scratch. This creates a time-based competitive advantage that doesn't exist in pure AI products.

**[C]** Open question: How durable is this moat if a well-resourced competitor committed to building the same corpus for SEU from scratch? What is the minimum corpus size that makes replication economically irrational?

---

## 3. Trust Doctrine

### The Trust Ladder

**[A]** Students grant trust to RUMMAN at four levels, in order. Each level must be established before the next is possible:
1. **Information trust** — "RUMMAN gives me accurate, grounded information about my course"
2. **Pattern trust** — "RUMMAN identifies patterns across exams that I can rely on for preparation"
3. **Recommendation trust** — "RUMMAN can tell me what to study, not just what exists"
4. **Decision trust** — "RUMMAN can tell me how to allocate my study time across courses"

**[A]** These levels are sequential dependencies, not parallel options. A student who has not established information trust will not accept pattern claims. A student who has not established pattern trust will not follow recommendations. The trust ladder defines the product roadmap.

**[A]** RUMMAN is currently operating at Level 1 (information trust). Stage 2 (Semester Companion) targets Level 2. Stage 3 (Academic Copilot) targets Level 3. Stage 4 (Academic Operating System) requires Level 4.

**[B]** The transition from pattern trust to recommendation trust is the hardest because it requires the student to act on RUMMAN's output rather than simply verify it. A student who studies the wrong topic because RUMMAN was wrong suffers a real cost (exam failure). This is why confidence modeling is essential before Stage 3.

### Confidence as the Product

**[A]** Students do not pay for AI answers. They pay for the confidence that they have not missed anything important before a high-stakes exam. This is the core monetization insight. The psychological job-to-be-done: "I need to know what's likely on this exam, and I need to be sure I haven't missed anything."

**[A]** The calibration is against exam failure cost, not AI service prices. A failed course at SEU costs approximately SAR 3,000 to repeat. An SAR 79 semester subscription is less than one tutoring hour. Positioned correctly, RUMMAN is extraordinarily cheap for what it provides.

**[A]** Silence is better than hallucination. A system that says "I don't know" 30% of the time is better than one that confidently says wrong things 5% of the time. One wrong high-confidence answer about exam topics can permanently destroy the student relationship.

**[A]** The zero-result response ("لا أجد معلومات كافية") is a success state, not a failure. It means the anti-hallucination guarantee is holding. The appropriate product response to a high zero-result rate is to expand the corpus, not to relax the guarantee.

### Recommendation Authority

**[B]** RUMMAN can eventually achieve the authority to say "study chapters 3, 5, and 7 for the IT362 midterm" — but only after establishing pattern trust through consistent accuracy on historical patterns. Recommendation authority must be earned through demonstrated accuracy, not assumed through AI capability.

**[C]** Open question: What is the minimum number of accurate pattern predictions required before a student grants recommendation authority? Is this individual or population-level?

---

## 4. Product Evolution Doctrine

### Stage 1 — Finals Companion (Current)

**[A]** The product promise: "I know more about this course's exam history than you can learn from reading three years of Telegram messages."

**[A]** The unit of value is the course, not the student. Anonymous use is acceptable.

**[A]** Usage is episodic and exam-season driven. Students will use RUMMAN intensively in the two to three weeks before exams.

**[A]** The unlock event that advances to Stage 2: **Enrollment Declaration** — a student voluntarily tells RUMMAN which courses they are taking this semester. This single act transforms the product from course-centric to student-centric.

**[B]** The critical hypothesis: students will declare their enrollment if the product provides clearly better answers for enrolled students than for anonymous users.

### Stage 2 — Semester Companion

**[A]** The product promise: "I know your courses, your gaps, and where we are in the semester."

**[A]** The unit of value shifts from the course to the student's semester. RUMMAN can provide cross-course prioritization and personalized weekly briefs.

**[A]** The unlock event that advances to Stage 3: **Second-Semester Retention** — a student returns to RUMMAN for a second semester. This proves the product delivered enough value to earn a repeat engagement.

### Stage 3 — Academic Copilot

**[A]** The product promise: "I know how you study, not just what you study."

**[A]** The unit of value is the individual student across their academic career.

**[A]** The unlock event that advances to Stage 4: **Outcome Loop** — connecting preparation patterns to actual exam results.

### Stage 4 — Academic Operating System

**[B]** The product promise: "Tell me your constraints and I'll tell you what to do."

**[B]** This stage requires institutional partnership. The business model shifts — the university buys, not the individual student.

**[A]** Institutional pricing at SAR 20–30 per student per semester is appropriate for Stage 4.

### Why This Sequence Exists

**[A]** Each stage depends on data accumulated in the previous stage. The sequence is not a product roadmap — it is a data dependency chain. Stage 2 cannot exist without Enrollment Declaration data. Stage 3 cannot exist without multi-semester behavioral data.

**[A]** The transitions are triggered by specific unlock events, not timelines. Stage 2 does not begin because 90 days passed; it begins because Enrollment Declaration adoption reaches a meaningful threshold.

**[A]** Data collection for future stages must begin in Stage 1. Course-query association per student, first-query timing relative to exam window, query type distribution — these signals cannot be reconstructed retroactively.

---

## 5. Product Form Doctrine

### The Telegram Bot (Current)

**[A]** The current product form is a Telegram bot. Students already live in Telegram; zero new app installation required; exam-season queries have a conversational shape.

**[A]** The chatbot form has structural limitations: no structured output for complex comparisons, no visual progress indicators, no persistent dashboard. These limitations become more acute at Stage 2 and beyond.

### The Telegram Mini App Option

**[B]** A Telegram Mini App would unlock capabilities the chatbot cannot provide: structured displays of exam topics, progress tracking, visual course coverage maps, notification preferences. The distribution advantage (stays within Telegram) is preserved.

**[B]** The Mini App is likely the right transition for Stage 2. A weekly brief that lists exam topics with confidence levels and historical patterns cannot be rendered well in a text conversation.

**[C]** Open question: At what product stage does the Mini App transition become a priority? The trigger should be: when structured output is needed to deliver the Stage 2 promise that cannot be adequately expressed in text.

### Hybrid Possibilities

**[B]** The likely optimal architecture for Stage 2–3: Telegram bot as primary interaction point (conversation, quick questions, notifications), Mini App for structured outputs (weekly briefs, study plans), web dashboard for institutional buyers (Stage 4).

---

## 6. Monetization Doctrine

### What Students Actually Pay For

**[A]** Students pay for confidence. The specific form: "I have not missed anything important before a high-stakes exam." Not AI answers. Not summaries. Confidence calibrated to actual historical data about their specific courses.

**[A]** The competitor is not ChatGPT or Google. The competitor is the 30–60 minutes a student spends searching Telegram history before every study session, multiplied by 5 courses, multiplied by 15 weeks per semester.

**[A]** The pricing hypothesis is SAR 79 per semester for individual access. Less than one physical textbook, less than one tutoring hour, and less than 3% of the cost to repeat a failed course.

**[A]** Semester-based pricing (not monthly) is intentional. It aligns with the academic calendar and eliminates the summer churn problem. Monthly pricing would produce massive summer churn.

### What Remains Free (Always)

**[A]** These features must always be free, permanently:
- Basic exam lookup (past exam files)
- Academic calendar queries
- General course signals (most discussed topics)
- Community contribution (submitting files) — must be free because this feeds the upstream intelligence network

**[A]** Charging for community contribution would be strategically catastrophic. The upstream network model depends on students contributing freely.

### The Anti-GPT-Wrapper Test

**[A]** Before every synthesis feature is shipped: "Can a student get this answer by typing the same question into ChatGPT?" If yes, the feature has failed. Features that survive the test require the SEU corpus: exam topic probability, course coverage gaps, historical pattern matching.

**[C]** Open question: What is the right free tier boundary? Too generous reduces conversion; too restrictive reduces trust. Requires empirical testing with real student cohorts.

---

## 7. Data Asset Doctrine

### Community Knowledge (The Living Layer)

**[A]** The Telegram corpus — 72,000+ messages, continuously growing — is the living intelligence layer. It decays in value over 18–24 months without maintenance because courses, professors, and exam formats change. Keeping the community connection active is not an infrastructure concern; it is a product survival concern.

**[A]** Value hierarchy: exam papers > exam tips from students who recently sat the exam > professor announcements > general discussion > social messages. The signal extraction layer (exam_emphasis, professor_note, confusion_cluster, difficulty) is the mechanism for surfacing high-value content.

### The Exam Corpus (The Irreplaceable Asset)

**[A]** Past exam papers are the highest-value asset class in the corpus. The accumulation of exam papers over multiple academic years creates a pattern layer that no single student or study group could maintain.

**[A]** The exam corpus compounds with time. A corpus with exam papers from three years is dramatically more valuable than a corpus with one year of papers because topic repetition patterns become statistically reliable.

**[B]** The exam corpus is legally ambiguous. Students upload exam papers that may contain copyrighted material or institutional IP. This risk needs resolution before institutional partnerships.

### Official University Content (The Authority Layer)

**[A]** Official university content — study plans, course descriptions, regulations, academic calendar — provides the authoritative grounding that community content alone cannot.

**[A]** The authority tier system (official > verified > community) is the mechanism. When an official study plan contradicts a student's claim about course content, the official document wins.

**[A]** 153 official SEU documents have been ingested. Ongoing maintenance: adding new academic year documents as published.

### Student Behavior Signals (The Intelligence Layer)

**[A]** Student behavior signals — which queries students ask, when relative to exam dates, which topics require multiple queries — are the raw material for Stage 3. These signals cannot be reconstructed retroactively.

**[B]** The most valuable behavioral signal not yet captured systematically: the "topic resolution event" — the moment a student stops asking about a topic and moves on. This would distinguish topics students understood from topics they remained confused about.

### Long-Term Intelligence Accumulation

**[A]** Every academic cycle adds to the corpus and makes the pattern layer more reliable. The platform becomes more valuable the longer it operates without discontinuity.

**[B]** The intelligence accumulation advantage compounds differently by course type. Core required courses accumulate rich signal data quickly. Elective courses with small cohorts accumulate slowly.

---

## 8. Strategic Constraints

### Why SEU Is the Current Focus

**[A]** The strategic constraint: "Architect for multiple domains, execute for a single domain." The multi-tenant architecture is built; the execution focus is SEU. This distinction is intentional.

**[A]** SEU has 200,000+ students, is a distance-learning institution (students depend heavily on digital community knowledge), and has a rich Telegram ecosystem. It is the best possible first market.

**[A]** Multi-university expansion before SEU mastery would dilute corpus quality for each university. Deep coverage of one university is more defensible than thin coverage of many.

**[B]** The SEU focus is intended to hold for 12–18 months from the current state. Phase 3 (Multi-University Expansion) should not begin until SEU demonstrates student retention (students returning for a second semester) and reasonable corpus coverage across the top 50 most-queried courses.

### Why Multi-University Expansion Is Deferred

**[A]** The architectural infrastructure for multi-tenancy is complete (tenant_id on all tables, tenant-scoped queries, inst_* table naming). Adding a second university is an operational task, not an engineering task.

**[A]** The strategic reason for deferral is product validation, not infrastructure readiness.

**[B]** The trigger for Phase 3: second-semester retention rate exceeds 50%. More than half of students who used RUMMAN in Semester 1 return in Semester 2. This would prove the product delivered real value, not just novelty.

### Architecture vs. Execution Intentionally Different

**[A]** A system architected for one domain could never scale. A system executed for one domain can master it.

**[A]** Every engineering decision should be evaluated for platform generalizability even while execution is SEU-specific. Adding a column that only applies to SEU instead of using a generic design is technical debt that slows the second university.

---

## 9. Rejected Paths

### Monthly Subscription Pricing
**[A]** REJECTED. Monthly pricing creates massive summer churn. Students think in academic cycles. The pricing model must match the mental model.

### Per-Query Billing (AI Metering)
**[A]** REJECTED. Per-query billing positions RUMMAN as a GPT wrapper and creates an adversarial relationship with the product. Charging for inference makes inference the product. Charging for access makes the corpus the product.

### LangChain / Agent Frameworks
**[A]** REJECTED. These frameworks own control flow and hide operational state. RUMMAN's pipeline is DB-driven — all job coordination and state lives in Postgres. External agent frameworks break observability and replay capability.

### Institutional Selling Before Stage 3
**[A]** REJECTED as a primary go-to-market strategy before Stage 3. The trust sequence must run through students first. The student product is the proof of concept for the institutional product.

### General Academic Assistant (Before SEU Mastery)
**[A]** REJECTED. Deep SEU coverage creates a product competitors cannot replicate for SEU. General coverage of all Saudi universities creates a product anyone could replicate.

### Building a Competitor to Telegram Groups
**[A]** REJECTED permanently. Any feature that attempts to replace Telegram groups cuts off the upstream intelligence source. RUMMAN must strengthen Telegram communities, not replace them.

### Supabase Client Library
**[A]** REJECTED (technical). Abstracts HTTP behavior in ways that prevent precise control over conditional PATCH, 409 deduplication semantics, and Prefer header combinations. See ADR-0009.

### External Vector Databases
**[A]** REJECTED until pgvector is the measured bottleneck. At current scale, pgvector HNSW performance is adequate. See ADR-0007.

---

## 10. Unresolved Founder Questions

### Product Form
**[C]** When is the right moment to transition from Telegram bot to Telegram Mini App? The trigger should be: when structured output is needed to deliver the Stage 2 promise that cannot be adequately expressed in text.

**[C]** Should RUMMAN ever build a standalone web application independent of Telegram? Likely appropriate for institutional buyers (Stage 4); probably premature for Stages 1–3.

### Distribution
**[C]** How does RUMMAN reach students who are not already in the monitored Telegram groups? The current channel is organic word-of-mouth within study groups.

**[C]** Should RUMMAN actively promote itself within SEU communities, or let organic discovery drive growth? Active promotion creates faster adoption but risks perception as spam.

### Monetization Validation
**[C]** The SAR 79/semester price hypothesis has not been tested with real students. Real willingness to pay may differ from theoretical willingness to pay. Requires a cohort test.

**[C]** What is the conversion rate from free tier to paid? Finding the right free/paid boundary requires empirical testing.

**[C]** Is the group plan (SAR 249 for 5 students) the right mechanism for viral growth? Study groups are the natural unit of exam preparation, but implementing group features adds product complexity.

### Trust and Community Evolution
**[C]** How should RUMMAN handle the transition when a student's Enrollment Declaration reveals courses with very thin corpus coverage? The right product response (acknowledge the gap, direct to contribute) has not been fully designed.

**[C]** What incentive structure should RUMMAN use to encourage community contribution? Currently there is no explicit incentive — contribution is rewarded indirectly through better answers.

**[C]** When communities evolve beyond Telegram (WhatsApp, Discord, or a future platform), what is RUMMAN's strategy? The three-account Telegram architecture cannot be replicated to WhatsApp (no user API equivalent).

### AI Dependency
**[C]** At what point does it make sense to fine-tune a small Arabic academic model rather than relying entirely on OpenAI? Probably a 2–3 year horizon; viable when enough validated Q&A pairs exist for a training set.

**[C]** What is the right synthesis model as the corpus grows? When should the synthesis model be upgraded, and what is the cost threshold?

### Telegram Dependency
**[C]** If Telegram were banned or restricted in Saudi Arabia (a non-trivial political risk), what is the contingency? The corpus already accumulated would survive; the living intelligence generation would stop.

**[C]** How does RUMMAN maintain community relationships at scale? Currently three accounts join and monitor groups manually. At 500+ groups across multiple universities, manual management becomes operationally difficult.

---

## Cross-Cutting Principles

**[A]** Silence is better than hallucination. In every domain — synthesis, attribution, signal extraction, intelligence items — the system must prefer saying nothing to saying something wrong.

**[A]** Every AI output is a hypothesis until grounded. The claim model (machine_asserted → confirmed/rejected) applies not just to attribution but to every AI-generated claim in the system.

**[A]** Cheapest path first. Every pipeline step must ask: can we get this result for free or cheaply before spending API budget? Regex before LLM. Keyword hints before classification. Cache before synthesis.

**[A]** Postgres as the control plane. All coordination, all state, all observability lives in Postgres. Any component whose state cannot be understood by reading Postgres tables has a design problem.

**[B]** The product is currently in the most critical phase: moving from anonymous use (Stage 1, no user identity) to declared use (Stage 1 with Enrollment Declaration). This transition generates the behavioral data required for all subsequent stages. Everything in the next 6 months either accelerates or retards this transition.

---

*This document should be reviewed and updated each time a major product stage transition occurs or a strategic decision is made that changes the product direction. Tier 2 (Maintained) — AI may draft updates, human reviews before merge.*
