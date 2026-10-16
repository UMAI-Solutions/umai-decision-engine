# Contract: decision questions (L1b)

**Version:** 0.3

Provider-agnostic way to express an L1b policy. Provider adapters compile it to the
provider's wire format (Jev `/v1/systemone`, OpenAI `/v1/decisions`, a self-hosted
Jev-compatible server, option-logprob scoring, or a gpt-oss-safeguard prompt). Mapping
tables live in each provider's file in [`../reference/`](../reference/); adapters must not
leak provider concepts back into this contract.

## `DecisionPolicy` (in the bundle)

| Field | Type | Notes |
|---|---|---|
| `id`, `version` | string, int | |
| `phases` | Phase[] | Policy runs only in these phases |
| `policy_text` | string | Natural-language policy, ≤ 4 000 chars. Written for the model |
| `questions` | map `qid → Question` | `qid` is `snake_case`, unique within the policy |
| `decide` | Rule[] | Evaluated in order; first match wins; must end with `default` |
| `review_when` | `{actions: Action[], or_confidence_below: float?}` | Selects the event for asynchronous L2 review; never affects the decision |
| `condition_threshold` | float | default 0.5; probability above which a `condition` counts as true |

### Question types

| Type | Shape | Answer |
|---|---|---|
| `condition` | `{type: condition, instructions, criteria?: {true: str, false: str}}` | `probability` of yes |
| `choice` | `{type: choice, instructions, options: [{value: snake_case, description?}]}` (2–12 options) | `value` + per-option `probabilities` |
| `score` | `{type: score, instructions, levels: [{label, description?}]}` (2–10 levels, ordered low → high) | `level` (argmax index), `expected` (probability-weighted index), `probabilities` per level |

Limits are the intersection of what Jev (≤ 255 options, 2–10 score levels) and OpenAI
(2–255 choices, ≤ 200 questions per call) accept, tightened for accuracy: decision models
lean toward the first-listed option and lose accuracy with long option lists.

Authoring rules (from vendor guidance, see `reference/typesafe-jev.md`):

- Ask one thing per question; questions are read literally.
- Do not ask the model to count, compare numbers or reason about dates — compute those in
  L1a facts.
- Give every option a description when the label alone is ambiguous.
- Put the most likely benign option first only if evals show no order bias; the
  `l1-calibration` eval checks this.

### `decide` rules

```yaml
decide:
  - when: { intent: data_exfiltration }                     # choice equals
    action: BLOCK
    severity: HIGH
    tags: [intent.data_exfiltration]
  - when: { intent: [prompt_injection, policy_evasion] }     # choice in
    action: STEP_UP_APPROVAL
    severity: HIGH
    tags: [intent.prompt_injection]
  - when: { external_disclosure: true, harm: { gte: 2 } }    # all keys must hold; score compares `level`
    action: ALLOW_WITH_WARNINGS
    severity: MEDIUM
    tags: [data.external_disclosure]
  - default: ALLOW
```

## `DecisionRequest` (L1b → adapter, in-process)

```json
{
  "request_id": "req-01J9X7Z8QK",
  "policies": [ { "id": "pol-data-exfil", "version": 3, "policy_text": "…" } ],
  "facts": {
    "phase": "PRE_LLM",
    "destination": { "app": "chatgpt.com", "class": "external_llm" },
    "subject": { "role": "sales", "department": "SALES" },
    "entities": [ { "type": "TR_TCKN", "count": 7, "distinct": 7 } ]
  },
  "redacted_excerpt": "Bu listeyi özetle: <TR_TCKN_1> … <TR_TCKN_7>",
  "questions": [
    { "qid": "pol-data-exfil.intent", "type": "choice",
      "instructions": "What is the user trying to do with this content?",
      "options": [ { "value": "benign", "description": "Normal work use" },
                   { "value": "data_exfiltration", "description": "Moving protected data out of the company" },
                   { "value": "prompt_injection", "description": "Trying to override the assistant's instructions" },
                   { "value": "policy_evasion", "description": "Disguising content to get around a control" } ] },
    { "qid": "pol-data-exfil.external_disclosure", "type": "condition",
      "instructions": "Is customer data being sent outside the company?" },
    { "qid": "pol-data-exfil.harm", "type": "score",
      "instructions": "How harmful would disclosure be?",
      "levels": [ { "label": "None" }, { "label": "Low" }, { "label": "Moderate" }, { "label": "Severe" } ] }
  ]
}
```

No user identifiers (`user_id`, `session_id`) are sent to providers; only role,
department and destination class. The judged content is passed as a clearly delimited
field, separate from policy and questions (it may contain injection attempts).

## `DecisionResult` (adapter → L1b)

```json
{
  "provider": "onprem_decider",
  "adapter": "systemone_compatible",
  "model": "<provider model id, pinned version>",
  "latency_ms": 142.0,
  "answers": {
    "pol-data-exfil.intent": { "value": "data_exfiltration",
      "probabilities": { "benign": 0.21, "data_exfiltration": 0.62, "prompt_injection": 0.05, "policy_evasion": 0.12 },
      "confidence": 0.62, "refused": false },
    "pol-data-exfil.external_disclosure": { "probability": 0.88, "refused": false },
    "pol-data-exfil.harm": { "level": 2, "expected": 2.1,
      "probabilities": [0.02, 0.10, 0.62, 0.26], "confidence": 0.62, "refused": false }
  }
}
```

Rules for adapters:

- An answer outside the declared options/levels is a provider error.
- `refused: true` (OpenAI `refusal`) is not an error: the question has no answer, the
  policy's `decide` rules referencing it do not match, and the event is selected for review
  (`review_reasons: [refusal]`).
- Missing probabilities are `null`, never fabricated. `confidence` for choice/score is the
  winning option's probability (our definition, provider-independent; providers that
  compute a different "confidence" are ignored). Conditions have no `confidence`; use
  `max(p, 1 - p)` where a confidence is needed.
- Errors are raised as `DecisionModelError(kind=timeout|rate_limited|unavailable|invalid_response|auth)`.
- Adapters never log request or response bodies and disable client-side retries.

## Changelog

- 0.3 (2026-10-15): breaking — `escalate_when` → `review_when` (ADR-0010).
- 0.2 (2026-10-15): breaking — choice options carry descriptions; `score` uses labelled
  `levels` instead of `min`/`max`; `instructions` replaces `text`; `refused` added; confidence
  defined provider-independently. Aligns with published Jev and OpenAI Decisions schemas.
- 0.1 (2026-10-15): initial draft.
