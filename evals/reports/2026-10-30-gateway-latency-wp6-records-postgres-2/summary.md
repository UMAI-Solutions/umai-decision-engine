# Gateway latency — what the gateway adds to a call

- **Suite:** `gateway-latency` (in-process gateway, zero-latency fake upstream, 200 runs per row after 5 warm-up calls, one client at a time)
- **Date:** 2026-10-30
- **Git commit:** 45fce50 (uncommitted changes)
- **Dataset:** `evals/suites/gateway_latency.py` (sha256 `7284789a4a2d`), split `all`
- **Engine:** umai-decision-engine 0.1.0, presidio-analyzer 2.2.364, phonenumbers 9.0.41
- **Host:** Windows 11, Python 3.12.6, AMD64
- **Guardrail:** `gr-demo-data-protection` (active version; full L0 catalog), L0 on 1 thread

| Scenario | p50 | p95 | p99 |
|---|---|---|---|
| fake upstream called directly (baseline) | 0.26 ms | 0.73 ms | 1.05 ms |
| 500 chars, clean, non-streaming | 10.53 ms | 16.95 ms | 22.39 ms |
| 4 000 chars with identifiers (masked), non-streaming | 11.43 ms | 17.92 ms | 23.31 ms |
| 16-message history, 8 000 chars, identifiers masked | 8.88 ms | 13.05 ms | 14.57 ms |
| streaming `window`, 2 000-char answer in 40-char chunks | 133.69 ms | 207.94 ms | 267.35 ms |
| streaming `buffer`, same answer | 19.59 ms | 25.60 ms | 28.28 ms |
| streaming `after`, same answer | 26.70 ms | 37.16 ms | 39.37 ms |

`window` released the answer in 48 content chunks; the first text left after about 160 upstream characters (hold-back 128, step 32). At 4 characters per token that is about 40 tokens: ≈ 0.40 s at 100 tokens/s, ≈ 1.00 s at 40 tokens/s before the user sees the first words.

**Reading.** With a zero-latency upstream the numbers are the gateway's own cost per call (HTTP handling in the test client included). Real upstream latency (hundreds of milliseconds to seconds) comes on top. Not measured: concurrency, the reference on-prem hardware, injection detection (WP4).
