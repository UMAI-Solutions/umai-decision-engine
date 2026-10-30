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

## Amendment 2026-10-26: the provider connection at startup

**Context.** On umai-beta, right after `umai.sh up`, the first five L1b calls (five concurrent
shadow calls, `l1b.timeout_ms` 600) all timed out; minutes later the same requests took
260–384 ms. smarttech's guardrail v3 now enforces two policies with `fail_closed` and a 1 400 ms
timeout, so a slow call BLOCKs a legitimate request (`L1B_TIMEOUT`). Measured on the VM from
one-off containers, with httpcore's trace events and OpenAI's `openai-processing-ms` header:

- **The client side of a new process.** The first call opened a connection: client and network
  p50 102 ms against 54–56 ms for later calls (suite `l1-cold-start`); after the change
  54 ms, with no new connection. Without the app's imports
  done, the first request also paid 37–75 ms of imports and pool setup; DNS took 1–14 ms, TCP
  4–21 ms, TLS 6–20 ms. DNS, the event loop and Presidio's warm-up (it ends before the app takes
  traffic) are not the cause, and a cancelled request closes only its own connection.
- **Keep-alive.** httpx closes idle connections after 5 s, so on sparse traffic almost every call
  opened a new one (after 8 s idle: 6/6 new, p50 74 ms; after the change 0/6, 49 ms). OpenAI's edge kept idle
  connections open for the whole 25 minutes we watched.
- **The provider side, the larger part.** After 4–5 minutes without calls, OpenAI's own
  processing time is higher for the first one or two calls, on a connection that is already open:
  375, 771, 613, 416, 210→748→2 064 ms for the first calls of five rounds, against about 220 ms
  later. Prompt caching never hit (`cached_tokens` 0 on every call, identical bodies included).
  Two calls sent together after idle were both slow (388 and 452 ms).

**Decision.**

1. **Warm-up.** Before the engine or the gateway takes traffic, `start_runtime` warms every
   provider. The OpenAI adapter sends one decision on a fixed synthetic sentence with one
   question (about 170 input tokens, never caller content). It opens the connection, does the
   first-request imports, checks the key and the endpoint, and takes the provider's slow first
   answer itself: after a decision warm-up the next real call's processing was 339, 427 and
   214 ms in three idle rounds, against 613 and 416 ms after a `GET /models/{model}` warm-up. It
   runs within `UMAI_DE_DECISION_WARM_UP_TIMEOUT_MS` (2 000; 0 turns it off). The outcome is
   logged (`l1b.warm_up provider=… outcome=ok|auth|timeout|unavailable|http_<n>`); a failure
   changes nothing else: the first decision opens its own connection, and a provider that is
   really down meets the bundle's failure policy there.
2. **Keep-alive.** Idle provider connections are kept `UMAI_DE_DECISION_KEEPALIVE_S` (120 s),
   well below what the provider's edge keeps.
3. **One deadline per call.** `l1b.timeout_ms` bounds the whole call (connect, send and wait
   together), not each httpx phase: before, a new connection could take the timeout once to
   connect and once more to answer.

**Alternatives.**

| Option | Why not (now) |
|---|---|
| `GET /models/{model}` as the warm-up (no tokens) | Opens the connection but leaves the provider's slow first answer to a real call (613 and 416 ms); itself took 0.55–1.2 s |
| A decision with the bundle's own policies at startup | About 11 k tokens per start and per policy set, and needs the active bundles at startup; no better than the one-question call in our rounds |
| A periodic keep-warm call while idle | The provider is slow again after every few idle minutes, which a startup warm-up cannot cover. A synthetic call every few minutes per process would; it spends tokens on a timer: a product decision, not part of this fix |
| A hedged second request | Two calls sent together after idle were both slow, so a hedge would not have helped there; it still may for the random tail (one 2.5 s call on a warm connection). A pipeline decision (adapters stay retry-free) |
| HTTP/2 (one connection for concurrent calls) | A new dependency (`h2`); without it concurrent first calls open their own connections, 30–50 ms each once the imports are done. Not needed at the measured concurrency (rule 6) |
| httpx defaults | 5 s keep-alive, and a timeout per phase instead of per call |

**Consequences.** Each process start sends one synthetic decision per provider (about 170 input
tokens, $0.00002) and waits for it, at most the warm-up timeout. The first calls of a new process
cost what later calls cost on the client side. The provider's own slowness after idle and its
random tail remain and are the larger risk for enforcing policies with `fail_closed`: choosing
between keep-warm calls, a longer `l1b.timeout_ms`, a fallback provider and hedging is open.

**Validation.** Tests: the warm-up request (synthetic body, the key, an outcome per failure), the
deadline over the whole call, the first decisions reusing the connection opened at warm-up (a
local server counting TCP connections), keep-alive from settings, the runtime warming before
traffic. Live: [`l1-cold-start` on umai-beta](../../evals/reports/2026-10-26-l1-cold-start-beta-westeurope/summary.md)
(six fresh processes per variant, before and after).

## Amendment 2026-10-30: shadow policies in their own call

**Context.** smarttech's guardrail v3 on umai-beta enforces two of its nine policies
(`malicious-use`, `prompt-attack`, p ≥ 0.5) with `l1b.timeout_ms` 1 400, `batch: all` and
`fail_closed`; the other seven are in shadow; the evaluation budget is 1 500 ms. Until now shadow
policies rode on the enforcing call whenever a policy enforced (decision 4 sent them to the
background only when none did), so every evaluation's request path asked all nine questions.
Measured from the VM on 2026-10-30, before this change (200 calls, 40 synthetic Turkish prompts
in five classes, warm connection):

