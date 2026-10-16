# L0 latency — development machine baseline

- **Date:** 2026-10-16
- **Suite:** `l0-latency` (ad-hoc script; the eval harness does not exist yet)
- **Git commit:** none yet (repository has no commits)
- **Engine:** presidio-analyzer 2.2.364, `no_op` NLP engine, core entities
  (TR_TCKN, TR_IBAN, IBAN_CODE, TR_PHONE, CREDIT_CARD, EMAIL_ADDRESS), NER off
- **Host:** developer Windows 11 laptop, Python 3.12.6, single detector thread, in-process
  (no HTTP), 200 runs per size after one warm-up run
- **Data:** synthetic Turkish business text with one block of PII per ~360 characters

| Input size | p50 | p95 | p99 |
|---|---|---|---|
| 500 chars | 1.39 ms | 2.73 ms | 3.47 ms |
| 4 000 chars | 8.86 ms | 13.48 ms | 18.11 ms |
| 16 000 chars | 37.94 ms | 52.83 ms | 64.71 ms |

**Reading:** the 4 kB hypothesis budget (p95 < 20 ms, `docs/architecture/l0-entity-detection.md`)
holds on this machine. Latency grows roughly linearly with input length, as reported for
Presidio. Not representative of the reference on-prem hardware (Q1) or of concurrent load.

**Next:** rerun with the eval harness on the reference hardware, with concurrency, and
record the commit.
