# ADR-0017 — L0 coverage target: Lakera PII parity, extended to Türkiye and Europe

- **Status:** Accepted
- **Date:** 2026-10-17
- **Deciders:** product owner (UMAI)

## Context

The F1 exit criterion said L0 must "beat the `umai-engine` regex baseline". The product owner
ruled that rule set out as a reference ("garbage") and named Lakera Guard's managed PII
detectors as the bar to master ([`reference/lakera-guard.md`](../reference/lakera-guard.md)):
name, mailing address, phone, e-mail, IP address, credit card, IBAN, US SSN, unknown links.
Lakera is US-centric (addresses and phones are US-only); UMAI's customers are in Türkiye and
Europe. The first `l0-pii-tr` report (2026-10-17) showed L0 precision 0.993 / recall 0.939 on
the types it supports, with no NER, no IP, SSN, VKN, EU identifiers or unknown links.

## Decision

1. L0's target catalog is [`architecture/l0-entity-catalog.md`](../architecture/l0-entity-catalog.md):
   every Lakera managed PII detector, with Türkiye (TCKN, VKN, phone, IBAN, plate, Turkish
   addresses and names) and Europe (all-country phones, IBAN, checksum-bearing national IDs:
   UK NINO, DE tax ID, FR NIR, ES DNI/NIE, IT codice fiscale, NL BSN, PL PESEL) added.
2. The `l0-pii-tr` dataset labels every catalog type (v2), so coverage is measured, not
   claimed. Labels state the truth, including for types L0 cannot detect yet.
3. **F1 exit criterion** (replaces "beats the regex baseline"): on the `l0-pii-tr` test split,
   for every catalog type, precision ≥ 0.95 and recall ≥ 0.90 (deterministic types) or
   ≥ 0.80 (NER types: `PERSON`, `ADDRESS`); false alarms on clean texts ≤ 2 %; leak suite 0;
   `l0-latency` p95 within budget. Targets are revisited with the first full report.
4. Behaviour follows Lakera where Lakera documents it (e.g. single names and "John Doe" are not
   names; loopback / link-local / multicast / well-known DNS IPs are not flagged; SSNs with
   `000` / `666` areas are not SSNs), unless a Turkish or European case needs otherwise.

## Alternatives considered

| Option | Why not |
|---|---|
| Beat `umai-engine`'s regex rules (previous criterion) | Rejected by the product owner as a meaningless bar |
| Lakera parity only (US-centric) | Misses UMAI's markets: Turkish and European identifiers, addresses, phones |
| Every Presidio predefined recognizer | Presidio's coverage is broad but uneven (its Turkish recognizers were rejected in ADR-0007); we pick types by market and measure each |
| A fixed list of all EU national IDs at once | 27+ formats with weak validation for some; start with checksum-bearing IDs of the largest markets, add on evidence |

## Consequences

- NER becomes required for parity (`PERSON`, `ADDRESS`): the NER model choice (open question
  Q8) needs its own ADR with eval and latency numbers, on-prem and multilingual (TR / EN / EU).
- `UNKNOWN_LINK` needs a domain popularity list shipped with the engine (on-prem) plus
  per-guardrail allowed domains: a list source and its license must be chosen.
- `l0-pii-tr` grows to dataset v2 and the roadmap's F1 exit criterion changes accordingly.

## Validation

The F1 criterion above, reported per type in `evals/reports/*-l0-pii-tr-*`. Revisit the
catalog when a customer market needs another country, or when a type's precision target cannot
be met without context words (then it becomes "context required", like VKN and BSN).
