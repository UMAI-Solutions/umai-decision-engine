# ReDoS protection for tenant-written L0 patterns: today's matcher against RE2

- **Date:** 2026-11-02
- **Why:** [ADR-0056](../../../docs/adr/0056-guardrail-and-policy-authoring-in-the-console.md) lets
  customers write custom-recognizer patterns in the console. Today's protection
  (`l0/recognizers/custom.py`, `l0/recognizers/base.py`) was measured against RE2 before choosing.
- **Host:** Windows 11 laptop, Intel64 Family 6 Model 170, Python 3.12.6; `regex` 2026.9.29 (the
  engine's matcher), `google-re2` 1.1.20251105 (wheel `cp312-win_amd64`); engine at 0762d94
- **Method:** one `finditer` over the whole input, exactly as `SpecRecognizer.analyze` runs it
  (`regex` with `flags=MULTILINE` and `timeout=1 s`, the default `REGEX_TIMEOUT_SECONDS`); "load
  check" is `pattern_problems()`, the bundle-load check. Attack inputs are the classic "almost
  matches" strings (`a…a!`, `1…1!`). Medians of 3 runs at 1 000 characters, single runs above
  (each `regex` run that times out costs one second), 5 runs for RE2. Benign inputs are synthetic
  Turkish prose, medians of 15. The script is kept with the session notes and becomes
  `dev/bench_redos.py` with the RE2 change (ADR-0056 build step E2).

## Attack patterns: time of one scan

| Pattern | Today's load check | Characters | `regex` ms | `regex` result | RE2 ms | RE2 result |
|---|---|---|---|---|---|---|
| `(a+)+$` | rejected | 1 000 – 32 000 | 1 000 | timeout | 0.01 | no match |
| `(a\|a)*$` | rejected (matches empty) | 1 000 – 32 000 | 1 000 | timeout | 0.03 – 0.24 | 2 matches |
| `(\w\|\d)+$` | **accepted** | 1 000 | 118 | no match | 0.01 | no match |
| `(\w\|\d)+$` | **accepted** | 4 000 – 32 000 | 1 000 | **timeout** | 0.01 – 0.02 | no match |
| `([a-z0-9]{1,20})+$` | **accepted** | 1 000 | 330 | no match | 0.01 | no match |
| `([a-z0-9]{1,20})+$` | **accepted** | 4 000 – 32 000 | 1 000 | **timeout** | 0.01 | no match |
| `[A-Z0-9]+[A-Z0-9]+-X` | accepted | 1 000 – 32 000 | ≤ 0.1 | no match | 0.04 – 0.21 | no match |
| `\d+\d+\d+-\d{4}` | accepted | 1 000 – 32 000 | ≤ 0.1 | no match | 0.02 – 0.17 | no match |
| `[\w.]+@[\w.]+\.com` | accepted | 1 000 – 32 000 | ≤ 0.1 | no match | 0.02 – 0.20 | no match |

- Two patterns that pass today's load check reach the one-second timeout from 4 000 characters
  (the engine accepts 32 000). On a timeout `SpecRecognizer.analyze` skips the pattern
  (`except TimeoutError: continue`, `base.py:97-98`): the input is scanned as if the recognizer
  did not exist, nothing is recorded, and one of the process's four L0 threads
  (`UMAI_DE_L0_THREADS`) is busy for a second per pattern and chunk.
- `regex` releases the GIL while it matches: another Python thread kept running during the timed-out
  match (5.3 M against 9.0 M counter increments per second when idle), so the cost is a thread and
  a CPU core per match, not the whole process.
- RE2 stayed below 0.3 ms on every attack input at every length (linear-time matching).

## Benign patterns: cost of the switch

32 000 characters, the largest input the engine accepts.

| Pattern | Matches | `regex` ms | RE2 ms |
|---|---|---|---|
| `(?<![A-Z0-9])MUS-[0-9]{6}(?![0-9])` (policy-bundle's own example) | 3 | 0.025 | does not compile (`invalid perl operator: (?<!`) |
| `\bMUS-[0-9]{6}\b` | 3 | 0.025 | 0.40 |
| `\b[A-Z]{3}-[0-9]{4}-[A-Z]{2}\b` | 0 | 0.025 | 0.16 |
| `\bMUS-[0-9]{6}\b` (dense text) | 258 | 0.39 | 2.50 |
| `\b[A-Z]{3}-[0-9]{4}-[A-Z]{2}\b` (dense text) | 0 | 0.94 | 0.15 |

Compiling a new pattern: `regex` 206–267 µs, RE2 61–69 µs.

- RE2 costs 0.15–0.4 ms per pattern per 32 000 characters on ordinary text, against 0.025 ms for
  `regex` with a literal prefix: the Python binding converts the text on every call and builds a
  match object per match (2.5 ms with 258 matches). Scanning each chunk's UTF-8 bytes once for all
  patterns is the obvious optimisation if the `l0-latency` suite shows the cost at the pattern cap.
- RE2 has no lookbehind, lookahead or backreferences. The contract's example pattern uses
  lookarounds to require a whole value; a separate whole-value check is needed (ADR-0056 §4).

## Conclusion

The load-time heuristic misses common exponential shapes, and the timeout turns them into a
silent miss. ADR-0056 compiles tenant-written patterns with RE2, adds a whole-value option for the
lost lookarounds, and turns any matcher timeout or error into `L0_ERROR` under the guardrail's
failure policy.
