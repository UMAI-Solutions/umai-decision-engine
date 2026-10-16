# Testing

| Level | Location | Runs | Network | Notes |
|---|---|---|---|---|
| Unit | `tests/unit/` | always | no | Validators, recognizers, normalization, rules, question compiler, merge, adapters with mocked HTTP |
| Integration | `tests/integration/` | always | no | FastAPI app + pipeline with fake providers; L2 worker with fake model, SQLite queue and redelivery |
| Live | `tests/live/` | `-m live` only | yes | Real providers; needs keys in `.env`; never in default CI |
| Evals | `evals/` | on demand | optional | Accuracy and latency; see `docs/evals/` |

## Required test kinds

- **Validators:** valid vectors, invalid-checksum vectors, separators, edge digits
  (leading zero, all-same digits). Vectors from `docs/reference/turkish-identifiers.md`.
- **Merge rules:** one test per row/rule in `docs/architecture/decision-merge-and-failure.md`,
  named after the rule (`test_l1b_cannot_go_below_l1a_floor`).
- **Adapters:** request mapping snapshot (with synthetic content), each error kind
  (`timeout`, `rate_limited`, `unavailable`, `invalid_response`, `auth`), answer outside
  options → error, missing probabilities stay `None`.
- **Budget:** a slow fake provider proves the pipeline stops at the remaining budget and
  applies the failure policy.
- **Leak:** for representative cases, serialize response, tagged action, captured logs and
  the payload sent to fakes; assert no raw entity value appears (with and without separators).
- **Contracts:** example payloads in `docs/contracts/*.md` parse with the Pydantic models
  (doc examples are tests).

## Conventions

- `pytest-asyncio` in auto mode; no `time.sleep` in tests, use fake clocks or short timeouts.
- Fixtures with content use synthetic data only; Turkish characters (`ğ ü ş ı ö ç İ`) appear
  in fixtures on purpose.
- Mark slow tests `@pytest.mark.slow` (Presidio with NER, large inputs).
