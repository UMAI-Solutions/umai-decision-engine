# What a Standard_B2s carries — estimate from the 2026-10-31 load runs (launch plan S0-3)

- **Date:** 2026-10-31
- **Sources:** [`load-s0-3-laptop-2cpu`](../2026-10-31-load-s0-3-laptop-2cpu/summary.md) (stubs answer at
  once), [`load-s0-3-laptop-2cpu-stub400ms`](../2026-10-31-load-s0-3-laptop-2cpu-stub400ms/summary.md)
  (stubs answer after 400 ms, about beta's measured L1b median), [`l0-latency-s0-15-laptop`](../2026-10-31-l0-latency-s0-15-laptop/summary.md),
  [`records-overhead`](../2026-10-26-records-overhead/summary.md), B2s facts in
  [`docs/reference/azure-b-series.md`](../../../docs/reference/azure-b-series.md) (verified 2026-10-31)
- **Host:** Windows 11 laptop, Intel Core Ultra 7 155H; services pinned to one performance core
  and its hyperthread (logical CPUs 0, 1), like a B2s's two vCPUs; the host was 26–67 % busy with
  other work during the runs, so latencies are noisy and CPU time per request is the steadier number
- **Not measured here:** the VM itself. `docs/workflow/load-test-beta.md` is the plan; nothing
  was run against umai-beta.

## Method

CPU time per request is read from the serving process at the highest throughput where every
answer was 2xx and decided as the policy says (no failure-policy decision). One uvicorn process
runs Python on one core at a time, so a process tops out near one vCPU. A B2s vCPU is assumed
1.5–2.5× slower than the laptop core (Azure places a B2s on Haswell to Ice Lake hosts; step 2 of
the beta plan measures the factor). A B2s gives 2 vCPUs while CPU credits last (a full bank of
576 credits lasts about 6 h at 100 %) and **0.4 vCPU sustained** for everything on the VM.

## Per request (1 000-character Turkish text, unique, no cache hits)

| Path | CPU ms per request (laptop) | Laptop peak, no degraded answer | One process on a B2s vCPU (burst) | Whole B2s, sustained (0.4 vCPU) |
|---|---|---|---|---|
| `POST /v1/evaluate`, L0 only | 5.0 | 188/s (16 workers) | 80–133/s | 32–53/s |
| `POST /v1/evaluate`, L1b: 2 enforcing + 7 shadow policies (stub) | 13.1 | 65/s (1 worker) | 31–51/s | 12–20/s |
| Gateway chat completion, L0 in and out, stub upstream | 12.4 | 76/s (2 workers) | 32–54/s | 13–22/s |

Not in the CPU figures, all on the same two vCPUs on the VM: Caddy's TLS, the records writer and
Postgres (records cost 25 % at low concurrency and 6 % at saturation, 2026-10-26), the policy
service, the console, the OS. Keep **30 % headroom** on the figures above until the beta run.

**Memory** after the runs (resident): engine 152 MiB, gateway 132 MiB, policy service 158 MiB,
against Compose limits of 900 MiB each; with the console (512 MiB) and Postgres (512 MiB) limits
the VM's 4 GiB is the planned total, not the used one.

## What breaks first: the L1b budget, not the CPU

With umai-beta's L1b settings (two policies enforcing, `l1b.timeout_ms` 1 400, `fail_closed`,
evaluation budget 1 500 ms; ADR-0040 amendment 2026-10-30) the pipeline asks L1b only while
1 400 ms of the 1 500 ms budget are left (`l1/stage.py`: "no runtime budget left for L1b"). Any
evaluation that spends more than about 100 ms before L1b — queueing behind other requests, or L0
over a long text — is decided by `fail_closed` (BLOCK, `degraded: true`) without asking L1b:

| Load (laptop, 2 logical CPUs) | Stub answers at once | Stub answers after 400 ms |
|---|---|---|
| 4 workers | 23 of 1 304 (1.8 %) | — |
| 8 workers | 47 of 1 164 (4 %) | 7 of 331 (2 %) |
| 32 workers | 710 of 1 276 (56 %) | 408 of 997 (41 %) |
| 64 workers | — | 927 of 927 (100 %) |

Without load, the same happens to long inputs: 2 of 5 clean 32 000-character texts were blocked
this way on the laptop (L0 took over 100 ms); on a slower B2s core the threshold drops to roughly
12 000–16 000 characters. A gateway call on beta runs the same check (it sends no `timeout_ms`,
so it gets the 1 500 ms default). So on the B2s the L1b path's clean capacity is a few concurrent
requests, far below its CPU capacity. Options (none taken here; a decision for ADR-0040 and the
beta configuration): start L1b with the remaining budget instead of skipping it, lower the
guardrail's `l1b.timeout_ms`, or raise the evaluation budget (`UMAI_DE_DEFAULT_TIMEOUT_MS`, at most
`UMAI_DE_MAX_TIMEOUT_MS`).

## Past saturation

Closed loops beyond saturation add latency, not throughput (L0-only: p50 36 ms at 8 workers,
80 ms at 16, 292 ms at 32); a 400 ms upstream needs about 8 concurrent calls to reach 17/s. There
is no concurrency limit in front of the services, so overload shows up as latency and, on the
L1b path, as fail-closed blocks rather than as 503s.

## Reading for sizing

- Demand at Stage 0 is small: a Pro plan's 100 000 requests a month is 0.04/s on average and
  0.16/s if all of it fell in working hours. The sustained B2s figures are two orders of magnitude
  above that; CPU is not what limits a B2s at Stage 0.
- What does: the L1b budget behaviour above (fix before more traffic), a single uvicorn process
  per service (one vCPU each, at most), burst credits for peaks (an alert on credits remaining),
  and the Bv1 series' End of Life status (a successor size, ADR-0050's question).
- Narrow the 1.5–2.5× factor on the VM (beta plan step 2) before quoting a single number.
