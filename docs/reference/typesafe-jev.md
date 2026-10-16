# TypeSafe Jev

**Last verified:** 2026-10-15 · **Used by:** L1b (candidate default) · Labels: see [README](README.md)

## What it is

A "System One" **decision model**: non-generative, returns typed answers with
probabilities and no text, reasoning or explanation. Trained with "Reinforcement Learning
for Calibrated Decisions (RLCD)". Same weights for every account, not fine-tuned on
customer data. Architecture and parameter count undisclosed.
Sources: [docs: System One](https://docs.typesafe.ai/concepts/system-one),
[docs: models](https://docs.typesafe.ai/models),
[launch blog, 2026-09-15](https://typesafe.ai/blog/introducing-system-one-models-and-jev).

## Versions and access

| Model id | Released | Notes |
|---|---|---|
| `jev-1.14.0` | 2026-10-14 | Adds experimental `box` questions |
| `jev-1.13.0` | 2026-09 | Stable |

- **Alias conflict:** on TypeSafe `jev-latest` → 1.13.0 and `jev-preview` → 1.14.0; on
  OpenRouter `~typesafe/jev-latest` → 1.14. **Always pin an explicit version**; thresholds
  drift across versions (TypeSafe also advises pinning).
- **Direct:** "early access"; key from console.typesafe.ai.
- **Via OpenRouter:** open to any OpenRouter key, no waitlist
  ([OpenRouter guide](https://openrouter.ai/docs/guides/community/jev)). OpenRouter ids:
  `typesafe/jev-1.13`, `typesafe/jev-1.14`. `typesafe/jev-router` is a model router that
  outputs text, **not** a decision endpoint.
- OpenRouter's default `GET /api/v1/models` lists text-output models only; use
  `?output_modalities=all` to see decision models.

## API

| Surface | Endpoint | Auth |
|---|---|---|
| TypeSafe direct | `POST https://api.typesafe.ai/v1/systemone`, `GET /v1/models` | `Authorization: Bearer $TYPESAFE_API_KEY` |
| OpenRouter, TypeSafe-compatible | `POST https://openrouter.ai/api/v1/systemone` | OpenRouter key |
| OpenRouter, normalized decisions (alpha) | `POST https://openrouter.ai/api/alpha/decisions` | OpenRouter key |

Sources: [API docs](https://docs.typesafe.ai/api),
[OpenRouter TypeSafe SDK guide](https://openrouter.ai/docs/guides/community/typesafe-sdk),
[OpenRouter OpenAPI](https://openrouter.ai/openapi.json).

### Request

```json
POST https://api.typesafe.ai/v1/systemone
{
  "state": "Help! My payouts have been failing for 3 days.",
  "model": "jev-1.13.0",
  "questions": {
    "is_urgent":   {"type": "noul", "instructions": "Does this convey urgency?",
                    "criteria": {"true": "Explicitly time-sensitive", "false": "No urgency expressed"}},
    "department":  {"type": "choice", "instructions": "Which team should handle this?",
                    "criteria": {"billing": "Payments, invoicing, refunds",
                                 "technical": "Bugs, outages, integrations",
                                 "sales": "Pricing, upgrades, new accounts"}},
    "frustration": {"type": "score", "instructions": "How frustrated is the customer?",
                    "criteria": ["Calm", "Frustrated", "Very angry"]}
  }
}
```

- `state`: the input (string or JSON). `questions`: a **map** keyed by question id.

| Type | Fields | Limits | Answer |
|---|---|---|---|
| `noul` (yes/no) | `instructions`, optional `criteria{true,false}` | — | `noul` = P(yes); **no `confidence`** |
| `choice` | `instructions`, `criteria` {option → description or null} | ≤ 255 options | `choice`, `probabilities` (sum 1), `confidence` |
| `score` | `instructions`, `criteria` (ordered array of level labels) | 2–10 levels | `score` (probability-weighted index, float), `legend`, `probabilities`, `confidence` |
| `box` | — | 1.14 only, experimental | bounding box + confidence |

### Response

```json
{"model": "jev-1.13.0",
 "answers": {
   "is_urgent":   {"type": "noul", "noul": 0.95},
   "department":  {"type": "choice", "choice": "billing",
                   "probabilities": {"billing": 0.88, "technical": 0.12, "sales": 0.0}, "confidence": 0.81},
   "frustration": {"type": "score", "score": 1.05, "legend": {"0": "Calm", "1": "Frustrated", "2": "Very angry"},
                   "probabilities": {"0": 0.0, "1": 0.95, "2": 0.05}, "confidence": 0.92}},
 "usage": {"input_tokens": 318, "output_tokens": 34}}
```

Via OpenRouter the response also carries `id`, `provider`, `usage.cost` (USD).

`confidence` is **derived from the probabilities** (1 = all mass on one outcome,
0 = uniform) ([docs: confidence](https://docs.typesafe.ai/confidence)); it adds no
information beyond the distribution.

### Limits

- Body ≤ 32 MiB, ≤ 64 nesting levels, UTF-8 JSON; gzip/zstd accepted.
- Context: 64k tokens per request and 32k per question by default. Header
  `TypeSafe-Danger-Zone-Long-Context: true` raises both to 262k, "slower and less
  accurate". On OpenRouter, requests above ~32 000 bytes of text and all image requests
  run in long-context mode.
- No limit on the number of questions beyond context. Batching all questions in one call
  is "12.2x cheaper and 10.0x faster" (**vendor claim**).
- No asynchronous batch API documented (**unverified** that none exists).
- Images (PNG/JPEG, 256–1 568 px per side, ≤ 16 MiB decoded) are **experimental**
  ("Dreamer-ready"); direct calls need header `TypeSafe-Danger-Zone-Images: true`
  ([docs: images](https://docs.typesafe.ai/images)). The System One page still says text-only.
- Rate limits per organisation (tokens/s, requests/s) in the console; `429` when exceeded,
  `529` when overloaded. No public numbers.

### Python SDK

`pip install typesafe-sdk` (0.7.5, 2026-10-14): `TypeSafeClient`, `AsyncTypeSafeClient`,
typed `Choice` / `Noul` / `Score`, retries with backoff, zstd
([repo](https://github.com/typesafe-ai/typesafe-sdk-python),
[docs](https://docs.typesafe.ai/sdk/python)). Works against OpenRouter with
`base_url="https://openrouter.ai/api"`.

**Engine note:** the SDK retries automatically. On the runtime path retries must be
disabled (our pipeline owns fallback); prefer a thin httpx adapter or configure the SDK
with zero retries and our timeout.

## Pricing

$0.042 per 1M input tokens, output free; same for 1.13, 1.14 and long-context mode
([docs: models](https://docs.typesafe.ai/models)). OpenRouter charges the same. The
vendor says it "can't prove it isn't subsidized" (launch blog).

## Latency

| Source | Figure |
|---|---|
| Vendor | 70–500 ms end to end; "most queries about 100 ms"; cookbooks mean 111–114 ms (**vendor claim**) |
| OpenRouter telemetry, 2026-10-15, 30-min window, jev-1.13 | p50 153 ms · p95 283 ms · p99 380 ms · ~2.75 M requests · 100 % uptime (1 day) |
| OpenRouter telemetry, jev-1.14 | p50 195 ms · p95 338 ms · p99 544 ms |
| preuve.ai, 2026-09-19 | median 273 ms, 4 concurrent requests ([link](https://preuve.ai/blog/jev-ai-review)) |

OpenRouter's exact definition of latency for decision models is **unverified**. None of
these were measured from Turkey or Azure West Europe; F0 measures that.

## Data handling

| Topic | Fact | Source |
|---|---|---|
| Training | Not trained or fine-tuned on inputs or outputs | [privacy policy](https://typesafe.ai/legal/privacy-policy), [enterprise](https://typesafe.ai/enterprise) |
| Retention | "As long as necessary"; **ZDR for enterprise customers only** (contact sales) | [docs: legal](https://docs.typesafe.ai/legal) |
| Via OpenRouter | Both Jev endpoints on OpenRouter's ZDR list (`training:false`, `retainsPrompts:false`) — OpenRouter metadata, not a TypeSafe contract | [ZDR list](https://openrouter.ai/api/v1/endpoints/zdr) |
| Region | "Hosted in the United States"; **no EU residency** published | privacy policy |
| DPA | Published (EU SCCs modules 2 and 3, UK IDTA, Swiss; 72 h breach notice) | [DPA](https://typesafe.ai/legal/data-processing) |
| SOC 2 | Type II on request via Trust Center | enterprise page |
| On-prem / open weights | **None published**; VPC/on-prem FAQ has no public answer (**unverified**) | enterprise page |

## Language and accuracy

- "English is the primary training language … other languages … handled but not equally
  well; test on your own content" ([docs](https://docs.typesafe.ai/models#language-support)).
  **No Turkish evaluation exists.**
- No public-benchmark results from the vendor; vendor "workflow evals" use GPT-6 Astra and
  Fable 5.1 as reference.
- Independent:
  - [arXiv 2609.34024](https://arxiv.org/abs/2609.34024) (medicine, Jev 1.13): accuracy close
    to GPT-6 Sol on PubMedQA (78.4 vs 78.2 %), far below on harder sets (DiagnosisArena 59.8
    vs 82.4 %); better calibration (MetaMedQA ECE 0.063 vs 0.146); picked "cannot answer"
    correctly only 10.5 % of the time.
  - [arXiv 2609.29429](https://arxiv.org/abs/2609.29429) "Just Ask Jev": jailbreak and prompt
    injection among 10 alignment-failure types over 44 benchmarks; one generic zero-shot
    question reached **median AUROC 0.886**, beating supervised baselines on most, 63x cheaper
    than LLM judges. Context fields that encode the label drove much of the result.

## Known weaknesses (vendor's own "jaggedness" page)

[docs: jaggedness jev-1.13](https://docs.typesafe.ai/model-jaggedness/jev-1.13), reviewed 2026-10-02:

- **Option order:** Choice answers lean toward the first-listed option. Mitigation:
  reorder and check consistency.
- **Adversarial content:** "State is data … an injected instruction … can move the answer."
  Critical for a security product: the content we judge may itself attack the judge.
- Literal reading of questions; weak at numbers, counting and dates (→ counting belongs in
  L1a facts, not questions); indirection hurts; accuracy falls with irrelevant state;
  contradictory criteria confuse it.
- Community: calibration holds pooled but not per benchmark; recalibrate on own data
  (**secondary source**). "Black box" ([Simon Willison](https://simonwillison.net/2026/Sep/21/jev/)).

## Vendor guardrail guidance

[Cookbook: Guardrails for LLMs](https://docs.typesafe.ai/cookbooks/llm_guardrails) (jev-1.12,
2026-08-15): one request per message; four `noul` hazard questions (jailbreak/broke_policy,
harmful_request, medical_advice, self_harm) + one `score` for severity 0–3; **thresholds in
code** (strict: review ≥ 0.35, action ≥ 0.70, severity block ≥ 2.0); screen inputs and
outputs. OpenRouter cookbooks: "Gate Agent Tool Calls with Jev", "Auto-Approve Coding Agent
Permission Prompts with Jev".

## Mapping to our contract

| `decision-questions` | Jev |
|---|---|
| `condition` | `noul` (`instructions` = `text`; optional `criteria{true,false}`) → `probability` = `noul` |
| `choice` with options `{value, description}` | `choice` with `criteria{value: description}` |
| `score` with `levels[]` | `score` with `criteria` = level labels; our integer value = argmax of `probabilities`, keep Jev's float `score` as `expected` |
| `qid` (`pol.q`) | question map key (dots allowed? **unverified** — adapter should encode as `pol__q`) |
| `redacted_excerpt` + facts | `state` as JSON `{policy, facts, content}` |

## Implications for the engine

1. Pin `jev-1.13.0` (or 1.14.0 after eval); record the version on every tagged action.
2. Implement an option-order check in the `l1-calibration` eval; consider a runtime
   permutation check only for `BLOCK` outcomes if the eval shows instability.
3. Treat the judged content as hostile: wrap it as a clearly delimited field in `state`,
   keep policy and questions out of the content field; low-confidence events go to L2 review.
4. Data leaves to the US (direct) or via OpenRouter; KVKK assessment required (open question Q4).
