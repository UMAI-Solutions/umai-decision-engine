# l0-pii-tr — catalog-dev

- **Date:** 2026-10-17
- **Git commit:** 0f0b420 (uncommitted changes)
- **Dataset:** `evals/datasets/l0-pii-tr/v2.jsonl` (sha256 `6f59a1c3d0b3`), split `dev`
- **Engine:** umai-decision-engine 0.1.0, presidio-analyzer 2.2.364, phonenumbers 9.0.41
- **Host:** Windows 11, Python 3.12.6, AMD64
- **Detector config:** engine defaults, every supported type; min_score 0.5
- **Cases:** 480 (73 without entities)
- **Match rule:** same type and overlap ≥ 50 % of the longer span

## Headline (types the detector supports)

| | Precision | Recall | F1 | False alarms on clean texts |
|---|---|---|---|---|
| **L0** | 1.000 | 0.986 | 0.993 | 0.0 % |
| naive-regex baseline | 0.689 | 0.268 | 0.386 | 26.0 % |

**Catalog coverage:** 27 of 27 labelled types implemented; recall over every labelled entity 0.986.

The naive regex baseline is illustrative (digit shapes, no checksum, no context). The F1
exit criterion is per type: precision ≥ 0.95, recall ≥ 0.90 (≥ 0.80 for PERSON and
ADDRESS) on the test split, false alarms ≤ 2 % (ADR-0017).

## By entity type

| Type | Support | L0 P | L0 R | L0 F1 | Baseline P | Baseline R | Baseline F1 | Note |
|---|---|---|---|---|---|---|---|---|
| ADDRESS | 19 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| API_KEY | 7 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| CONNECTION_STRING | 6 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| CREDIT_CARD | 20 | 1.000 | 1.000 | 1.000 | 0.810 | 0.850 | 0.829 |  |
| DE_TAX_ID | 13 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| EMAIL_ADDRESS | 20 | 1.000 | 1.000 | 1.000 | 0.739 | 0.850 | 0.791 |  |
| ES_NIE | 12 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| ES_NIF | 12 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| FR_NIR | 17 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| IBAN_CODE | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |  |
| IP_ADDRESS | 13 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| IT_FISCAL_CODE | 15 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| JWT | 2 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| NL_BSN | 12 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| PASSWORD | 17 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| PERSON | 20 | 1.000 | 0.800 | 0.889 | — | 0.000 | — |  |
| PHONE_NUMBER | 14 | 1.000 | 0.929 | 0.963 | — | 0.000 | — |  |
| PL_PESEL | 12 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| PRIVATE_KEY | 5 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| TR_IBAN | 18 | 1.000 | 1.000 | 1.000 | 1.000 | 0.889 | 0.941 |  |
| TR_PHONE | 40 | 1.000 | 1.000 | 1.000 | 0.868 | 0.825 | 0.846 |  |
| TR_PLATE | 13 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| TR_TCKN | 52 | 1.000 | 0.981 | 0.990 | 0.448 | 0.577 | 0.504 |  |
| TR_VKN | 16 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| UK_NINO | 14 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| UNKNOWN_LINK | 18 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |
| US_SSN | 20 | 1.000 | 1.000 | 1.000 | — | 0.000 | — |  |

## Recall by category (L0)

| Category | Support | Recall | FP |
|---|---|---|---|
| address_eu | 8 | 100.0 % | 0 |
| address_tr | 6 | 100.0 % | 0 |
| address_us | 5 | 100.0 % | 0 |
| adv_at_word | 1 | 100.0 % | 0 |
| adv_dashed | 1 | 100.0 % | 0 |
| adv_dots | 1 | 100.0 % | 0 |
| adv_letter_o_for_zero | 1 | 100.0 % | 0 |
| adv_lowercase | 1 | 100.0 % | 0 |
| adv_newline_split | 1 | 0.0 % | 0 |
| adv_obfuscated | 2 | 100.0 % | 0 |
| adv_single_digit_spaced | 2 | 100.0 % | 0 |
| adv_zero_width | 2 | 100.0 % | 0 |
| adversarial | 12 | 91.7 % | 0 |
| amex | 1 | 100.0 % | 0 |
| card_length | 2 | 100.0 % | 0 |
| code_switch | 1 | 100.0 % | 0 |
| context_colloquial | 21 | 100.0 % | 0 |
| context_en | 16 | 100.0 % | 0 |
| context_formal | 34 | 100.0 % | 0 |
| fixed_line | 3 | 100.0 % | 0 |
| foreign | 2 | 100.0 % | 0 |
| format_bare | 1 | 100.0 % | 0 |
| format_bare_grouped | 2 | 100.0 % | 0 |
| format_cc_no_plus | 3 | 100.0 % | 0 |
| format_compact | 10 | 100.0 % | 0 |
| format_dashed | 12 | 100.0 % | 0 |
| format_dotted | 6 | 100.0 % | 0 |
| format_double_zero | 2 | 100.0 % | 0 |
| format_grouped | 13 | 100.0 % | 0 |
| format_intl | 2 | 100.0 % | 0 |
| format_intl_compact | 3 | 100.0 % | 0 |
| format_paren | 3 | 100.0 % | 0 |
| format_plain | 12 | 100.0 % | 0 |
| format_spaced | 2 | 100.0 % | 0 |
| format_trunk | 1 | 100.0 % | 0 |
| format_trunk_compact | 2 | 100.0 % | 0 |
| invalid_checksum_with_context | 2 | 100.0 % | 0 |
| ip_private | 1 | 100.0 % | 0 |
| ip_public | 5 | 100.0 % | 0 |
| link_unknown | 5 | 100.0 % | 0 |
| long | 2 | 100.0 % | 0 |
| multi | 33 | 100.0 % | 0 |
| no_context | 13 | 100.0 % | 0 |
| phone_de | 1 | 100.0 % | 0 |
| phone_es | 1 | 100.0 % | 0 |
| phone_fr | 1 | 100.0 % | 0 |
| phone_it | 2 | 100.0 % | 0 |
| phone_nl | 2 | 100.0 % | 0 |
| phone_uk | 3 | 100.0 % | 0 |
| phone_us | 4 | 75.0 % | 0 |
| suffix | 8 | 100.0 % | 0 |
| uppercase | 3 | 100.0 % | 0 |

## L0 misses (case ids)

- **PERSON** (4): l0-0448, l0-0449, l0-0454, l0-0455
- **PHONE_NUMBER** (1): l0-0258
- **TR_TCKN** (1): l0-0055

## L0 false positives (case ids)

None.

## Detector latency (this host, sequential, one thread)

p50 0.72 ms · p95 1.68 ms · max 16.06 ms (not the latency suite; indicative only)
