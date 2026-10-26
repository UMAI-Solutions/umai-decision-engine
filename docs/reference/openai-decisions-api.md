# OpenAI Decisions API

**Last verified:** 2026-10-20 · **Used by:** L1b (candidate) · Labels: see [README](README.md)

## What it is and status

A dedicated endpoint that returns typed answers (`predicate`, `choice`, `score`) "about
10x faster than the Responses API". **Only `gpt-6-luna` is supported.** Still **public beta**
on 2026-10-20 ("we expect to GA in the coming weeks"); the changelog has no GA entry (latest
entry 2026-10-16). ([guide](https://developers.openai.com/api/docs/guides/decisions),
[changelog](https://developers.openai.com/api/docs/changelog), both read 2026-10-20)

| Date | Event | Source |
|---|---|---|
| 2026-09-29 | Announced at DevDay 2026, "limited preview" | [@OpenAIDevs](https://x.com/OpenAIDevs/status/2105003318917697873) |
| 2026-10-06 | **Public beta** ("Released the Decisions API in beta with gpt-6-luna") | [changelog](https://developers.openai.com/api/docs/changelog) |
| 2026-10-06 | Listed on OpenRouter as `openai/gpt-6-luna-decisions` (endpoint `…-20261006`) | [OpenRouter endpoints API](https://openrouter.ai/api/v1/models/openai/gpt-6-luna-decisions/endpoints) |
| 2026-10-14 | Python SDK 3.30.0: image URLs; organization Decisions usage reporting | [openai-python CHANGELOG](https://github.com/openai/openai-python/blob/v3.35.1/CHANGELOG.md) |
| 2026-10-15 | Native Decisions integration in GPT-Live (voice) | [changelog](https://developers.openai.com/api/docs/changelog), [voice guide](https://developers.openai.com/api/docs/guides/decisions-voice) |
| 2026-10-16 | Python SDK 3.32.0: `prompt_cache_key`, `prompt_cache_options` | [openai-python CHANGELOG](https://github.com/openai/openai-python/blob/v3.35.1/CHANGELOG.md) |
| by 2026-10-20 | API reference now says **1–400 questions** (1–200 on 2026-10-15); SDKs still say 1–200 (**conflict**) | [reference](https://developers.openai.com/api/reference/resources/decisions/methods/create) |

Fetching: openai.com DevDay pages return 403 to automated fetches. The developers.openai.com
API reference pages return 404 to non-browser user agents and have no `.md` variant; fetch
them with a browser user agent. Guides have `.md` variants (append `.md`).

## API

`POST https://api.openai.com/v1/decisions`, `Authorization: Bearer $OPENAI_API_KEY`
([reference](https://developers.openai.com/api/reference/resources/decisions/methods/create), 2026-10-20).
Regional processing: base URL `https://eu.api.openai.com/v1` or `https://us.api.openai.com/v1`,
either from a region-pinned project or per request with a key from a Global project; eligibility
rules apply (see Data handling) ([your data](https://developers.openai.com/api/docs/guides/your-data), 2026-10-20).

**Sources disagree on the input surface and the question limit** (all read 2026-10-20):

- **R**: HTTP API reference ([create a decision](https://developers.openai.com/api/reference/resources/decisions/methods/create)).
- **S**: Python reference page ([python](https://developers.openai.com/api/reference/python/resources/decisions/methods/create))
  and SDK types: openai-python v3.35.1 and `main`
  ([decision_create_params.py](https://github.com/openai/openai-python/blob/v3.35.1/src/openai/types/decision_create_params.py),
  [decision.py](https://github.com/openai/openai-python/blob/v3.35.1/src/openai/types/decision.py)), openai-node `master`.
- **G**: the [guide](https://developers.openai.com/api/docs/guides/decisions): input is "a text string or user messages containing text and images".

### Request

| Field | Notes |
|---|---|
| `model` | required; `gpt-6-luna` only (R, S, G) |
| `input` | required; string, or array. **All sources agree on:** user messages `{type?: "message", role: "user", content: string \| [parts]}`. **R only** (**conflict, unverified** until a live call confirms): `developer` / `system` messages (input text only; system "rendered as developer"), `assistant` messages (`output_text` / `refusal` parts, optional `phase`: `commentary` \| `final_answer`), `function_call {call_id, name, arguments, namespace?, id?, status?}`, `function_call_output {call_id, output}`, `custom_tool_call {call_id, name, input}`, `custom_tool_call_output {call_id, output}`; each tool output must match a call by `call_id`. S says "Files, audio, tools, and item references are not supported" (SDK 3.26.0: "Non-user roles, function calls, function-call outputs … are not supported") |
| — `input_text` part | `{type: "input_text", text}`; `text` ≤ 10 485 760 chars; optional `prompt_cache_breakpoint: {mode: "explicit"}` (R) |
| — `input_image` part | `{type: "input_image", image_url, detail?}`; base64 data URL **or** public HTTP(S) URL (R, S, G now agree); `detail`: `low` \| `high` \| `auto` \| `original`; ≤ 128 images per request; no `file_id` |
| `questions` | required; **R and Python reference page: 1–400; SDK docstrings (v3.35.1, `main`, node `master`): 1–200** (**conflict**). "Answers come back in the same order." |
| — every question | `type`, `instructions` (required, ≤ 1 048 576 chars), `name` (optional, ≤ 1 048 576 chars). Guide: "Give each question a unique `name`"; whether duplicates are rejected is **unverified** |
| — `predicate` | no extra fields |
| — `choice` | `choices`: 2–255, "Each choice must be unique"; `{value: string \| boolean, description?}` (description ≤ 1 048 576 chars); a string and a boolean with the same text are distinct values |
| — `score` | `levels`: ordered lowest to highest, `{label, description?}` (each ≤ 1 048 576 chars); min/max level count **unverified** (not stated anywhere) |
| `prompt_cache_key` | optional; ≤ 64 chars (R); typed in SDK ≥ 3.32.0 |
| `prompt_cache_options` | optional; `{mode: "implicit" \| "explicit", ttl: "30m"}`; up to four breakpoints written per request (R) |
| `safety_identifier` | optional; ≤ 128 chars; "never the authenticated user identity" (R) |
| not accepted | `metadata`, `store`, `service_tier`, `user`, `temperature`, `reasoning`, `stream` are not parameters of this endpoint (absent in R and S). There is no per-request retention switch |

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

A `Decision` object with **only** `answers`, `model` and `usage` (no `id`, `object` or
`created`) (R, S). Values below are illustrative (shapes from R and G):

```json
{"model": "gpt-6-luna",
 "answers": [
  {"type": "choice", "name": "department", "choice": "billing",
   "probabilities": [{"value": "billing", "probability": 0.95}, {"value": "technical", "probability": 0.02},
                     {"value": "shipping", "probability": 0.01}, {"value": "other", "probability": 0.02}],
   "confidence": 0.93},
  {"type": "score", "name": "severity", "score": 1.1,
   "probabilities": [{"value": 0, "label": "Cosmetic", "probability": 0.1},
                     {"value": 1, "label": "Workaround available", "probability": 0.7},
                     {"value": 2, "label": "Fully blocked", "probability": 0.2}],
   "confidence": 0.55},
  {"type": "refusal", "name": "refund_requested"}],
 "usage": {"input_tokens": 42, "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
           "output_tokens": 0, "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 42}}
```

- **Order and matching:** `answers` are "in question order" (R, S); the API "echoes" each
  question's `name` (G). `name` is `string | null`, null when the question was unnamed (R, S).
- **Per type:** `predicate` → `probability` (0–1). `choice` → `choice` (one supplied value,
  type preserved), `probabilities[{value, probability}]`, `confidence`. `score` → `score`
  (probability-weighted mean of 0-based level indices, can fall between levels),
  `probabilities[{value: int, label, probability}]`, `confidence` (R, G).
- **`refusal`:** `{type: "refusal", name}`, no reason or other fields. "Any question can return
  a refusal instead of an answer; other questions can still receive answers" (R, S). The
  engine treats a refusal as "no answer, select for review", never as an answer.
- **`usage`** is nested: `input_tokens`, `input_tokens_details.{cached_tokens, cache_write_tokens}`,
  `output_tokens`, `output_tokens_details.reasoning_tokens`, `total_tokens` (R, S). This
  corrects the 2026-10-15 version (flat `cached_tokens` / `reasoning_tokens`).

### Errors

- No Decisions-specific error codes are documented (invalid question, too many questions,
  duplicate names: **unverified**). General API errors apply
  ([error codes](https://developers.openai.com/api/docs/guides/error-codes), 2026-10-20):
  401 (key, organization, IP allowlist); 403 unsupported country; 429 rate limit (follow
  `Retry-After`), 429 `slow_down` (type `rate_limit_error`, ramp rate), 429
  `credit_balance_exhausted` / `organization_spend_limit_exceeded` /
  `project_spend_limit_exceeded` / `organization_usage_limit_exceeded` (retrying does not
  help); 500; 503 `server_is_overloaded` (type `service_unavailable_error`). The Python SDK
  raises `RateLimitError` (429) and `InternalServerError` (503).
- Content refusals are per-question `refusal` answers inside a successful response, not HTTP
  errors (R, S).
- One third-party hands-on review reportedly got 403 "Decision API is not enabled" with a
  standard key (**secondary source, unverified**; seen only in a web-search summary).

### SDKs

Python: `client.decisions.create(model="gpt-6-luna", input=..., questions=[...],
prompt_cache_key=..., prompt_cache_options=..., safety_identifier=...)` (async:
`AsyncOpenAI().decisions.create`), returns `openai.types.Decision`; answers are a union
discriminated on `type`. Minimum `openai` **3.26.0** (guide); typed `prompt_cache_key` /
`prompt_cache_options` need **3.32.0**. Latest **3.35.1** (2026-10-18,
[PyPI](https://pypi.org/project/openai/)). Guide minimums for the others: JavaScript 7.30.0,
Go 3.73.0, Ruby 0.101.0, Java 4.78.0. The SDK also ships `DecisionsToolRunner`
(`openai/lib/decisions_tool_runner.py`), documented as "do not treat a choice as permission".

### Limits

- ≤ 128 images per request; text part ≤ 10 485 760 chars; `image_url` ≤ 1 073 741 824 chars (R).
- `gpt-6-luna` context 1 050 000 tokens, 922 000 max input
  ([model page](https://developers.openai.com/api/docs/models/gpt-6-luna), 2026-10-20); a
  Decisions-specific token limit is **unverified**.
- **Not available in the Batch API**: `/v1/decisions` is not in the batch endpoint list
  ([batch guide](https://developers.openai.com/api/docs/guides/batch), 2026-10-20).
- Rate limits: no Decisions-specific limits published (**unverified**). `gpt-6-luna` model
  limits: Build 5 000 RPM / 2 M TPM; Launch 10 000 / 10 M; Grow 30 000 / 180 M (model page,
  2026-10-20); whether `/v1/decisions` shares them is **unverified**. Above about 1 M TPM,
  grow traffic by no more than 50 % per 15 minutes or expect 429 `slow_down` (error codes).
- **Independent vs joint:** the guide says to "put independent questions in the same
  `questions` array" and to send separate requests "for decisions that depend on an earlier
  answer". The voice guide states "One native decision request answers all three questions
  independently" (**vendor claim**, GPT-Live context). Whether one question's text can
  influence another's answer in `/v1/decisions` is not stated (**unverified**). No guidance on
  one call with many questions vs many calls (latency, cost) (**unverified**).

## Pricing

$0.10 per 1M input tokens; "no cache-read, cache-write, or output-token charges"
([guide](https://developers.openai.com/api/docs/guides/decisions), 2026-10-20). The guide
now confirms "Regional processing premiums and long-context input pricing multipliers apply":
+10 % for regional processing, and > 272k input tokens are billed at 2x input ($0.20)
([model page](https://developers.openai.com/api/docs/models/gpt-6-luna),
[pricing](https://developers.openai.com/api/docs/pricing), 2026-10-20). The pricing page has
no separate Decisions row. About 2.4x Jev's list price (per [typesafe-jev.md](typesafe-jev.md),
not re-checked here).

## Latency

| Source | Figure |
|---|---|
| Vendor | "about 10x faster than the Responses API" (guide, 2026-10-20). No millisecond figure, percentile, region or input size in any OpenAI doc; the [latency guide](https://developers.openai.com/api/docs/guides/latency-optimization) has no Decisions content |
| Press | 150 ms vs 1.6 s for a normal Luna call (**secondary source**: [The New Stack](https://thenewstack.io/openai-decision-api-luna/), 2026-09-29; methodology undisclosed) |
| OpenRouter telemetry, 2026-10-15, 30-min window | p50 216 ms · p90 565 ms · **p95 754 ms** · p99 1 149 ms · ~178 k requests · **97.5 % uptime** (1 day) |
| OpenRouter endpoints API, 2026-10-20 | `latency_last_30m: null` (no sample); uptime 100 % (30 min), **99.93 %** (1 day) |

The tail was much heavier than Jev's on the same telemetry. Not measured from Turkey or
Azure West Europe; F0 measures that, including against `eu.api.openai.com`.

**Live observations, umai-beta (Azure West Europe), 2026-10-26** (our measurements, not vendor
statements; nine policies, about 11 k input tokens per call; ADR-0040 amendment):

- Responses carry `openai-processing-ms`, OpenAI's own time; the rest (about 50–65 ms on an open
  connection) is client and network.
- Processing p50 about 210–270 ms; single calls of 748, 2 064 and 2 533 ms on open connections.
- After 4–5 minutes without calls the first one or two calls are slower (first-call processing
  375–771 ms in five rounds); two calls sent together then were both slow. A one-question
  decision first takes that slow answer itself; `GET /v1/models/gpt-6-luna` (200, 119 bytes,
  0.55–1.2 s) does not.
- `usage.input_tokens_details.cached_tokens` was 0 on every call (about 200), identical bodies
  with the same `prompt_cache_key` included: no implicit prompt-cache hits for this request shape.
  `prompt_cache_options` untested.
- The edge kept idle HTTP/1.1 connections open for at least 25 minutes (3/3).

## Data handling

| Topic | Fact |
|---|---|
| Training | Not used for training |
| Abuse-monitoring logs | Up to 30 days by default |
| Application state | None, with exceptions (prompt cache) |
| ZDR | Eligible, with limits: prompt-cache KV kept GPU-local, not retained after 24 h; images scanned for CSAM, flagged images retained even under ZDR |
| Private Retention with PSP / Safety Retention | "Pending confirmation" for `/v1/decisions` |
| HIPAA | Eligible under an executed BAA and Healthcare Addendum |
| **Regional processing** | **US and Europe (EEA + Switzerland)** for `/v1/decisions`. The endpoint is reachable in all listed regions (AU, CA, JP, IN, SG, KR, GB, AE), but "availability in a region does not imply that inference is performed in that region"; extended prompt caching there may store content outside the region |
| EU conditions | Data residency needs sales approval; non-US regions need approval for abuse-monitoring controls and a Modified Retention amendment; EU requires ZDR, Modified Abuse Monitoring, Private Retention with PSP or Safety Retention; image input in the EU needs enhanced ZDR/MAM approval; +10 % price. TLS terminates in-region (Cloudflare Regional Services) for `us.`/`eu.` hosts. System data (account, usage, billing) may leave the region |
| DPA | Standard OpenAI DPA (not re-verified, **unverified**) |
| On-prem / open weights | None (**secondary source**, The New Stack 2026-10-07) |
| Via OpenRouter | As of 2026-10-15 (not re-checked; the endpoints API no longer shows these fields): `retainsPrompts: true`, `training: false`, `requiresUserIDs: true`, `is_moderated: true`; not on OpenRouter's ZDR list. OpenRouter moderation may block raw jailbreak payloads before classification (**unverified** on this path) → call OpenAI directly for L1b |

Source for all rows except the last three: [your data](https://developers.openai.com/api/docs/guides/your-data)
and the [guide](https://developers.openai.com/api/docs/guides/decisions), 2026-10-20.

## Language and accuracy

- No language statement in the guide; Luna keeps "broad language support" (**secondary
  source**). **No Turkish data.**
- **No accuracy or calibration benchmarks published by OpenAI** (re-checked 2026-10-20). A
  third-party test reportedly found it "2x more expensive and 5–10 % less accurate" than Jev
  (**secondary source, unverified**).
- Format guarantees are not correctness guarantees; option-order/calibration concerns apply
  as for any decision model (**secondary source**).

## Vendor guidance

From the [guide](https://developers.openai.com/api/docs/guides/decisions) (2026-10-20):
positioned to "classify content, route requests, and prioritize work"; set thresholds from
labelled examples by the cost of false positives vs negatives; write questions around
observable criteria, one concern per question, choices with distinct meanings, adjacent score
levels with distinct criteria; include a fallback choice such as `"other"`. Use `choice` for
unordered categories, `score` for ordered levels. No official jailbreak-detection guidance
found (**unverified**).

## Mapping to our contract

| `decision-questions` | OpenAI Decisions |
|---|---|
| `condition` | `predicate` (`instructions` = `text`) → `probability` |
| `choice` `{value, description}` | `choice` with `choices[{value, description}]`; send string values only (types are preserved) |
| `score` `levels[]` | `score` with `levels[{label, description}]` → `score`, `probabilities[].value` = level index |
| `qid` | `name` (always set, unique per request); match answers by `name` and check that order and count match the request; a missing, duplicate or unknown name is a provider error (no silent fallback) |
| `excerpt` + facts | Safe subset: `input` as user messages only. **Implemented** (adapter `l1/decision_models/openai_decisions.py`, ADR-0040): one user message with two `input_text` parts, the labelled UMAI context and facts, then the redacted content between `<<<CONTENT` / `CONTENT>>>` markers (earlier turns as `[role] text`). `developer` role only per R (**conflict**): not used until a live call confirms |
| agent tool phases | `function_call` / `function_call_output` items only per R (**conflict**); until confirmed, render `TOOL_INPUT` / `TOOL_OUTPUT` as labelled text in a user message (redacted data only) |
| question count | cap at 200 per call until the 400 limit is confirmed live |
| — | `refusal` → `Answer.refused = true` → select for review |
| — | `usage.input_tokens`, `usage.input_tokens_details.cached_tokens` for metrics |

`safety_identifier`: if used, send a salted hash (≤ 128 chars), never a user id.

## Implications for the engine

1. EU regional processing makes OpenAI the easier KVKK/GDPR story for cloud tenants, but it
   is not a switch: it needs sales approval, abuse-monitoring controls (ZDR or MAM) and a
   Modified Retention amendment, and costs +10 %. Still cross-border for Turkey (Q4).
2. Heavier latency tail: the L1b runtime budget (`l1b.timeout_ms` 600 ms) would have cut ~5 %
   of calls at the 2026-10-15 p95; no newer telemetry. Measure from the target region before
   choosing defaults.
3. Build the adapter on the surface all sources agree on (string or user messages, ≤ 200
   questions, nested `usage`) and test non-user roles, tool items and 400 questions in F0
   before using them.
4. Handle `refusal` explicitly (select for review), and never route L1b through OpenRouter's
   moderated OpenAI endpoint.