| Call shape | p50 ms | p95 ms | max ms | Input tokens |
|---|---|---|---|---|
| All nine policies in one call | 409 | 635 | 1 045 | ≈ 11 000 |
| Only the two enforcing policies | 426 | 607 | 632 | ≈ 2 600 |
| One call per policy, in parallel | 613 | — | — | — |

The median is the same; the tail is not, and the tail is what meets the timeout. Earlier the same
day two calls on beta took longer than the 1 400 ms `l1b.timeout_ms` (`L1B_TIMEOUT`), so
`fail_closed` blocked them.

**Decision.**

1. **The request path asks only the enforcing policies.** Shadow policies always run in their
   own call, in the background, started after the enforcing call has returned so the two never
   compete; with their own `l1b.timeout_ms`, and also when the enforcing call failed or had no
   budget left. `per_policy` batching applies to each group. Decision 4 becomes: shadow policies
   never add latency, whether or not a policy enforces.
2. **Records.** Each call writes its own L1b outcome records: the enforcing ones with the
   decision, the shadow ones when their call finishes (ADR-0041). Usage stays one record per
   meter and evaluation (`(tenant_id, event_id, meter)` is unique, so a second `l1b_calls` row
   would be dropped as a duplicate): the evaluation's last L1b call, the shadow call when there
   is one, writes `l1b_calls` and the token meters for both calls. The decision event's `l1b`
   summary describes the enforcing call (error kind, latency). No contract shape changes.
3. **Held evaluations.** A gateway stream's window re-check holds its records for the caller to
   commit or drop (ADR-0041). Its hold now lasts until its shadow call has reported: before the
   commit the shadow records join the held ones, after it they are written directly, and without
   a commit they are dropped with the rest. Before, a late shadow call's records were dropped
   even for the re-check that stopped the stream.

**Alternatives.**

| Option | Why not |
|---|---|
| Keep one call for both | The request path pays for nine questions to decide with two: four times the input tokens and a longer tail at the same median |
| Start the shadow call together with the enforcing one | Two concurrent calls compete for the connection pool and the provider while the decision waits; the shadow call has no deadline that requires it |
| One call per policy | Slower: p50 613 ms, each evaluation waits for the slowest call |
| Separate usage meters for shadow calls (`l1b_shadow_calls`, …) | A contract change (usage 0.3) for every consumer; worth doing when the console shows shadow cost on its own, not needed to keep the totals right |
| One usage record per call | Breaks the ledger's uniqueness: the second call's row is dropped as a duplicate |

**Consequences.**

- An evaluation with both kinds of policy makes two provider calls (`l1b_calls` 2 where it was
  1) and sends the excerpt and context twice; requests per minute at the provider double for
  those evaluations, input tokens grow about 1 % (measured below).
- A shadow call can still be running when the next evaluation's enforcing call starts; under
  load the provider sees more concurrent requests than before.
- An evaluation's L1b usage is written when its shadow call finishes. A process that stops
  before then loses that call's records, now including the evaluation's L1b usage; a graceful
  shutdown waits for shadow calls before it flushes the records (`runtime`).

**Validation.** Tests: the request path asks only the enforcing questions and the shadow call
follows (also after a failed enforcing call and with `per_policy`); outcome records for both
calls, the enforcing ones at the decision; one usage record per meter with both calls' counts
and tokens; with no budget left only the shadow call is counted; a held evaluation's late shadow
records are committed (shadow before and after the commit) or dropped, and its hold released.
Live: [`l1-latency --modes all,split` on umai-beta](../../evals/reports/2026-10-30-l1-latency-split-beta-westeurope/summary.md)
(from the VM, 2026-10-30, 200 `pi-tr` cases, smarttech v3, both shapes per case in alternating
order, warm connection, client timeout 10 s to see the whole distribution). On the 178 cases
where both kinds of policy apply (the other 22 are `TOOL_OUTPUT`, where only `prompt-attack`
applies and both shapes send the same call):

| Call | p50 ms | p95 ms | p99 ms | max ms | No answer within 10 s | Input tokens |
|---|---|---|---|---|---|---|
| Before: all nine policies in one call (request path) | 425 | 656 | 748 | 987 | 4 | 11 092 |
| After: the two enforcing policies (request path) | 421 | 622 | 663 | 801 | 0 | 2 669 |
| After: the seven shadow policies (background) | 506 | 683 | — | 1 093 | 0 | 8 544 |

- **The stalls.** The four nine-question calls without an answer within 10 s came within about a
  minute (cases 61–91), each right after the same prompt's enforcing and shadow calls had
  answered in 281–503 and 554–669 ms. Asking those four prompts again 20 times in each order
  (alone, and after an enforcing + shadow pair) gave no stall, at most 671 ms, so it was a
  transient provider episode, not the prompts or the order. With 1 400 ms and `fail_closed`
  each would have been a BLOCK. In this run the episode met only the nine-question call: four
  events, a hint rather than proof that the smaller call is less exposed.
- **Answers.** The same winning answer per question in both shapes: 1 588/1 588, enforcing
  questions 370/370. Asking fewer questions in a call did not change the enforcing answers.
- **Overlap.** 85 of 200 request-path calls started while the previous case's shadow call was
  still running: p50 441 ms against 416 ms without, p95 614 against 622 ms.
- **Tokens.** Both calls together 11 213 input tokens against 11 092 for the single call (+1 %).
