# Open questions

When a question is answered, move it to **Answered** with the date, the answer and a link
to the ADR or report. Mark questions that block a phase.

## Open

| # | Question | Blocks | Owner | Notes |
|---|---|---|---|---|
| Q1 | Which open-weight decision model is the on-prem default for L1b, and on what reference hardware (GPU class / CPU)? | F2 default | product + eng | F0 `l1-providers` eval; ADR-0004; `reference/onprem-decision-models.md` |
| Q2 | Ask TypeSafe whether a self-hosted / VPC Jev exists (their enterprise FAQ lists the question without a public answer) | cloud vs on-prem parity | product | Would let cloud and on-prem run the same model |
| Q3 | Turkish accuracy of every L1b candidate (open-weight and hosted)? | Q1 | eng | No vendor publishes Turkish results |
| Q4 | Data processing terms for hosted options: TypeSafe is US-hosted (ZDR enterprise-only); OpenAI offers EU regional processing and ZDR. KVKK cross-border basis even for redacted text | cloud tenants | product / legal | `reference/typesafe-jev.md`, `reference/openai-decisions-api.md` |
| Q7 | Where does gpt-oss-safeguard run for L2 in cloud and on-prem? (Latency is no longer on the request path; throughput and cost are.) | F3 | eng | Groq is "Preview"; Azure has no serverless option; Bedrock EU regions available (EOL ≥ 2026-10-29); on-prem needs ≥ RTX 4090-class. F0 latency probe. `reference/gpt-oss-safeguard.md` |
| Q8 | Turkish NER: keep off, use a Turkish transformer via Presidio, or a multilingual model (e.g. GLiNER)? | `PERSON` entity | eng | See `reference/turkish-nlp.md` |
| Q9 | Session history key and window defaults (session → agent → user; 30 min; 20 records) | F3 | eng | |
| Q10 | Will callers (extension, SDK) send `subject.role/department` and `destination.class`? Without them L1a context rules have nothing to work with | F2 usefulness, F4 | product | |
| Q11 | Redaction UX: may the browser extension replace text for the user (`ALLOW_WITH_MODIFICATIONS`), or only warn? | L1a redaction rules defaults | product | |
| Q12 | Image input (screenshots) in scope? OpenAI Decisions supports images; Jev only experimentally; on-prem candidates unknown | provider choice | product | |
| Q17 | Where does the policy control plane (dashboard, drafts, signing) live: a separate service in this repository or its own repository? | F5 | product | ADR-0013 |
| Q18 | Who signs bundles on-prem: the customer's control plane with its own key, or UMAI-signed bundles imported as files? | F5, on-prem | product | ADR-0012/0013 |
| Q13 | Event delivery beyond R&D: what durable mechanism (outbox table, queue), retention, and who owns finding creation? | F4 | eng | |
| Q14 | Default `review_when` selection and `finding_threshold` | F3 | product + eng | |
| Q15 | Should L2 results feed a session-risk fact into *later* L1 decisions? | after F3 | product | Not part of ADR-0010 |

## Answered

| # | Question | Answer | Date | Record |
|---|---|---|---|---|
| A1 | Is L2 synchronous or asynchronous? | Asynchronous: L1 decision is final; L2 reviews afterwards (the earlier synchronous decision of 2026-10-12 is superseded) | 2026-10-15 | ADR-0010 |
| A2 | What is "JEV"? | TypeSafe AI's Jev decision model | 2026-10-12 | ADR-0004, `reference/typesafe-jev.md` |
| A3 | What is the "OpenAI Decision API"? | OpenAI Decisions API (DevDay 2026, GPT-6 Luna) | 2026-10-12 | ADR-0004, `reference/openai-decisions-api.md` |
| A4 | Relationship to `umai-engine` | Will replace it; standalone R&D for now | 2026-10-15 | ADR-0008 |
| A5 | Documentation language | English | 2026-10-15 | ADR-0008 |
| A6 | Must the L1b decision model be on-prem deployable? | Yes — a Jev-like model that can run on-prem | 2026-10-15 | ADR-0004 (amended) |
| A8 | Relationship to `umai-platform` | None for now: completely separate project, nothing read from, written to or linked into it | 2026-10-15 | ADR-0008 |
| A7 | Does the OpenAI Decisions API return probabilities? | Yes for choice/score/predicate; any question may return `refusal` (→ review) | 2026-10-15 | `reference/openai-decisions-api.md` |
