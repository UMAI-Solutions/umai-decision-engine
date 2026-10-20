# ADR-0004 — L1b uses decision models behind a provider interface; on-prem deployable

- **Status:** Accepted (default provider pending F0; first adapter built: OpenAI Decisions, ADR-0040)
- **Date:** 2026-10-12, amended 2026-10-15
- **Deciders:** product owner (UMAI) for the model class and the on-prem requirement; the
  interface is a design proposal

## Context

The product owner chose **decision models** for L1b: models that answer typed questions
(yes/no, choice, score) with per-option probabilities instead of generating text, like
TypeSafe **Jev**. The two hosted products are Jev and the **OpenAI Decisions API**
(public beta since 2026-10-06). Facts: `docs/reference/typesafe-jev.md`,
`docs/reference/openai-decisions-api.md`.

**Amendment 2026-10-15:** the product owner requires the decision model to be
**deployable on-prem** ("a Jev-like model that is on-prem deployable"). Neither Jev nor the
OpenAI Decisions API publishes open weights or an on-prem option. Several open-weight
decision models exist (e.g. `kev-4b`, reportedly Jev-API-compatible; Cloudflare `clef`;
Perplexity `pplx-decider`; `drex`; `Tev1`; Mistral `Shieldstral`), and any open instruct
model can be turned into a decision model by scoring option tokens. These are being
evaluated: `docs/reference/onprem-decision-models.md`.

## Decision

- L1b calls a `DecisionModel` interface. Adapters: `jev` (TypeSafe API), `openai_decisions`,
  `systemone_compatible` (any self-hosted server speaking Jev's `/v1/systemone` contract),
  `local_scoring` (option-logprob scoring on a self-hosted open model), `gpt_oss_safeguard`
  (prompted; last-resort fallback), `fake`.
- **The default L1b model must be on-prem deployable** (open weights or a self-hostable
  licensed container, commercial use allowed, one GPU or CPU). Hosted Jev and OpenAI
  Decisions remain available as cloud options, shadow providers and accuracy references.
- Policies are written once in a provider-agnostic question schema
  (`docs/contracts/decision-questions.md`).
- Provider, fallback provider and shadow providers are chosen per policy bundle.
- The default on-prem model is chosen by the F0 provider evaluation: Turkish accuracy,
  calibration and option-order stability, latency on the reference on-prem hardware,
  license, and API compatibility with Jev.

## Alternatives considered

| Option | Why not |
|---|---|
| Hosted Jev only | No on-prem option; fails the requirement |
| Hosted OpenAI Decisions only | No on-prem option; fails the requirement |
| Only gpt-oss-safeguard prompted to answer questions | Generative, ~1 s per call, no calibrated probabilities; kept as fallback |
| Train our own decision model now | Premature before evaluating existing open models |

## Consequences

- An inference server for the on-prem model becomes part of the deployment (GPU sizing,
  quantization, container) — to be specified after F0.
- If a Jev-compatible open model is chosen, one wire contract covers cloud Jev and on-prem.
- Capability differences (probabilities, question types, image input) are declared by
  adapters and checked at bundle load.

## Validation

F0 provider evaluation report in `evals/reports/` including at least two open-weight
candidates against hosted Jev as reference; this ADR's status line is updated with the
chosen default.
