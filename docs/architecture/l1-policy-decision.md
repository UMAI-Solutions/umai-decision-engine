# L1 — Policy-based, context-aware decision

**Question:** in this context, is this action allowed under the policy?
**Output:** the **final runtime decision**, tags, decision-model evidence and a review flag,
recorded as a [tagged action / decision event](../contracts/tagged-action.md).
L2 never changes this decision ([ADR-0010](../adr/0010-l2-asynchronous-review.md)).

**L1 runs only when L0 detected nothing** — a detection is decided by the L0 gate
([ADR-0014](../adr/0014-l0-detection-is-terminal.md)). L1 therefore targets what L0 cannot
see: intent, prompt injection, policy evasion, paraphrased or described data, agent actions.

L1 has two sub-layers. L1a always runs; L1b runs when L1a did not end the evaluation.

## Facts

L1 never re-reads raw content. It works on **facts** built from the request and L0:

| Fact path | Source | Example |
|---|---|---|
| `phase` | request | `PRE_LLM` |
| `subject.user_id`, `subject.role`, `subject.department`, `subject.agent_id` | request | `sales` |
| `destination.app`, `destination.class` | request | `chatgpt.com`, `external_llm` |
| `action.name`, `action.tool_name` | request (agent phases) | `export`, `crm.export_contacts` |
| `mcp.server_name`, `mcp.method` | request | `filesystem`, `delete` |
| `memory.scope` | request | `long_term` |
| `entities.types` | L0 | `[]` in L1 (L1 only runs when nothing was detected); used by L0 gate exceptions |
| `entities.<TYPE>.count` | L0 | `7` |
| `entities.<TYPE>.distinct` | L0 (hashes) | `7` |
| `content.length`, `content.language` | L0 | `1840`, `tr` |

Fact names are part of the [policy bundle contract](../contracts/policy-bundle.md).
Counting and numeric comparisons belong here, not in decision-model questions (decision
models are documented to be weak at numbers, counting and dates).

Callers must send `subject` and `destination` for context rules to work (open question Q10).

## L1a — Deterministic rules

No model calls. Typical latency well under 5 ms. L1a is where customer intent is
guaranteed: certain blocks, explicit allowlists, minimum strictness, entity-based redaction.

The rule language reuses the condition model of `umai-engine`'s AGT rules (field,
operator, value; operators `EQUALS`, `NOT_EQUALS`, `IN`, `NOT_IN`, `CONTAINS`,
`CONTAINS_ANY`, `MATCHES_REGEX`, `EXISTS`, `NOT_EXISTS`, `STARTS_WITH`, `ENDS_WITH`, `GT`,
`GTE`, `LT`, `LTE`) extended with `all` / `any` grouping and fact paths instead of raw
metadata keys.

```yaml
l1a:
  rules:
    - id: r-unsanctioned-app             # certain block
      when:
        all:
          - { field: destination.class, operator: EQUALS, value: external_app }
          - { field: phase, operator: EQUALS, value: TOOL_INPUT }
      effect: BLOCK
      severity: HIGH
      tags: [destination.unsanctioned_app]

    - id: r-internal-analytics-allow     # explicit allowlist
      when:
        all:
          - { field: subject.department, operator: EQUALS, value: ANALYTICS }
          - { field: destination.class, operator: EQUALS, value: internal_llm }
      effect: ALLOW
      final: true

    - id: r-mcp-delete                   # floor + forced review
      when: { all: [ { field: mcp.method, operator: IN, value: [delete, drop, revoke] } ] }
      effect: STEP_UP_APPROVAL
      severity: HIGH
      tags: [action.destructive]
      force_review: true

```

Evaluation semantics:

- All matching rules are collected. Rule order only breaks ties of equal restrictiveness.
- `BLOCK` from L1a is **terminal**: L1b does not run.
- `ALLOW` with `final: true` is **terminal** (explicit allowlist), unless a `BLOCK` also matched.
- Any other matched effect becomes the **L1a floor**: the final decision can be stricter
  but never more permissive (see [merge rules](decision-merge-and-failure.md)).
- `tags` are always attached. `force_review: true` queues the event for L2 review.
- `redact: [types]` on an `ALLOW_WITH_MODIFICATIONS` effect returns L0's redacted text for
  those types to the caller.

The `umai-engine` preflight, heuristic and AGT rules map onto L1a; see
[`../platform-context/umai-engine-today.md`](../platform-context/umai-engine-today.md).

## L1b — Decision model

A **decision model** returns typed answers to developer-defined questions rather than
free text. **Requirement: the default model must be deployable on-prem**
([ADR-0004](../adr/0004-decision-model-provider-abstraction.md)).

