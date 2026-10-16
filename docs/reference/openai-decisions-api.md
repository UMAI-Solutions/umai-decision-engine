# OpenAI Decisions API

**Last verified:** 2026-10-15 · **Used by:** L1b (candidate) · Labels: see [README](README.md)

## What it is and status

A dedicated endpoint that returns typed answers (`predicate`, `choice`, `score`) "about
10x faster than the Responses API". **Only `gpt-6-luna` is supported.**
([guide](https://developers.openai.com/api/docs/guides/decisions))

| Date | Event | Source |
|---|---|---|
| 2026-09-29 | Announced at DevDay 2026, "limited preview" | [@OpenAIDevs](https://x.com/OpenAIDevs/status/2105003318917697873) |
| 2026-10-06 | **Public beta** ("Released the Decisions API in beta with gpt-6-luna"); GA "in the coming weeks" | [changelog](https://developers.openai.com/api/docs/changelog) |
| 2026-10-06 | Listed on OpenRouter as `openai/gpt-6-luna-decisions` | OpenRouter |

openai.com DevDay pages return 403 to automated fetches; use developers.openai.com.

## API

`POST https://api.openai.com/v1/decisions`, `Authorization: Bearer $OPENAI_API_KEY`.
Regional processing: `eu.api.openai.com` or `us.api.openai.com`
([your data](https://developers.openai.com/api/docs/guides/your-data)).
Reference: [create decision](https://developers.openai.com/api/reference/resources/decisions/methods/create).

### Request

| Field | Notes |
|---|---|
| `model` | `gpt-6-luna` only |
| `input` | string, or array of items: messages (user/developer/system/assistant), **`function_call` / `function_call_output` / custom tool call items**, images; text parts ≤ 10 485 760 chars each; ≤ 128 images |
| `questions` | array of **1–200** questions, each with optional `name` |
| — `predicate` | `instructions` |
| — `choice` | `instructions`, `choices`: 2–255 unique `{value, description}`; value string or boolean |
| — `score` | `instructions`, `levels`: `{label, description}`; max level count **unverified** |
| `prompt_cache_key`, `prompt_cache_options`, `safety_identifier` | optional |

```bash
curl https://api.openai.com/v1/decisions \
  -H "Authorization: Bearer $OPENAI_API_KEY" -H "Content-Type: application/json" -d '{
  "model": "gpt-6-luna",
  "input": "I was charged twice for my order.",
  "questions": [
    {"type": "choice", "name": "department", "instructions": "Which department should handle this complaint?",
     "choices": [{"value": "billing", "description": "Payments, invoices, and refunds."},
                 {"value": "technical", "description": "Problems using the product."},
                 {"value": "shipping", "description": "Delivery and tracking."},
                 {"value": "other", "description": "Requests outside these categories."}]},
    {"type": "score", "name": "severity", "instructions": "How severe is this issue?",
     "levels": [{"label": "Cosmetic"}, {"label": "Workaround available"}, {"label": "Fully blocked"}]},
    {"type": "predicate", "name": "refund_requested", "instructions": "Does the customer ask for money back?"}]}'
```

### Response

`answers` is an **array in question order**:

```json
{"answers": [
  {"type": "choice", "name": "department", "choice": "billing",
   "probabilities": [{"value": "billing", "probability": 0.95}, {"value": "technical", "probability": 0.02},
                     {"value": "shipping", "probability": 0.01}, {"value": "other", "probability": 0.02}],
   "confidence": 0.93},
  {"type": "score", "name": "severity", "score": 1.1,
   "probabilities": [{"value": 0, "label": "Cosmetic", "probability": 0.1},
                     {"value": 1, "label": "Workaround available", "probability": 0.7},
                     {"value": 2, "label": "Fully blocked", "probability": 0.2}],
   "confidence": 0.55},
  {"type": "predicate", "name": "refund_requested", "probability": 0.92}],
 "usage": {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}}
```

- **`refusal`**: any question may come back as `{name, type: "refusal"}` while others are
  answered. The engine treats a refusal as "no answer, select for review", never as an answer.
- Per-option probabilities **are** returned for choice and score; predicate returns a probability.

### SDKs

Python: `client.decisions.create(model="gpt-6-luna", input=..., questions=[...])`; requires
`openai` ≥ 3.26.0 (3.31.0 on PyPI, 2026-10-14). JS, Go, Ruby, Java SDKs exist.

### Limits

- Images: the guide says base64 data URLs only; the API reference also allows public
  HTTP(S) URLs (**conflict** — use base64).
- `gpt-6-luna` context 1 050 000 tokens, 922 000 max input
  ([model page](https://developers.openai.com/api/docs/models/gpt-6-luna)); a
  Decisions-specific limit is **unverified**.
- **Not available in the Batch API** ([batch guide](https://developers.openai.com/api/docs/guides/batch)).
- Rate limits: Decisions-specific limits not published (**unverified**). `gpt-6-luna` model
  limits: Build 5 000 RPM / 2 M TPM; Launch 10 000 / 10 M; Grow 30 000 / 180 M.

## Pricing

$0.10 per 1M input tokens; no output, cache-read or cache-write charges (guide). Regional
processing premium (+10 % per the Luna model page) and long-context multipliers apply
(> 272k tokens: 2x input, model-level, assumed to carry over). About 2.4x Jev's list price.

## Latency

| Source | Figure |
|---|---|
| Vendor | "~10x faster than the Responses API"; 150 ms vs 1.6 s for a normal Luna call (**secondary source**: [The New Stack](https://thenewstack.io/openai-decision-api-luna/), 2026-09-29) |
| OpenRouter telemetry, 2026-10-15, 30-min window | p50 216 ms · p90 565 ms · **p95 754 ms** · p99 1 149 ms · ~178 k requests · **97.5 % uptime** (1 day) |

The tail is much heavier than Jev's on the same telemetry. Not measured from Turkey or
Azure West Europe; F0 measures that, including against `eu.api.openai.com`.

## Data handling

| Topic | Fact |
|---|---|
| Training | Customer data not used for training |
| Abuse-monitoring logs | Up to 30 days |
| ZDR | Eligible, with limits: prompt-cache KV kept GPU-local up to 24 h; images scanned for CSAM, flagged images retained even under ZDR |
| HIPAA | Eligible under BAA |
| **Regional processing** | **Supported for `/v1/decisions` in US and Europe (EEA + Switzerland)** |
| DPA | Standard OpenAI DPA (not re-verified, **unverified**) |
| On-prem / open weights | None (**secondary source**, The New Stack 2026-10-07) |
| Via OpenRouter | `retainsPrompts: true`, `training: false`, `requiresUserIDs: true`, `is_moderated: true`; **not on OpenRouter's ZDR list**. OpenRouter moderation may block raw jailbreak payloads before classification (**unverified** on this path) → call OpenAI directly for L1b |

Source: [your data](https://developers.openai.com/api/docs/guides/your-data).

## Language and accuracy

- No language statement in the guide; Luna keeps "broad language support" (**secondary
  source**). **No Turkish data.**
- **No accuracy or calibration benchmarks published by OpenAI.** A third-party test
  reportedly found it "2x more expensive and 5–10 % less accurate" than Jev (**secondary
  source, unverified**).
- Format guarantees are not correctness guarantees; option-order/calibration concerns apply
  as for any decision model (**secondary source**).

## Vendor guidance

Positioned for "classify content, route requests, and prioritize work"; set thresholds
from labelled examples by the cost of false positives vs negatives. No official
jailbreak-detection guidance found (**unverified**).

## Mapping to our contract

| `decision-questions` | OpenAI Decisions |
|---|---|
| `condition` | `predicate` (`instructions` = `text`) → `probability` |
| `choice` `{value, description}` | `choice` with `choices[{value, description}]` |
| `score` `levels[]` | `score` with `levels[{label, description}]` |
| `qid` | `name`; answers matched by `name` (fall back to order) |
| `redacted_excerpt` + facts | `input` as developer message (policy + facts) + user message (content) |
| agent tool phases | optional: `function_call` / `function_call_output` items for `TOOL_INPUT` / `TOOL_OUTPUT` (built from redacted data only) |
| — | `refusal` → `Answer.refused = true` → select for review |

`safety_identifier`: if used, send a salted hash, never a user id.

## Implications for the engine

1. EU regional processing makes OpenAI the easier KVKK/GDPR story for cloud tenants; still
   cross-border for Turkey (Q4).
2. Heavier latency tail: the L1b runtime budget (`l1b.timeout_ms` 600 ms) would cut ~5 % of
   calls at today's p95; measure from the target region before choosing defaults.
3. Handle `refusal` explicitly (select for review), and never route L1b through OpenRouter's
   moderated OpenAI endpoint.
