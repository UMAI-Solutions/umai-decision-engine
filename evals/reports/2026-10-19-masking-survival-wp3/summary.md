# Masking survival — do models keep the gateway's placeholders?

- **Suite:** `masking-survival` (live; 12 synthetic cases per model through the real gateway, temperature 0 where the model accepts it)
- **Date:** 2026-10-19
- **Git commit:** 5c5f690 (uncommitted changes)
- **Dataset:** `evals/datasets/masking-survival/v1.jsonl` (sha256 `8c948e5f6554`), split `all`
- **Engine:** umai-decision-engine 0.1.0, presidio-analyzer 2.2.364, phonenumbers 9.0.41
- **Host:** Windows 11, Python 3.12.6, AMD64
- **Models:** gpt-4.1-mini, gpt-5.4-mini

| Model | Type | Sent | Kept exactly | Altered | Dropped | Restored |
|---|---|---|---|---|---|---|
| gpt-4.1-mini | EMAIL_ADDRESS | 9 | 9 (100%) | 0 | 0 | 9/9 |
| gpt-4.1-mini | TR_IBAN | 5 | 5 (100%) | 0 | 0 | 5/5 |
| gpt-4.1-mini | TR_PHONE | 8 | 7 (88%) | 0 | 1 | 7/7 |
| gpt-4.1-mini | TR_TCKN | 6 | 5 (83%) | 0 | 1 | 5/5 |
| gpt-5.4-mini | EMAIL_ADDRESS | 9 | 9 (100%) | 0 | 0 | 9/9 |
| gpt-5.4-mini | TR_IBAN | 5 | 5 (100%) | 0 | 0 | 5/5 |
| gpt-5.4-mini | TR_PHONE | 8 | 8 (100%) | 0 | 0 | 8/8 |
| gpt-5.4-mini | TR_TCKN | 6 | 6 (100%) | 0 | 0 | 6/6 |

| Model | Task | Placeholders | Kept | Altered |
|---|---|---|---|---|
| gpt-4.1-mini | draft | 6 | 67% | 0 |
| gpt-4.1-mini | json | 8 | 100% | 0 |
| gpt-4.1-mini | summary | 6 | 100% | 0 |
| gpt-4.1-mini | table | 3 | 100% | 0 |
| gpt-4.1-mini | translate | 5 | 100% | 0 |
| gpt-5.4-mini | draft | 6 | 100% | 0 |
| gpt-5.4-mini | json | 8 | 100% | 0 |
| gpt-5.4-mini | summary | 6 | 100% | 0 |
| gpt-5.4-mini | table | 3 | 100% | 0 |
| gpt-5.4-mini | translate | 5 | 100% | 0 |

Failures: none.

**Reading.** *Kept* placeholders are restored for the caller; *altered* ones stay visible as damaged placeholders (no value leaks either way); *dropped* means the model left the value out (its task allowed it, e.g. a summary). Exact-match restoration is safe; an altered placeholder is a usability loss, never a disclosure.
