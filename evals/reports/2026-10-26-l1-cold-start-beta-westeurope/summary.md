# l1-cold-start — beta-westeurope

- **Date:** 2026-10-26
- **Git commit:** unknown in the container (no git): `c7cc21b`, mounted over `umai-decision-engine:current` (7740237)
- **Dataset:** `policies/gr-demo-data-protection/5/bundle.yaml` (sha256 `10d1685c95e0`), split `all`
- **Engine:** umai-decision-engine 0.1.0, presidio-analyzer 2.2.364, phonenumbers 9.0.41
- **Host:** Linux 7.0.0-1017-azure, Python 3.12.15, x86_64
- **Provider:** OpenAI Decisions `gpt-6-luna` at `https://api.openai.com/v1`; bundle `policies/gr-demo-data-protection/5/bundle.yaml` (nine policies, about 11 k input tokens per call)
- **Where it ran:** umai-beta, Azure West Europe (Standard_B2s), one-off container of umai-decision-engine:current with this branch mounted
- **Processes:** 6 per variant, alternating; calls 1–3 back to back, call 4 after 8 s idle; client timeout 10000 ms; `after` keeps idle connections 120 s, `before` 5 s

| Variant | Call | Calls | p50 ms | max ms | New connections | Client + network p50 / max ms | OpenAI processing p50 / max ms | Errors |
|---|---|---|---|---|---|---|---|---|
| `before` | 1 (first) | 6 | 300 | 396 | 6/6 | 102 / 108 | 198 / 294 | 0  |
| `before` | 2 | 6 | 287 | 375 | 0/6 | 54 / 75 | 235 / 314 | 0  |
| `before` | 3 | 6 | 274 | 368 | 0/6 | 56 / 64 | 219 / 310 | 0  |
| `before` | 4 (after 8 s) | 6 | 287 | 417 | 6/6 | 74 / 84 | 213 / 352 | 0  |
| `after` | 1 (first) | 6 | 275 | 363 | 0/6 | 54 / 71 | 218 / 292 | 0  |
| `after` | 2 | 6 | 263 | 355 | 0/6 | 56 / 76 | 204 / 299 | 0  |
| `after` | 3 | 6 | 263 | 349 | 0/6 | 56 / 65 | 204 / 284 | 0  |
| `after` | 4 (after 8 s) | 6 | 289 | 430 | 0/6 | 49 / 60 | 242 / 381 | 0  |

**Warm-up** (`after`, at startup): outcomes ['ok'], p50 321 ms, max 420 ms.

**Reading.** Before, the first call of a process opened a connection and paid about 46 ms more
than later calls (client and network p50 102 ms against 54–56), and a call after 8 s idle
opened another one (6/6, p50 74 ms). After, the warm-up's connection serves the first call
(0/6 new connections, p50 54 ms, the same as later calls) and the call after 8 s idle (0/6,
p50 49 ms). OpenAI's own processing time is the same in both variants; the warm-up does not
change the provider's tail. The warm-up itself (one synthetic decision) took 321 ms p50,
420 ms max, before the process takes traffic. An earlier run of this suite with a
`GET /v1/models/gpt-6-luna` warm-up gave the same client-side picture (first call 67 ms
after, 103 before) and showed a 2 533 ms provider-side call on a warm connection.
