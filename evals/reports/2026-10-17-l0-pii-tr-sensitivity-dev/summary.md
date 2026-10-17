# l0-pii-tr — sensitivity sweep (dev)

- **Date:** 2026-10-17
- **Git commit:** c543b45 + uncommitted guardrail-policy changes (branch feat/l0-catalog-golden-tests)
- **Dataset:** `evals/datasets/l0-pii-tr/v2.jsonl` (sha256 `6f59a1c3d0b3`), split `dev` (480 cases)
- **Command:** `python -m evals.run --suite l0-pii-tr --split dev --sensitivity L1|L2|L3|L4`
- **Detector config:** engine defaults, every supported type (27); `min_score` from the level
  (policy-bundle 0.6, `SENSITIVITY_MIN_SCORE`)

| Level | min_score | Precision | Recall | F1 | False alarms on clean texts |
|---|---|---|---|---|---|
| L1 (lenient) | 0.80 | 1.000 | 0.613 | 0.760 | 0.0 % |
| L2 (balanced) | 0.65 | 1.000 | 0.790 | 0.883 | 0.0 % |
| **L3 (default)** | 0.50 | 1.000 | 0.986 | 0.993 | 0.0 % |
| L4 (paranoid) | 0.35 | 1.000 | 0.986 | 0.993 | 0.0 % |

**Reading:** on this synthetic dataset precision is 1.000 at every level, so L1/L2 only cost
recall: many valid detections score 0.6 (checksum-valid TCKN, mobile numbers, dashed SSNs),
which L1/L2 cut. L4 adds nothing here because context-required shapes stay context-required at
every level and the dataset has no near-threshold cases. Keep **L3 as the default**; revisit the
level → score mapping when false-positive data from real traffic exists (Lakera's default is
L4, but its scores are not comparable to ours).
