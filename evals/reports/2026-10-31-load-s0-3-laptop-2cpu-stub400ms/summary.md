# Suite `load` — s0-3-laptop-2cpu-stub400ms

- **Date:** 2026-10-31
- **Git commit:** 1cac298 (uncommitted changes)
- **Dataset:** `evals/suites/load.py` (sha256 `79795176a7eb`), split `all`
- **Engine:** umai-decision-engine 0.1.0, presidio-analyzer 2.2.364, phonenumbers 9.0.42
- **Host:** Windows 11, Python 3.12.6, AMD64
- **Stack:** `evals/stack.py`, `UMAI_DE_ENV=production`, one uvicorn process each (as on the VM), access logs on, records off, rate limiter on with a lifted limit, quota counted against a lifted plan override
- **CPUs:** services on logical CPUs 0,1 (pinned); load generator and stubs on [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]; 22 logical CPUs on the host
- **Stubs:** answer after 400 ms; L1b policies enforcing: prompt-attack, malicious-use (one call on the request path, timeout 1400 ms as on beta), the other seven in shadow (a second call after the answer)
- **Resident memory after the run:** engine 164 MiB, gateway 149 MiB, policy 158 MiB, stubs 79 MiB
- **Requests:** unique 1000-character synthetic texts (no L0 cache hits); closed loop with 8,32,64,128 workers, 3 s warm-up then 20 s measured per step

## Results

Throughput counts 2xx answers per second. Degraded: 2xx evaluations decided by a failure policy (`degraded` or `errors` in the answer, e.g. an L1b call past its timeout in a saturated process). Server CPU: the scenario's process (engine or gateway) as a share of one CPU; CPU ms/request is that time per 2xx answer. Client CPU: the load generator, to show it was not the bottleneck. Host busy: the whole machine's CPU in use, this test included; other work on the host slows the run down.

| Scenario | Load | Requests | Non-2xx | Degraded | Throughput | p50 ms | p95 ms | p99 ms | Server CPU | CPU ms/request | Client CPU | Host busy |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| evaluate-l1b | c=8 | 331 | 0 | 7 | 17/s | 466 | 532 | 563 | 24% | 14.22 | 5% | 35% |
| evaluate-l1b | c=32 | 997 | 0 | 408 | 50/s | 663 | 1043 | 1372 | 74% | 14.82 | 15% | 43% |
| evaluate-l1b | c=64 | 927 | 0 | 927 | 46/s | 1277 | 1794 | 1997 | 92% | 19.76 | 19% | 50% |
| evaluate-l1b | c=128 | 567 | 2 | 565 | 28/s | 3073 | 6626 | 8097 | 78% | 27.44 | 40% | 50% |
| gateway | c=8 | 345 | 0 | — | 17/s | 446 | 505 | 542 | 33% | 19.09 | 8% | 59% |
| gateway | c=32 | 687 | 0 | — | 34/s | 821 | 1416 | 1796 | 77% | 22.32 | 15% | 63% |
| gateway | c=64 | 623 | 0 | — | 31/s | 1505 | 3817 | 5430 | 79% | 25.30 | 17% | 67% |
| gateway | c=128 | 435 | 0 | — | 22/s | 2731 | 8189 | 9775 | 69% | 31.50 | 68% | 61% |

Non-2xx answers, transport errors and degraded evaluations:

- evaluate-l1b c=8: 7 degraded answers; decisions {'ALLOW': 324, 'BLOCK': 7}
- evaluate-l1b c=32: 408 degraded answers; decisions {'BLOCK': 408, 'ALLOW': 589}
- evaluate-l1b c=64: 927 degraded answers; decisions {'BLOCK': 927}
- evaluate-l1b c=128: {'ReadError': 2}
- evaluate-l1b c=128: 565 degraded answers; decisions {'BLOCK': 565}

## What a B2s can carry (estimate)

Scaled from the CPU time per request at the local peak. Assumptions: a B2s vCPU is 1.5–2.5× slower than this laptop's performance core (B-series hosts range from Haswell 2.4 GHz to Ice Lake; measure on the VM to narrow it); one uvicorn process uses at most one vCPU (Python's GIL); the policy service, Postgres, the console and Caddy (TLS) share the same two vCPUs and are not counted.

| Scenario | Local peak, no degraded answer (2 logical CPUs) | CPU ms/request | One process, 1 vCPU (burst), host 1.5–2.5× slower | B2s sustained (0.4 vCPU for the whole VM) |
|---|---|---|---|---|
| evaluate-l1b | 50/s (c=32) | 14.82 | 27–45/s | 11–18/s |
| gateway | 34/s (c=32) | 22.32 | 18–30/s | 7–12/s |
