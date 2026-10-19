# Knowledge base

Everything an engineer or coding agent needs to build `umai-decision-engine`. The project
is self-contained; it has no connection to `umai-platform`.

## Map

```text
docs/
├── README.md                     you are here
├── glossary.md                   terms, Turkish ↔ English
├── roadmap.md                    R&D phases F0–F6 and exit criteria
├── open-questions.md             what is not decided yet
├── architecture/
│   ├── overview.md               the L0 → L1 → L2 pipeline, diagrams, principles
│   ├── l0-entity-detection.md    Presidio, Turkish recognizers, redaction
│   ├── l0-entity-catalog.md      target entity types: Lakera parity + Türkiye + Europe
│   ├── l1-policy-decision.md     L1a rules, L1b decision models, question compilation
│   ├── l2-llm-analyst.md         asynchronous review worker, event delivery, findings
│   ├── decision-merge-and-failure.md   precedence, floor, budgets, failure modes
│   ├── policy-lifecycle.md       edit in a dashboard → publish → live (design constraint)
│   ├── deployment-and-editions.md  SaaS vs self-hosted, Community vs Enterprise, entitlements
│   ├── sign-in-and-onboarding.md  sign-up → organization → invitation → first key; surfaces, branding, tests
│   ├── ai-gateway.md             UMAI secure gateway: fit, options, design, build plan (ADR-0034)
│   └── code-layout.md            target package layout and module boundaries
├── contracts/                    versioned data shapes (the source of truth for code)
│   ├── README.md                 versioning rules
│   ├── evaluation-api.md         request / response of the engine
│   ├── gateway-api.md            the UMAI gateway's OpenAI-compatible surface (draft)
│   ├── policy-bundle.md          guardrail configuration consumed by the engine
│   ├── entity-detection.md       L0 output
│   ├── decision-questions.md     provider-agnostic question schema for L1b
│   ├── tagged-action.md          decision event: L1 output, history record, L2 input
│   ├── l2-analysis.md            L2 output and stored analysis
│   ├── tag-taxonomy.md           tag codes
│   └── policy-tests.md           golden cases for a bundle version
├── adr/                          architecture decision records
├── reference/                    external facts: vendors, libraries, algorithms (dated)
├── security/                     threat model, gap analysis, launch plan, verification plan (SaaS readiness)
├── platform-context/             background: what umai-engine (the engine this may replace) does
├── evals/                        eval strategy, datasets, metrics
└── workflow/                     how to develop, test and ship changes here
    └── policy-authoring.md       how the team changes, tests and activates a policy
```

## Reading order for a newcomer

1. [`architecture/overview.md`](architecture/overview.md)
2. [`adr/`](adr/) — skim the titles, read the accepted ones
3. [`contracts/evaluation-api.md`](contracts/evaluation-api.md) and
   [`contracts/tagged-action.md`](contracts/tagged-action.md)
4. The layer document for the area you will work on
5. [`roadmap.md`](roadmap.md) and [`open-questions.md`](open-questions.md)

## Source of truth

| Topic | Source of truth |
|---|---|
| Data shapes | `contracts/` (until code exists); afterwards Pydantic models in `src/`, with `contracts/` kept in sync in the same change |
| Why a decision was made | `adr/` |
| External vendor behaviour | the vendor's own docs; `reference/` is a dated summary |
| Detection design (L0 / L1 / L2) | this knowledge base |

## Freshness

Documents in `reference/` carry a **Last verified** date. Anything older than 30 days is
re-checked before it is relied on for an API shape, price or limit (skill
`verify-vendor-facts`).
