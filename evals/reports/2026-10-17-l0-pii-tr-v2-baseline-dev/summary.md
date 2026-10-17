# l0-pii-tr — v2-baseline-dev

- **Date:** 2026-10-17
- **Git commit:** 0f0b420 (uncommitted changes)
- **Dataset:** `evals/datasets/l0-pii-tr/v2.jsonl` (sha256 `a2aeaa89943b`), split `dev`
- **Engine:** umai-decision-engine 0.1.0, presidio-analyzer 2.2.364, phonenumbers 9.0.41
- **Host:** Windows 11, Python 3.12.6, AMD64
- **Detector config:** engine defaults, every supported type; min_score 0.5
- **Cases:** 272 (65 without entities)
- **Match rule:** same type and overlap ≥ 50 % of the longer span

## Headline (types the detector supports)

| | Precision | Recall | F1 | False alarms on clean texts |
|---|---|---|---|---|
| **L0** | 0.979 | 0.914 | 0.946 | 0.0 % |
| naive-regex baseline | 0.816 | 0.757 | 0.785 | 29.2 % |

**Catalog coverage:** 6 of 22 labelled types implemented; recall over every labelled entity 0.607.

The naive regex baseline is illustrative (digit shapes, no checksum, no context). The F1
exit criterion is per type: precision ≥ 0.95, recall ≥ 0.90 (≥ 0.80 for PERSON and
ADDRESS) on the test split, false alarms ≤ 2 % (ADR-0017).

## By entity type

| Type | Support | L0 P | L0 R | L0 F1 | Baseline P | Baseline R | Baseline F1 | Note |
|---|---|---|---|---|---|---|---|---|
| ADDRESS | 9 | — | 0.000 | — | — | 0.000 | — | not implemented in L0 |
| CREDIT_CARD | 20 | 1.000 | 0.900 | 0.947 | 0.810 | 0.850 | 0.829 |  |
| DE_TAX_ID | 2 | — | 0.000 | — | — | 0.000 | — | not implemented in L0 |
| EMAIL_ADDRESS | 20 | 1.000 | 0.850 | 0.919 | 1.000 | 0.850 | 0.919 |  |
| ES_NIE | 3 | — | 0.000 | — | — | 0.000 | — | not implemented in L0 |
| ES_NIF | 3 | — | 0.000 | — | — | 0.000 | — | not implemented in L0 |
| FR_NIR | 3 | — | 0.000 | — | — | 0.000 | — | not implemented in L0 |
| IBAN_CODE | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |  |
| IP_ADDRESS | 6 | — | 0.000 | — | — | 0.000 | — | not implemented in L0 |
| IT_FISCAL_CODE | 2 | — | 0.000 | — | — | 0.000 | — | not implemented in L0 |
| NL_BSN | 1 | — | 0.000 | — | — | 0.000 | — | not implemented in L0 |
| PERSON | 11 | — | 0.000 | — | — | 0.000 | — | not implemented in L0 |
| PHONE_NUMBER | 14 | — | 0.000 | — | — | 0.000 | — | not implemented in L0 |
| PL_PESEL | 2 | — | 0.000 | — | — | 0.000 | — | not implemented in L0 |
| TR_IBAN | 18 | 1.000 | 0.944 | 0.971 | 1.000 | 0.889 | 0.941 |  |
| TR_PHONE | 40 | 0.929 | 0.975 | 0.951 | 0.917 | 0.825 | 0.868 |  |
| TR_PLATE | 2 | — | 0.000 | — | — | 0.000 | — | not implemented in L0 |
| TR_TCKN | 52 | 1.000 | 0.885 | 0.939 | 0.612 | 0.577 | 0.594 |  |
| TR_VKN | 5 | — | 0.000 | — | — | 0.000 | — | not implemented in L0 |
| UK_NINO | 2 | — | 0.000 | — | — | 0.000 | — | not implemented in L0 |
| UNKNOWN_LINK | 5 | — | 0.000 | — | — | 0.000 | — | not implemented in L0 |
| US_SSN | 7 | — | 0.000 | — | — | 0.000 | — | not implemented in L0 |

## Recall by category (L0)

