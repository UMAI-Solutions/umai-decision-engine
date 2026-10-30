# Gateway latency — what the gateway adds to a call

- **Suite:** `gateway-latency` (in-process gateway, zero-latency fake upstream, 200 runs per row after 5 warm-up calls, one client at a time)
- **Date:** 2026-10-30
- **Git commit:** 605e66a
- **Dataset:** `evals/suites/gateway_latency.py` (sha256 `7284789a4a2d`), split `all`
- **Engine:** umai-decision-engine 0.1.0, presidio-analyzer 2.2.364, phonenumbers 9.0.41
- **Host:** Windows 11, Python 3.12.6, AMD64
- **Guardrail:** `gr-demo-data-protection` (active version; full L0 catalog), L0 on 1 thread

| Scenario | p50 | p95 | p99 |
|---|---|---|---|
| fake upstream called directly (baseline) | 0.26 ms | 0.58 ms | 0.78 ms |
| 500 chars, clean, non-streaming | 7.46 ms | 10.06 ms | 11.44 ms |
| 4 000 chars with identifiers (masked), non-streaming | 7.95 ms | 11.00 ms | 11.83 ms |
| 16-message history, 8 000 chars, identifiers masked | 7.44 ms | 10.11 ms | 11.40 ms |
| streaming `window`, 2 000-char answer in 40-char chunks | 123.25 ms | 174.24 ms | 215.95 ms |
| streaming `buffer`, same answer | 13.50 ms | 16.52 ms | 17.92 ms |
| streaming `after`, same answer | 13.91 ms | 20.18 ms | 21.46 ms |

`window` released the answer in 48 content chunks; the first text left after about 160 upstream characters (hold-back 128, step 32). At 4 characters per token that is about 40 tokens: ≈ 0.40 s at 100 tokens/s, ≈ 1.00 s at 40 tokens/s before the user sees the first words.

**Reading.** With a zero-latency upstream the numbers are the gateway's own cost per call (HTTP handling in the test client included). Real upstream latency (hundreds of milliseconds to seconds) comes on top. Not measured: concurrency, the reference on-prem hardware, injection detection (WP4).
