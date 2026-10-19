# Gateway latency — what the gateway adds to a call

- **Suite:** `gateway-latency` (in-process gateway, zero-latency fake upstream, 200 runs per row after 5 warm-up calls, one client at a time)
- **Date:** 2026-10-19
- **Git commit:** 5c5f690 (uncommitted changes)
- **Dataset:** `evals/suites/gateway_latency.py` (sha256 `65307c4d2972`), split `all`
- **Engine:** umai-decision-engine 0.1.0, presidio-analyzer 2.2.364, phonenumbers 9.0.41
- **Host:** Windows 11, Python 3.12.6, AMD64
- **Guardrail:** `gr-demo-data-protection` (active version; full L0 catalog), L0 on 1 thread

| Scenario | p50 | p95 | p99 |
|---|---|---|---|
| fake upstream called directly (baseline) | 0.12 ms | 0.33 ms | 0.54 ms |
| 500 chars, clean, non-streaming | 5.14 ms | 7.52 ms | 8.45 ms |
| 4 000 chars with identifiers (masked), non-streaming | 16.67 ms | 26.20 ms | 30.67 ms |
| 16-message history, 8 000 chars, identifiers masked | 18.22 ms | 23.10 ms | 27.68 ms |
| streaming `window`, 2 000-char answer in 40-char chunks | 85.50 ms | 109.02 ms | 134.89 ms |
| streaming `buffer`, same answer | 7.94 ms | 9.83 ms | 11.58 ms |
| streaming `after`, same answer | 21.23 ms | 42.89 ms | 57.10 ms |

`window` released the answer in 48 content chunks; the first text left after about 160 upstream characters (hold-back 128, step 32). At 4 characters per token that is about 40 tokens: ≈ 0.40 s at 100 tokens/s, ≈ 1.00 s at 40 tokens/s before the user sees the first words.

**Reading.** With a zero-latency upstream the numbers are the gateway's own cost per call (HTTP handling in the test client included). Real upstream latency (hundreds of milliseconds to seconds) comes on top. Not measured: concurrency, the reference on-prem hardware, injection detection (WP4).
