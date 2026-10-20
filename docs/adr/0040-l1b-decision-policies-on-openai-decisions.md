# ADR-0040 — L1b decision policies, first on the OpenAI Decisions API, shadow before enforce

- **Status:** Accepted for the mechanism (product owner, 2026-10-20: "start with the rewritten
  prompt … begin with steps 1 and 2"). The per-policy actions below are proposals: every policy
  stays in shadow until the product owner decides its action and evals have set its threshold.
- **Date:** 2026-10-20
- **Deciders:** product owner; engineering proposal
- **Builds on:** ADR-0004 (provider abstraction), ADR-0005 (no raw PII beyond L0), ADR-0014 and
  ADR-0036 (L0 gate; checks continue after masking), ADR-0015 (golden tests)

## Context

- **The colleagues' work.** The `OpenAI_Decisions` repository (commit `985138d`) holds 10
  policies. Each is one `choice` question with a long instruction file.
  - The runner calls the OpenAI Decisions API once per policy, in parallel, with the raw user
    text.
  - The result is a label per policy, not an action.
  - Tests: 44 cases cover the four MIT policies. Their own script says the labels are "draft
    targets, not validated ground truth". Six policies, prompt injection among them, have none.
- **The engine before this change.** L1 was not implemented. A bundle with L1b policies was
  answered with its failure policy and `L1_NOT_IMPLEMENTED`. ADR-0036 (L1 continues after L0
  masking) waited for L1.
- **The Decisions API** (re-verified 2026-10-20, [reference](../reference/openai-decisions-api.md)):
  - **Product:** public beta, `gpt-6-luna` only.
  - **Questions:** `predicate` / `choice` / `score`; up to 200 questions per call (the SDK
    limit; the HTTP reference says 400, unverified).
  - **Input:** the SDK types allow user messages only.
  - **Answers and refusals:** answers come with per-option probabilities. Any question may come
    back as a `refusal`.
  - **Price:** $0.10 per million input tokens.
  - **Latency:** third-party telemetry shows p50 216 ms and p95 754 ms.
  - **Data handling:**
    - EU processing (`eu.api.openai.com`) needs OpenAI's approval and zero data retention (ZDR);
      it costs 10 % more.
    - No training on customer data.
    - Abuse-monitoring logs are kept 30 days.
- **The goal set for this work.** Prompt-injection recall ≥ 95 % at ≤ 2 % false positives on a
  Turkish holdout, and L1b p95 ≤ 600 ms.

## Decision

1. **L1b runs a guardrail's decision policies through a provider adapter.**
   - The first adapter is the OpenAI Decisions API (`openai_decisions`). It uses our async httpx
     client: explicit timeout, no retries, no redirects, no request or response bodies in logs.
   - Providers are named in settings: `UMAI_DE_DECISION_PROVIDERS` (adapter, base URL, model)
     and `UMAI_DE_DECISION_PROVIDER_KEYS` (keys). A bundle names one in `l1b.provider`.
2. **L1b judges only what L0 scanned, redacted.**
   - **Input:** L0's parts with every detected entity replaced by its placeholder; the latest
     parts first, up to `l1b.max_input_chars`; a part that is too long alone is clipped in the
     middle.
   - **Facts:** phase, destination class, role and department, entity types and counts, the
     tool's name. No user identifier.
   - **Wire shape:** one user message with two parts. The first is a labelled UMAI context; the
     second is the content between markers, declared untrusted.
3. **One call per evaluation by default.** `l1b.batch: all` sends every applicable question in
   one call. `per_policy` sends one call per policy, in parallel. The `l1-latency` eval decides
   the default with live numbers (pending a key).
4. **Shadow before enforce.** Each policy has `enforcement: enforce | shadow`.
   - Shadow outcomes are logged (policy, would-be action, probability; never content) and never
     change a decision.
   - When only shadow policies apply, the call runs after the decision is returned, so it adds
     no latency.
   - All converted policies start in shadow.
5. **Operating points live in the policy.** A `decide` rule can require a probability:
   `{q: {value: v | [v…], p_gte: x}}` for a choice (the summed probability of the values) and
   `{q: {p_gte: x}}` for a condition. Thresholds come from evals, not defaults; the converted
   policies carry `0.5` as a placeholder.
6. **ADR-0036 is now in effect.**
   - An L0 `BLOCK` / `STEP_UP_APPROVAL` is final, and L1b does not run.
   - Any other gate outcome is a floor: L1b judges the masked text, and
     `final = max(floor, L1b)`. A masking floor keeps its masking unless L1b refuses outright.
7. **Failures.**
   - A provider error, timeout or missing provider tries `l1b.fallback_provider` if the budget
     allows. Otherwise the bundle's `on_evaluation_failure` applies to the L1b part, never
     lowering the floor, and `degraded` is set with error `L1B_<KIND>`.
   - A refusal is not an error: the question has no answer, the policy falls through to its
     default, and the event is marked for review.
   - A failure in a shadow call is logged only.
   - L1a rules are still not implemented (`L1_NOT_IMPLEMENTED`).
8. **The colleagues' policies, converted.** `dev/convert_openai_decisions.py` turns them into
   nine policies in `gr-demo-data-protection` version 5 (re-run it when the source changes).
   - **Kept verbatim:** instructions and option descriptions.
   - **Normalized:** option values become snake_case, in the instructions too.
   - **Added by UMAI:** ids, phases, `decide`, `review_when`, provenance.
   - **Not converted:** `mit_privacy_security`. Its 2.1 is covered by `data-exfiltration` and
     its 2.2 by `prompt-attack`.

   Proposed actions (shadow until decided):

   | Policy | From | Phases | Proposed action |
   |---|---|---|---|
   | `prompt-attack` | prompt_injection | PRE_LLM, TOOL_OUTPUT | `injection_detected` ≥ t → BLOCK |
   | `data-exfiltration` | data_exfiltration (+ MIT 2.1) | PRE_LLM, TOOL_INPUT | exfiltration ≥ t → BLOCK; sensitive data request → warn + review |
   | `malicious-use` | malicious_actors_misuse (MIT 4) | PRE_LLM | cyber/weapons/mass harm, fraud, influence operations ≥ t → BLOCK; sensitive topic → warn + review |
   | `discrimination-toxicity` | MIT 1 | PRE_LLM | toxic content ≥ t → BLOCK; unfair discrimination → warn + review; unequal performance → tag |
   | `misinformation` | MIT 3 | PRE_LLM | fabrication, ecosystem pollution → warn + review |
   | `religion` | politics_religion | PRE_LLM | sectarian hostility, religious insult ≥ t → BLOCK; proselytizing → warn; belief / practice / state → tag |
   | `ai-reliance` | human_computer_interaction (MIT 5) | PRE_LLM | unsafe reliance or delegation → warn + review; high stakes → tag |
   | `socioeconomic` | socioeconomic_environmental (MIT 6) | PRE_LLM | governance evasion, worker exploitation → warn + review; others → tag |
   | `ai-system-safety` | MIT 7 | PRE_LLM | tags only (describing a risk is not a harmful request) |

9. **Golden cases stub the model.** `model_answers` per case (policy-tests 0.3) feeds a fake
   provider, so bundles with L1b policies stay deterministic in CI. A question without a stub
   comes back refused.

## Alternatives considered

| Option | Why not |
|---|---|
| Ship the colleagues' runner as it is | It sends raw text (ADR-0005) and produces labels, not decisions. It has no failure policy, thresholds or tests for most policies |
| Enforce the converted policies now | Their false-positive rate on Turkish traffic is unknown, and a block there stops real work. Shadow first costs nothing in latency |
| One call per policy as the default | It sends the input nine times, and each evaluation waits for the slowest of nine calls. Kept as an option; the live comparison decides |
| The OpenAI SDK at runtime | It would be a second HTTP stack with its own retries and timeouts. httpx gives us explicit timeouts and no retries, as every other external call has. The SDK stays a dev dependency for tests |
| Context in a `developer` message | The SDK types allow user messages only (unverified otherwise); not until a live call confirms |
| Argmax only, no thresholds | The targets are an operating point (recall at a false-positive rate). That needs probabilities compared with a threshold chosen on data |

## Consequences

- **Latency:** an enforcing L1b policy adds a provider round trip to every evaluation it
  applies to, including every gateway call (p95 ≈ 750 ms by third-party telemetry, above the
  600 ms budget). This must be measured from our region before anything enforces. Shadow-only
  adds nothing.
- **Cost:** about 12 k input tokens per evaluation with the nine policies, about $0.0012, less
  with prompt-cache hits.
- **Data:** redacted content leaves to OpenAI: the US by default, the EU only with approval.
  This is a KVKK / GDPR question for each customer (Q51, Q59). On-prem customers need another
  provider (ADR-0004).
- **What must be built next:**
  - step 3: a labelled Turkish eval set, thresholds per policy, calibration, the
    option-order and injected-judge tests;
  - step 4: a shadow deployment with a provider key on umai-beta;
  - the review queue (WP6) for the review flag, now only logged;
  - L1a rules;
  - L1b on streamed answers (`POST_LLM`; none of the converted policies uses it).
- **Coverage:** the colleagues' instructions are used verbatim. Improving them is now a bundle
  change with golden cases, not a code change.

## Validation

- **Tests:**
  - unit: `decide` rules, adapter shape, every error kind;
  - pipeline: floor, failures, fallback, timeout, shadow, batching, MONITOR, TOOL_OUTPUT;
  - gateway: injection refused before the provider, indirect injection in a tool result,
    placeholders only, shadow never refuses;
  - golden: version 5, shadow never decides.
- **Live:** `python -m evals.run --suite l1-latency` (a dry run with a fake provider passes). It
  needs `UMAI_EVAL_OPENAI_API_KEY`, run once locally and once from Azure West Europe.
- **Revisit when:**
  - the live p95 is above 600 ms in both modes (another provider, an on-prem decision model, or
    fewer questions);
  - EU processing is not approved for a customer that requires it;
  - step 3 shows a policy cannot reach its target.
