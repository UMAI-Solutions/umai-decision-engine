# Suite `load` — s0-3-laptop-2cpu

- **Date:** 2026-10-31
- **Git commit:** 1cac298 (uncommitted changes)
- **Dataset:** `evals/suites/load.py` (sha256 `79795176a7eb`), split `all`
- **Engine:** umai-decision-engine 0.1.0, presidio-analyzer 2.2.364, phonenumbers 9.0.42
- **Host:** Windows 11, Python 3.12.6, AMD64
- **Stack:** `evals/stack.py`, `UMAI_DE_ENV=production`, one uvicorn process each (as on the VM), access logs on, records off, rate limiter on with a lifted limit, quota counted against a lifted plan override
- **CPUs:** services on logical CPUs 0,1 (pinned); load generator and stubs on [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]; 22 logical CPUs on the host
- **Stubs:** answer after 0 ms; L1b policies enforcing: prompt-attack, malicious-use (one call on the request path, timeout 1400 ms as on beta), the other seven in shadow (a second call after the answer)
- **Resident memory after the run:** engine 152 MiB, gateway 132 MiB, policy 158 MiB, stubs 74 MiB
- **Requests:** unique 1000-character synthetic texts (no L0 cache hits); closed loop with 1,2,4,8,16,32 workers, 3 s warm-up then 20 s measured per step

## Results

Throughput counts 2xx answers per second. Degraded: 2xx evaluations decided by a failure policy (`degraded` or `errors` in the answer, e.g. an L1b call past its timeout in a saturated process). Server CPU: the scenario's process (engine or gateway) as a share of one CPU; CPU ms/request is that time per 2xx answer. Client CPU: the load generator, to show it was not the bottleneck. Host busy: the whole machine's CPU in use, this test included; other work on the host slows the run down.

| Scenario | Load | Requests | Non-2xx | Degraded | Throughput | p50 ms | p95 ms | p99 ms | Server CPU | CPU ms/request | Client CPU | Host busy |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| evaluate-l0 | c=1 | 2929 | 0 | 0 | 146/s | 6.6 | 9.3 | 11.6 | 75% | 5.14 | 27% | 26% |
| evaluate-l0 | c=2 | 3470 | 0 | 0 | 174/s | 10.5 | 19.1 | 24.0 | 92% | 5.33 | 35% | 37% |
| evaluate-l0 | c=4 | 3477 | 0 | 0 | 174/s | 16.4 | 60.0 | 87.6 | 95% | 5.49 | 36% | 39% |
| evaluate-l0 | c=8 | 3660 | 0 | 0 | 183/s | 36.0 | 90.9 | 128 | 94% | 5.16 | 36% | 33% |
| evaluate-l0 | c=16 | 3759 | 0 | 0 | 188/s | 80.0 | 149 | 202 | 94% | 4.99 | 42% | 30% |
| evaluate-l0 | c=32 | 1939 | 0 | 0 | 97/s | 292 | 663 | 1347 | 82% | 8.42 | 56% | 59% |
| evaluate-l1b | c=1 | 1298 | 0 | 0 | 65/s | 14.1 | 22.1 | 30.8 | 85% | 13.07 | 18% | 41% |
| evaluate-l1b | c=2 | 1289 | 0 | 0 | 64/s | 29.5 | 50.2 | 63.7 | 93% | 14.41 | 22% | 45% |
| evaluate-l1b | c=4 | 1304 | 0 | 23 | 65/s | 58.1 | 105 | 140 | 93% | 14.28 | 17% | 43% |
| evaluate-l1b | c=8 | 1164 | 0 | 47 | 58/s | 124 | 246 | 338 | 91% | 15.65 | 17% | 45% |
| evaluate-l1b | c=16 | 1105 | 0 | 31 | 55/s | 233 | 638 | 914 | 93% | 16.82 | 18% | 46% |
| evaluate-l1b | c=32 | 1276 | 0 | 710 | 64/s | 468 | 691 | 1227 | 93% | 14.56 | 20% | 44% |
| gateway | c=1 | 1165 | 0 | — | 58/s | 16.5 | 22.6 | 28.7 | 75% | 12.79 | 20% | 42% |
| gateway | c=2 | 1530 | 0 | — | 76/s | 24.8 | 40.4 | 50.9 | 95% | 12.39 | 18% | 39% |
| gateway | c=4 | 1358 | 0 | — | 68/s | 54.9 | 105 | 145 | 95% | 13.98 | 19% | 44% |
| gateway | c=8 | 1368 | 0 | — | 68/s | 112 | 185 | 216 | 94% | 13.70 | 18% | 43% |
| gateway | c=16 | 1333 | 0 | — | 67/s | 230 | 345 | 394 | 94% | 14.13 | 19% | 48% |
| gateway | c=32 | 1530 | 0 | — | 76/s | 375 | 672 | 839 | 94% | 12.26 | 21% | 40% |

Non-2xx answers, transport errors and degraded evaluations:

- evaluate-l1b c=4: 23 degraded answers; decisions {'ALLOW': 1281, 'BLOCK': 23}
- evaluate-l1b c=8: 47 degraded answers; decisions {'ALLOW': 1117, 'BLOCK': 47}
- evaluate-l1b c=16: 31 degraded answers; decisions {'ALLOW': 1074, 'BLOCK': 31}
- evaluate-l1b c=32: 710 degraded answers; decisions {'ALLOW': 566, 'BLOCK': 710}

## What a B2s can carry (estimate)

Scaled from the CPU time per request at the local peak. Assumptions: a B2s vCPU is 1.5–2.5× slower than this laptop's performance core (B-series hosts range from Haswell 2.4 GHz to Ice Lake; measure on the VM to narrow it); one uvicorn process uses at most one vCPU (Python's GIL); the policy service, Postgres, the console and Caddy (TLS) share the same two vCPUs and are not counted.

| Scenario | Local peak, no degraded answer (2 logical CPUs) | CPU ms/request | One process, 1 vCPU (burst), host 1.5–2.5× slower | B2s sustained (0.4 vCPU for the whole VM) |
|---|---|---|---|---|
| evaluate-l0 | 188/s (c=16) | 4.99 | 80–133/s | 32–53/s |
| evaluate-l1b | 65/s (c=1) | 13.07 | 31–51/s | 12–20/s |
| gateway | 76/s (c=2) | 12.39 | 32–54/s | 13–22/s |
