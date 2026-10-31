# Suite `admission` — key-networks

- **Date:** 2026-10-30
- **Git commit:** 21defe1 (uncommitted changes)
- **Dataset:** `evals/suites/admission.py` (sha256 `7660ab24cabc`), split `all`
- **Engine:** umai-decision-engine 0.1.0, presidio-analyzer 2.2.364, phonenumbers 9.0.41
- **Host:** Windows 11, Python 3.12.6, AMD64

In-process, no HTTP; 20000 calls × 7 repeats per scenario, median of the per-repeat mean. The admission path the engine API and the gateway run per request: the client address (`Admission.client_of`), `authenticate` and the per-key rate limit. The first row is admission before ADR-0046, without an address or a throttle.

| Scenario | µs per call | vs. before |
|---|---|---|
| before ADR-0046: key, no address, no throttle | 15.90 | +0.00 |
| key without networks, peer address, throttle on | 21.34 | +5.43 |
| key without networks, behind a trusted proxy (X-Forwarded-For with 2 hops) | 27.83 | +11.92 |
| key with 1 network, client inside | 23.04 | +7.14 |
| key with 50 networks, client in the last | 32.39 | +16.49 |
| 403 ip_not_allowed (key with 50 networks, client outside) | 32.33 | +16.43 |
| 401 bad credential, counted (a new source each time) | 28.56 | +12.66 |
| 429 throttled source (before any lookup) | 11.83 | -4.07 |
