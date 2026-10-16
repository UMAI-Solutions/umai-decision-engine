# ADR-0007 — Microsoft Presidio for L0

- **Status:** Accepted
- **Date:** 2026-10-12
- **Deciders:** product owner (UMAI)

## Context

L0 needs scored, validated, span-level entity detection with redaction, extensible with
Turkish identifiers and tenant-specific patterns, runnable on-prem. Its value over plain
regex rules has to be shown by measurement (L0 eval).

## Decision

Use `presidio-analyzer` (pinned `2.2.364`; the project moved to
`data-privacy-stack/presidio`, still MIT) as the analysis framework for L0, with:

- **our own** Turkish recognizers and checksum validators (TCKN, VKN, TR IBAN, TR phone,
  plate) instead of the upstream Turkish recognizers, which have boundary, false-positive
  and context defects (`docs/reference/presidio.md`);
- the `no_op` NLP engine and our own Turkish-aware context scoring;
- our own HMAC hashing and placeholder redaction instead of the anonymizer's `hash`
  (random salt) and `encrypt` (AES-CBC without MAC) operators.

NER is disabled until a Turkish NER option is evaluated (`docs/reference/turkish-nlp.md`).

## Alternatives considered

| Option | Why not |
|---|---|
| Keep regex rules | No validation, scores or context; the precision problem we are solving |
| Cloud PII services (Azure AI Language, AWS Comprehend) | Raw PII leaves the engine; no on-prem |
| Custom NER model only | Pattern + checksum handles the highest-value Turkish IDs better and faster |

## Consequences

- Presidio runs CPU-bound and synchronously: thread-pool execution, startup warm-up.
- Turkish NER is an open question (see `docs/open-questions.md`).

## Validation

L0 eval: per-entity precision/recall vs `umai-engine` regex baseline on the synthetic
Turkish dataset; L0 p95 latency within budget.
