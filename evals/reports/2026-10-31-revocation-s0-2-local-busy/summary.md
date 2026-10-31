# Suite `revocation` — s0-2-local-busy

- **Date:** 2026-10-31
- **Git commit:** 1cac298 (uncommitted changes)
- **Dataset:** `evals/suites/revocation.py` (sha256 `6d07f6cd5750`), split `all`
- **Engine:** umai-decision-engine 0.1.0, presidio-analyzer 2.2.364, phonenumbers 9.0.42
- **Host:** Windows 11, Python 3.12.6, AMD64
- **Stack:** policy service, engine API and gateway as separate uvicorn processes (`evals/stack.py`), `UMAI_DE_ENV=production`, signed key snapshots in a shared directory, registry poll every 5 s (the default; the VM does not override it)
- **Rounds:** 20; probes every 100 ms per service (accepted: 200, refused: 401); random pauses of 0–5 s spread the changes over the poll cycle; the failed-credential throttle's limit is lifted, since probes of a key not yet loaded would trip it
- **Background load:** 16 workers on the engine and the gateway with another key, 45821 calls (167/s) during the run

## Verdict: PASS (slowest refusal 5.86 s; budget 10 s)

Latency: from sending the change to the policy service to the end of the first probe with the new answer (an upper bound; probes add up to ~0.1 s).

| What | Rounds | p50 | p95 | max |
|---|---|---|---|---|
| engine API: revoked key refused (401) | 20 | 2.98 s | 5.42 s | 5.68 s |
| gateway: revoked key refused (401) | 20 | 3.34 s | 5.58 s | 5.86 s |
| engine API: new key accepted | 20 | 2.86 s | 4.96 s | 5.04 s |
| gateway: new key accepted | 20 | 3.56 s | 5.12 s | 5.73 s |
| policy service: revocation answered | 20 | 0.68 s | 0.79 s | 0.85 s |

**Reading.** The policy service writes the tenant's signed snapshot before it answers; each process reads the directory every 5 s, so a change takes effect after at most one poll interval plus the reload. With one shared directory (the single VM) nothing else is in the path. A stack whose engines pull snapshots over HTTP (ADR-0025 §8) adds its pull interval and must be measured again.
