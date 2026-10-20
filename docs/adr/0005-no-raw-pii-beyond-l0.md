# ADR-0005 — Raw PII never leaves L0

- **Status:** Proposed; amended by ADR-0035 (the gateway forwarder is part of the raw-content zone)
- **Date:** 2026-10-12

## Context

L1b and L2 may call external providers (TypeSafe, OpenAI, a hosted gpt-oss-safeguard).
Customers are mostly in Turkey and subject to KVKK; cross-border transfer of personal data
needs a legal basis. Logs, metrics, traces and session stores are also exposure points.

## Decision

Only L0 handles raw content. Everything downstream receives:

- redacted text with placeholders (`<TR_TCKN_1>`),
- entity types, counts and spans,
- salted HMAC hashes of entity values (inside the engine only; never sent to providers).

Logs, metrics, traces, errors, session history, eval reports and test snapshots follow the
same rule. Module boundaries enforce it: no raw-content `str` in L1/L2/session/telemetry
signatures (`docs/architecture/code-layout.md`).

## Alternatives considered

| Option | Why not |
|---|---|
| Send raw text to providers under a DPA | Legal exposure, weaker product story, still leaks to logs |
| Reversible encryption of values for providers | Providers do not need values to judge intent; adds key management |

## Consequences

- Models judge redacted text. Some semantic signal is lost (e.g. a name's plausibility);
  evals must show the loss is acceptable.
- Product claim (once validated): "our model layers never see your customers' personal data".
- Leak tests are mandatory for L0, L2 rationale, logs and responses.

## Validation

Leak tests in CI; eval comparison of decision accuracy on redacted vs raw synthetic data.