| Category | Support | Recall | FP |
|---|---|---|---|
| address_eu | 4 | 0.0 % | 0 |
| address_tr | 2 | 0.0 % | 0 |
| address_us | 3 | 0.0 % | 0 |
| adv_at_word | 1 | 0.0 % | 0 |
| adv_dashed | 1 | 0.0 % | 0 |
| adv_dots | 1 | 100.0 % | 0 |
| adv_letter_o_for_zero | 1 | 0.0 % | 0 |
| adv_lowercase | 1 | 100.0 % | 0 |
| adv_newline_split | 1 | 0.0 % | 0 |
| adv_obfuscated | 2 | 0.0 % | 0 |
| adv_single_digit_spaced | 2 | 0.0 % | 0 |
| adv_zero_width | 2 | 100.0 % | 0 |
| adversarial | 12 | 33.3 % | 0 |
| amex | 1 | 100.0 % | 0 |
| card_length | 2 | 0.0 % | 0 |
| code_switch | 1 | 100.0 % | 0 |
| context_colloquial | 21 | 100.0 % | 0 |
| context_en | 16 | 100.0 % | 0 |
| context_formal | 34 | 100.0 % | 0 |
| fixed_line | 3 | 100.0 % | 0 |
| foreign | 2 | 100.0 % | 0 |
| format_bare | 1 | 100.0 % | 0 |
| format_bare_grouped | 2 | 100.0 % | 0 |
| format_cc_no_plus | 3 | 100.0 % | 0 |
| format_compact | 10 | 90.0 % | 0 |
| format_dashed | 12 | 91.7 % | 0 |
| format_dotted | 6 | 83.3 % | 0 |
| format_double_zero | 2 | 100.0 % | 0 |
| format_grouped | 13 | 92.3 % | 0 |
| format_intl | 2 | 100.0 % | 0 |
| format_intl_compact | 3 | 100.0 % | 0 |
| format_paren | 3 | 100.0 % | 0 |
| format_plain | 12 | 100.0 % | 0 |
| format_spaced | 2 | 0.0 % | 0 |
| format_trunk | 1 | 100.0 % | 0 |
| format_trunk_compact | 2 | 100.0 % | 0 |
| invalid_checksum_with_context | 2 | 100.0 % | 0 |
| ip_private | 1 | 0.0 % | 0 |
| ip_public | 5 | 0.0 % | 0 |
| link_unknown | 5 | 0.0 % | 0 |
| long | 2 | 100.0 % | 0 |
| multi | 33 | 100.0 % | 0 |
| no_context | 13 | 69.2 % | 0 |
| phone_de | 1 | 0.0 % | 0 |
| phone_es | 1 | 0.0 % | 0 |
| phone_fr | 1 | 0.0 % | 0 |
| phone_it | 2 | 0.0 % | 0 |
| phone_nl | 2 | 0.0 % | 0 |
| phone_uk | 3 | 0.0 % | 0 |
| phone_us | 4 | 0.0 % | 2 |
| suffix | 8 | 100.0 % | 0 |
| uppercase | 3 | 100.0 % | 0 |

## L0 misses (case ids)

- **ADDRESS** (9): l0-0300, l0-0301, l0-0302, l0-0305, l0-0306, l0-0308, l0-0309, l0-0310, l0-0311
- **CREDIT_CARD** (2): l0-0371, l0-0372
- **DE_TAX_ID** (2): l0-0349, l0-0351
- **EMAIL_ADDRESS** (3): l0-0156, l0-0369, l0-0370
- **ES_NIE** (3): l0-0337, l0-0338, l0-0339
- **ES_NIF** (3): l0-0333, l0-0334, l0-0336
- **FR_NIR** (3): l0-0353, l0-0354, l0-0359
- **IP_ADDRESS** (6): l0-0276, l0-0277, l0-0278, l0-0279, l0-0280, l0-0282
- **IT_FISCAL_CODE** (2): l0-0340, l0-0342
- **NL_BSN** (1): l0-0344
- **PERSON** (11): l0-0316, l0-0317, l0-0318, l0-0320, l0-0321, l0-0322, l0-0323, l0-0324, l0-0325, l0-0326, l0-0327
- **PHONE_NUMBER** (14): l0-0255, l0-0256, l0-0258, l0-0259, l0-0261, l0-0262, l0-0263, l0-0264, l0-0269, l0-0271, l0-0272, l0-0273, l0-0274, l0-0275
- **PL_PESEL** (2): l0-0346, l0-0347
- **TR_IBAN** (1): l0-0114
- **TR_PHONE** (1): l0-0096
- **TR_PLATE** (2): l0-0363, l0-0364
- **TR_TCKN** (6): l0-0036, l0-0038, l0-0040, l0-0051, l0-0055, l0-0056
- **TR_VKN** (5): l0-0239, l0-0240, l0-0241, l0-0242, l0-0243
- **UK_NINO** (2): l0-0355, l0-0357
- **UNKNOWN_LINK** (5): l0-0291, l0-0292, l0-0293, l0-0294, l0-0295
- **US_SSN** (7): l0-0244, l0-0245, l0-0246, l0-0247, l0-0248, l0-0249, l0-0250

## L0 false positives (case ids)

- **TR_PHONE** (3): l0-0240, l0-0255, l0-0259

## Type confusions (expected → predicted)

- PHONE_NUMBER → TR_PHONE: 2
- TR_VKN → TR_PHONE: 1

## Detector latency (this host, sequential, one thread)

p50 0.16 ms · p95 0.63 ms · max 2.38 ms (not the latency suite; indicative only)