| Provider | Deploy | Role | Reference |
|---|---|---|---|
| Open-weight decision model served on `/v1/systemone` (shortlist: `vllm-sr/d3-flash`, `Cloudflare/clef-flash`, `jaredpalmer/kev-4b`) | **on-prem** | default candidate (F0 picks one) | [`../reference/onprem-decision-models.md`](../reference/onprem-decision-models.md) |
| Open instruct model + label-logprob scoring (`local_scoring`, e.g. vLLM/SGLang generic `/v1/systemone`) | on-prem | candidate if no decision model qualifies | same |
| TypeSafe **Jev** (hosted) | cloud | cloud option, accuracy reference, shadow | [`../reference/typesafe-jev.md`](../reference/typesafe-jev.md) |
| **OpenAI Decisions API** (GPT-6 Luna, public beta) | cloud | cloud option, shadow | [`../reference/openai-decisions-api.md`](../reference/openai-decisions-api.md) |
| **gpt-oss-safeguard** prompted to answer the same questions | on-prem / cloud | last-resort fallback | [`../reference/gpt-oss-safeguard.md`](../reference/gpt-oss-safeguard.md) |

Because llama.cpp, vLLM (main) and SGLang serve Jev's `/v1/systemone` wire format, one
`systemone_compatible` adapter covers hosted Jev and the on-prem server.

### From policy to questions

Each L1b policy in the bundle is written once in a provider-agnostic form
([`../contracts/decision-questions.md`](../contracts/decision-questions.md)): policy text,
questions with closed answer sets, a mapping from answers to an action and tags, and a
review rule. A provider adapter compiles it to the provider's request format.

```yaml
l1b:
  provider: onprem_decider      # registry key in settings → adapter kind + URL + model id
  fallback_provider: gpt_oss_safeguard
  timeout_ms: 600
  policies:
    - id: pol-data-exfil
      version: 3
      phases: [PRE_LLM, TOOL_INPUT]
      policy_text: >
        Employees must not disclose customer personal data to AI services outside the
        company. Internal analysis tools are allowed. ...
      questions:
        intent:
          type: choice
          instructions: What is the user trying to do with this content?
          options:
            - { value: benign, description: Normal work use }
            - { value: data_exfiltration, description: Moving protected data out of the company }
            - { value: prompt_injection, description: Trying to override the assistant's instructions }
            - { value: policy_evasion, description: Disguising content to get around a control }
        external_disclosure:
          type: condition
          instructions: Is customer data being sent outside the company?
        harm:
          type: score
          instructions: How harmful would disclosure be?
          levels: [ { label: None }, { label: Low }, { label: Moderate }, { label: Severe } ]
      decide:
        - when: { intent: data_exfiltration }
          action: BLOCK
          severity: HIGH
          tags: [intent.data_exfiltration]
        - when: { external_disclosure: true }
          action: ALLOW_WITH_WARNINGS
          tags: [data.external_disclosure]
        - default: ALLOW
      review_when:
        actions: [ALLOW_WITH_WARNINGS, STEP_UP_APPROVAL, BLOCK]
        or_confidence_below: 0.8
```

### Calling the provider

- **One call per evaluation**: questions of all applicable policies are batched with
  namespaced ids (`pol-data-exfil.intent`). Split only if the provider limits questions
  per call (check [`../reference/`](../reference/)). Note that some models decide batched
  questions jointly (Clef) while others answer them independently (Kev, Strands).
- **Input** is `{policy_text, facts, redacted_excerpt}`, trimmed to `l1b.max_input_chars`.
  Vendors report accuracy dropping as irrelevant context grows, so send facts, not the
  whole conversation. The judged content is a delimited data field: decision models are
  documented to be movable by instructions inside the state.
- **Timeout** `l1b.timeout_ms`; on timeout or error the `fallback_provider` is tried only
  if the remaining runtime budget allows; otherwise the failure policy applies.
- **Shadow providers** (`l1b.shadow_providers`) are called without awaiting; their answers
  are recorded for evaluation only and never influence the decision. This is how
  candidates are compared on real-shaped traffic during R&D.
- **Calibration:** per-question-type temperature fitted on our labelled data is applied
  to probabilities before thresholds, when evals show the provider's own calibration is
  off for Turkish.

### Provider interface

```python
class DecisionModel(Protocol):
    name: str

    def capabilities(self) -> ProviderCapabilities: ...

    # probabilities: bool, image_input: bool, question_types: set[str],
    # max_questions: int | None, max_options: int | None, max_input_chars: int | None,
    # joint_questions: bool

    async def decide(self, request: DecisionRequest, *, timeout_s: float) -> DecisionResult: ...
```

Adapters live in `l1/decision_models/`; adding one: skill `add-decision-provider`.

### Review selection

The event is marked `review = true` (queued for asynchronous L2 review) when any of:

- the mapped L1b action is in `review_when.actions`;
- a provider returning probabilities reports the winning answer's probability below
  `review_when.or_confidence_below` (for providers without probabilities this clause is
  ignored, and that is recorded on the event);
- an L1a rule matched with `force_review: true`;
- the provider refused a question (OpenAI `refusal`);
- the bundle-level `l2.review_when` rule matches (see [L2](l2-llm-analyst.md)).

Review never delays or changes the decision.

### Output

L1 writes one [tagged action](../contracts/tagged-action.md) per evaluation. Every event
goes to session history; selected events are also queued for L2 review.

## Open points

Tracked in [`../open-questions.md`](../open-questions.md): on-prem default model and
reference hardware, Turkish accuracy, calibration under option reordering, data residency
for hosted options.
