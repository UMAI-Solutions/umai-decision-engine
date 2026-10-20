# ADR-0036 — Masking does not end the evaluation: attack and agent-action checks run on the masked text

- **Status:** Accepted (product owner approved WP1 of the gateway plan, 2026-10-19). Takes effect
  when L1 is implemented (gateway plan WP4c); until then nothing runs after the L0 gate and the
  engine's behaviour is unchanged
- **Date:** 2026-10-19
- **Deciders:** product owner; engineering proposal
- **Amends:** ADR-0014 §1 and §5 (an L0 detection is terminal)
- **Builds on:** ADR-0034 (UMAI secure gateway), ADR-0005 (redacted text for L1)

## Context

ADR-0014 ends every evaluation at the L0 gate when L0 detects an entity, whatever the gate's
action. The reason was cost and certainty: a request that L0 blocks needs no decision model.

Two checks the gateway's first customer needs live after L0:

- **prompt-injection and jailbreak detection** (planned for L1, gateway plan WP4), and
- **agent-action rules** in L1a: which tools may be called, high-impact actions, destinations.

With ADR-0014 as written, both are skipped whenever L0 masks something or an exception allows it.
An attacker only has to add an e-mail address to a prompt, a document or a tool result to get past
injection screening; an agent's `send_email` call with a masked address skips the rule that forbids
sending to external recipients. For a guardrail sold as injection-resistant this is a bypass.

## Decision

1. **An L0 gate outcome of `BLOCK` or `STEP_UP_APPROVAL` stays terminal**, as in ADR-0014: no later
   layer runs, no decision-model call is made.
2. **Any other L0 gate outcome is a floor, not a final decision.** After `ALLOW_WITH_MODIFICATIONS`,
   `ALLOW_WITH_WARNINGS` or an exception's `ALLOW`, the evaluation continues to the layers after L0
   (L1a rules, the injection detector of WP4, L1b policies) on the **masked text**.
3. **The later layers can only make the decision stricter:** `final = max(L0 gate outcome, L1
   outcome)`. An L1a rule with `final: true` and `ALLOW` cannot relax the L0 outcome.
4. **Provenance:** `decided_by` names the layer whose outcome won; `decision.matched` lists the L0
   policy or exception and every L1 rule or policy that contributed. If a later layer escalates to
   `BLOCK`, `output_modifications` is dropped (nothing is sent).
5. **Entity facts survive masking.** Typed placeholders (`<TR_TCKN_1>`) count as entities of their
   type in the facts L1a sees, so rules over entity types work on masked text and tool arguments.
6. When L0 detects nothing, the evaluation continues to L1 as before.

## Alternatives considered

| Option | Why not |
|---|---|
| Keep ADR-0014; put the injection detector inside L0 as a pseudo-entity | Closes the injection gap only: L1a agent-action rules are still skipped whenever PII is present. Mixes whole-text classification into a span-based layer and its contract |
| Revert to ADR-0011 (every detection continues to L1) | Spends L1 on requests L0 already blocks; ADR-0014's reason still holds for `BLOCK` |
| Run attack checks before L0, on raw text | The injection model would see raw personal data (against ADR-0005) and L1a rules could not use L0's entity facts |

## Consequences

- `architecture/decision-merge-and-failure.md` rule 0, `architecture/overview.md` principle 3 and
  the L0 document describe the floor; ADR-0014's status line points here.
- Cost: requests with masked entities now reach L1. The injection detector is chosen for CPU
  latency (WP4b); L1b policies on such requests cost decision-model calls, so bundle authors keep
  L1b policies narrow.
- Golden tests in WP4c must cover: masked input with an injection (→ `BLOCK`), masked tool
  arguments against an agent-action rule, `BLOCK` from L0 still skipping L1, `ALLOW` exception plus
  L1 escalation.

## Validation

The `pi-tr` attack set includes cases that pair an injection with a maskable entity; their recall
must equal the recall of the same attacks without the entity. Latency of masked requests is reported
separately in the gateway benchmark.
