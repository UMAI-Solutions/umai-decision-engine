# Suite `l0-latency` — s0-15-laptop

- **Date:** 2026-10-31
- **Git commit:** 1cac298 (uncommitted changes)
- **Dataset:** `evals/suites/l0_latency.py` (sha256 `ebb426d2d5c8`), split `all`
- **Engine:** umai-decision-engine 0.1.0, presidio-analyzer 2.2.364, phonenumbers 9.0.42
- **Host:** Windows 11, Python 3.12.6, AMD64
- **Method:** in-process, one thread, each call timed; admission 20000 calls per row after a warm-up, L0 200 unique texts per size after 5 warm-up texts
- **CPU:** pinned to logical CPU 19; the host was 28% busy during the run (other work competes for caches, memory bandwidth and turbo headroom; tail percentiles include preemption)
- **L0 configuration:** `gr-demo-data-protection` v4, 27 entity types (the active version's `l0` block), NER off, L0 cache off

## Verdict (admission, launch plan S0-15): PASS

- Admission p99 51.5 µs; budget 1000 µs (0.27 % of an L0 scan of 4 000 characters at p50).
- L0 p95 at 4 000 characters 25.21 ms, above the 20 ms hypothesis; L0 is judged on the reference hardware and a quiet host, not here.

## Admission per request (µs)

Each step alone, then the whole sequence of `POST /v1/evaluate` (client address → throttle → key → networks → rate limit → scope and tenant → prompt size → quota); the gateway runs the same steps. No I/O: keys, networks and limits come from the signed snapshot in memory, the quota from an in-memory count refreshed off the request path.

| Step | p50 | p95 | p99 |
|---|---|---|---|
| client address (socket peer, no trusted proxy) | 1.7 | 2.7 | 3.3 |
| failed-credential throttle check (ADR-0046 D3) | 0.3 | 0.3 | 0.4 |
| key lookup: parse, registry, constant-time hash, expiry (ADR-0023) | 3.8 | 4.2 | 6.9 |
| allowed networks, 1 network (ADR-0046 D1) | 0.6 | 0.6 | 1.0 |
| allowed networks, 50 networks, match in the last | 4.2 | 4.5 | 5.4 |
| per-key rate limit (token bucket) | 0.8 | 0.9 | 1.3 |
| scope and tenant binding | 0.3 | 0.4 | 0.5 |
| prompt size: limit and characters of a 4 000-char message (ADR-0019 §4) | 1.3 | 1.5 | 2.1 |
| monthly quota count (ADR-0043) | 2.7 | 3.1 | 9.3 |
| **whole admission**, key without networks | 15.9 | 19.7 | 37.0 |
| **whole admission**, key with 50 networks | 20.5 | 25.2 | 51.5 |

## L0 entity detection

| Input | p50 | p95 | p99 | Entities found per text |
|---|---|---|---|---|
| 500 chars | 2.82 ms | 3.70 ms | 4.85 ms | 5 |
| 4 000 chars | 18.75 ms | 25.21 ms | 28.15 ms | 48 |
| 16 000 chars | 78.95 ms | 98.12 ms | 102.04 ms | 195 |

**Reading.** Admission costs microseconds and does not grow with the input; L0 grows about linearly with the text. Measured on a developer laptop: the reference hardware (Q1) and a B2s vCPU are slower by a factor to be measured there; the budgets hold with a wide margin for admission. Not measured here: HTTP and JSON handling (see the `load` suite), concurrency.
