# l0-pii-tr — baseline-dev

- **Date:** 2026-10-17
- **Git commit:** 0f0b420 (uncommitted changes)
- **Dataset:** `evals/datasets/l0-pii-tr/v1.jsonl` (sha256 `1e4c317836e5`), split `dev`
- **Engine:** umai-decision-engine 0.1.0, presidio-analyzer 2.2.364, phonenumbers 9.0.41
- **Host:** Windows 11, Python 3.12.6, AMD64
- **Detector config:** engine defaults, every supported type; min_score 0.5
- **Cases:** 183 (44 without entities)
- **Match rule:** same type and overlap ≥ 50 % of the longer span

## Headline (types the detector supports)

| | Precision | Recall | F1 | False alarms on clean texts |
|---|---|---|---|---|
| **L0** | 0.993 | 0.939 | 0.965 | 0.0 % |
| naive-regex baseline | 0.839 | 0.777 | 0.807 | 43.2 % |

The naive regex baseline is illustrative (digit shapes, no checksum, no context). The F1
exit criterion compares against umai-engine's regex rules (docs/roadmap.md).

## By entity type

| Type | Support | L0 P | L0 R | L0 F1 | Baseline P | Baseline R | Baseline F1 | Note |
|---|---|---|---|---|---|---|---|---|
| CREDIT_CARD | 18 | 1.000 | 1.000 | 1.000 | 0.810 | 0.944 | 0.872 |  |
| EMAIL_ADDRESS | 18 | 1.000 | 0.944 | 0.971 | 1.000 | 0.944 | 0.971 |  |
| IBAN_CODE | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |  |
| PERSON | 8 | — | 0.000 | — | — | 0.000 | — | not implemented in L0 |
| TR_IBAN | 18 | 1.000 | 0.944 | 0.971 | 1.000 | 0.889 | 0.941 |  |
| TR_PHONE | 40 | 0.975 | 0.975 | 0.975 | 0.917 | 0.825 | 0.868 |  |
| TR_TCKN | 52 | 1.000 | 0.885 | 0.939 | 0.667 | 0.577 | 0.619 |  |
| TR_VKN | 5 | — | 0.000 | — | — | 0.000 | — | not implemented in L0 |

## Recall by category (L0)

| Category | Support | Recall | FP |
|---|---|---|---|
| adv_at_word | 1 | 0.0 % | 0 |
| adv_dashed | 1 | 0.0 % | 0 |
| adv_dots | 1 | 100.0 % | 0 |
| adv_letter_o_for_zero | 1 | 0.0 % | 0 |
| adv_lowercase | 1 | 100.0 % | 0 |
| adv_newline_split | 1 | 0.0 % | 0 |
| adv_single_digit_spaced | 2 | 0.0 % | 0 |
| adv_zero_width | 2 | 100.0 % | 0 |
| adversarial | 10 | 40.0 % | 0 |
| amex | 1 | 100.0 % | 0 |
| code_switch | 1 | 100.0 % | 0 |
| context_colloquial | 21 | 100.0 % | 0 |
| context_en | 16 | 100.0 % | 0 |
| context_formal | 34 | 100.0 % | 0 |
| fixed_line | 3 | 100.0 % | 0 |
| foreign | 2 | 100.0 % | 0 |
| format_bare | 1 | 100.0 % | 0 |
| format_bare_grouped | 2 | 100.0 % | 0 |
| format_cc_no_plus | 3 | 100.0 % | 0 |
| format_compact | 9 | 100.0 % | 0 |
| format_dashed | 12 | 91.7 % | 0 |
| format_dotted | 6 | 83.3 % | 0 |
| format_double_zero | 2 | 100.0 % | 0 |
| format_grouped | 13 | 92.3 % | 0 |
| format_intl | 2 | 100.0 % | 0 |
| format_intl_compact | 3 | 100.0 % | 0 |
| format_paren | 3 | 100.0 % | 0 |
| format_plain | 12 | 100.0 % | 0 |
| format_trunk | 1 | 100.0 % | 0 |
| format_trunk_compact | 2 | 100.0 % | 0 |
| invalid_checksum_with_context | 2 | 100.0 % | 0 |
| long | 2 | 100.0 % | 0 |
| multi | 33 | 100.0 % | 0 |
| no_context | 12 | 75.0 % | 0 |
| suffix | 8 | 100.0 % | 0 |
| uppercase | 3 | 100.0 % | 0 |

## L0 misses (case ids)

- **EMAIL_ADDRESS** (1): l0-0156
- **PERSON** (8): l0-0244, l0-0245, l0-0246, l0-0247, l0-0248, l0-0249, l0-0250, l0-0251
- **TR_IBAN** (1): l0-0114
- **TR_PHONE** (1): l0-0096
- **TR_TCKN** (6): l0-0036, l0-0038, l0-0040, l0-0051, l0-0055, l0-0056
- **TR_VKN** (5): l0-0239, l0-0240, l0-0241, l0-0242, l0-0243

## L0 false positives (case ids)

- **TR_PHONE** (1): l0-0240

## Type confusions (expected → predicted)

- TR_VKN → TR_PHONE: 1

## Detector latency (this host, sequential, one thread)

p50 0.21 ms · p95 0.64 ms · max 2.64 ms (not the latency suite; indicative only)
