# ADR-0011 — L0 detects, it does not decide

- **Status:** Superseded by ADR-0014
- **Date:** 2026-10-15

## Context

Early drafts let L0 return an early `BLOCK` (hard-block rules) and trigger redaction on its
own (`redact_on`). That spreads decisions over two layers; L0 has no policy context, so it
should not produce `BLOCK`, `REDACT` or any runtime decision; whether to redact is L1 policy.

## Decision

- L0 returns entities, summaries, hashes and the redacted view. It never returns an action.
- "Block when a card number and a private key appear together" or "redact card numbers" are
  **L1a rules** over entity facts (`entities.CREDIT_CARD.count`), with effects `BLOCK` or
  `ALLOW_WITH_MODIFICATIONS` + `redact: [types]`.
- L0's redacted view is always produced (it is what models see); whether the **caller**
  receives redacted text is an L1 decision.

## Alternatives considered

| Option | Why not |
|---|---|
| L0 hard blocks | Two places where blocks are configured; L0 would need policy context it does not have |

## Consequences

- One rule language and one place (L1a) for every deterministic outcome.
- An L1a `BLOCK` on entity facts still ends the evaluation before L1b, so latency is the same.

## Validation

Merge-rule tests; bundle validation rejects decision fields in the `l0` block.
