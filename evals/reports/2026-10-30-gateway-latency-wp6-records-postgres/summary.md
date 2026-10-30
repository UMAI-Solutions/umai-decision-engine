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
| fake upstream called directly (baseline) | 0.22 ms | 0.51 ms | 1.62 ms |
| 500 chars, clean, non-streaming | 7.19 ms | 12.76 ms | 16.97 ms |
| 4 000 chars with identifiers (masked), non-streaming | 8.76 ms | 13.64 ms | 15.38 ms |
| 16-message history, 8 000 chars, identifiers masked | 11.83 ms | 17.41 ms | 20.90 ms |
| streaming `window`, 2 000-char answer in 40-char chunks | 120.72 ms | 158.69 ms | 192.42 ms |
| streaming `buffer`, same answer | 12.25 ms | 16.40 ms | 18.62 ms |
| streaming `after`, same answer | 18.33 ms | 29.43 ms | 35.97 ms |

`window` released the answer in 48 content chunks; the first text left after about 160 upstream characters (hold-back 128, step 32). At 4 characters per token that is about 40 tokens: ≈ 0.40 s at 100 tokens/s, ≈ 1.00 s at 40 tokens/s before the user sees the first words.

**Reading.** With a zero-latency upstream the numbers are the gateway's own cost per call (HTTP handling in the test client included). Real upstream latency (hundreds of milliseconds to seconds) comes on top. Not measured: concurrency, the reference on-prem hardware, injection detection (WP4).
