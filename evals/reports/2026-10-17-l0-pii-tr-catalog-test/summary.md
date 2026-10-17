# l0-pii-tr — catalog-test

- **Date:** 2026-10-17
- **Git commit:** 0f0b420 (uncommitted changes)
- **Dataset:** `evals/datasets/l0-pii-tr/v2.jsonl` (sha256 `6f59a1c3d0b3`), split `test`
- **Engine:** umai-decision-engine 0.1.0, presidio-analyzer 2.2.364, phonenumbers 9.0.41
- **Host:** Windows 11, Python 3.12.6, AMD64
- **Detector config:** engine defaults, every supported type; min_score 0.5
- **Cases:** 174 (33 without entities)
- **Match rule:** same type and overlap ≥ 50 % of the longer span

## Headline (types the detector supports)

| | Precision | Recall | F1 | False alarms on clean texts |
|---|---|---|---|---|
| **L0** | 1.000 | 0.967 | 0.983 | 0.0 % |
| naive-regex baseline | 0.706 | 0.318 | 0.438 | 18.2 % |

**Catalog coverage:** 26 of 26 labelled types implemented; recall over every labelled entity 0.967.

The naive regex baseline is illustrative (digit shapes, no checksum, no context). The F1
exit criterion is per type: precision ≥ 0.95, recall ≥ 0.90 (≥ 0.80 for PERSON and
ADDRESS) on the test split, false alarms ≤ 2 % (ADR-0017).

## By entity type

| Type | Support | L0 P | L0 R | L0 F1 | Baseline P | Baseline R | Baseline F1 | Note |
|---|---|---|---|---|---|---|---|---|
| ADDRESS | 4 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| API_KEY | 2 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| CONNECTION_STRING | 3 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| CREDIT_CARD | 9 | 1.000 | 1.000 | 1.000 | 1.000 | 0.889 | 0.941 |  |
| DE_TAX_ID | 5 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| EMAIL_ADDRESS | 7 | 1.000 | 1.000 | 1.000 | 0.700 | 1.000 | 0.824 |  |
| ES_NIE | 6 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| ES_NIF | 7 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| FR_NIR | 2 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| IP_ADDRESS | 9 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| IT_FISCAL_CODE | 3 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| JWT | 1 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| NL_BSN | 6 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| PASSWORD | 3 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| PERSON | 6 | 1.000 | 0.333 | 0.500 | — | 0.000 | — |  |
| PHONE_NUMBER | 8 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| PL_PESEL | 6 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| PRIVATE_KEY | 1 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| TR_IBAN | 7 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |  |
| TR_PHONE | 17 | 1.000 | 1.000 | 1.000 | 1.000 | 0.765 | 0.867 |  |
| TR_PLATE | 7 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| TR_TCKN | 18 | 1.000 | 0.944 | 0.971 | 0.433 | 0.722 | 0.542 |  |
| TR_VKN | 4 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| UK_NINO | 5 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| UNKNOWN_LINK | 2 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| US_SSN | 3 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |

## Recall by category (L0)

| Category | Support | Recall | FP |
|---|---|---|---|
| address_eu | 2 | 100.0 % | 0 |
| address_tr | 2 | 100.0 % | 0 |
| adv_fullwidth | 2 | 100.0 % | 0 |
| adv_mixed_separators | 1 | 100.0 % | 0 |
| adv_single_digit_spaced | 1 | 100.0 % | 0 |
| adv_words | 1 | 0.0 % | 0 |
| adversarial | 5 | 80.0 % | 0 |
| context_colloquial | 8 | 100.0 % | 0 |
| context_en | 6 | 100.0 % | 0 |
| context_formal | 10 | 100.0 % | 0 |
| fixed_line | 1 | 100.0 % | 0 |
| format_bare | 2 | 100.0 % | 0 |
| format_bare_grouped | 1 | 100.0 % | 0 |
| format_compact | 3 | 100.0 % | 0 |
| format_dashed | 4 | 100.0 % | 0 |
| format_double_zero | 1 | 100.0 % | 0 |
| format_grouped | 6 | 100.0 % | 0 |
| format_intl | 1 | 100.0 % | 0 |
| format_plain | 8 | 100.0 % | 0 |
| format_spaced | 1 | 100.0 % | 0 |
| format_trunk | 2 | 100.0 % | 0 |
| format_trunk_compact | 1 | 100.0 % | 0 |
| invalid_checksum_with_context | 1 | 100.0 % | 0 |
| ip_private | 3 | 100.0 % | 0 |
| ip_public | 1 | 100.0 % | 0 |
| multi | 15 | 100.0 % | 0 |
| no_context | 8 | 100.0 % | 0 |
| phone_de | 1 | 100.0 % | 0 |
| phone_es | 1 | 100.0 % | 0 |
| phone_fr | 3 | 100.0 % | 0 |
| phone_uk | 1 | 100.0 % | 0 |
| phone_us | 2 | 100.0 % | 0 |
| suffix | 4 | 100.0 % | 0 |

## L0 misses (case ids)

- **PERSON** (4): l0-0446, l0-0447, l0-0452, l0-0453
- **TR_TCKN** (1): l0-0057

## L0 false positives (case ids)

None.

## Detector latency (this host, sequential, one thread)

p50 0.61 ms · p95 1.91 ms · max 6.41 ms (not the latency suite; indicative only)
