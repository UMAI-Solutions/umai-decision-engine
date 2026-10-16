# Code layout (target)

This is the layout the F0 scaffold creates. Until then it is the plan; agents creating
the first modules follow it. Change it with an ADR.

```text
umai-decision-engine/
├── pyproject.toml                 # package, deps, ruff, pytest config
├── .env.example                   # names of settings and secrets, no values
├── src/umai_decision_engine/
│   ├── settings.py                # pydantic-settings; env-driven; secrets never in bundles
│   ├── contracts/                 # Pydantic models = docs/contracts, one module per contract
│   │   ├── evaluation.py          # EvaluationRequest, EvaluationResponse
│   │   ├── bundle.py              # PolicyBundle and its l0 / l1a / l1b / l2 blocks
│   │   ├── entities.py            # EntityDetectionResult
│   │   ├── questions.py           # DecisionQuestion, DecisionRequest, DecisionResult
│   │   ├── tagged_action.py       # TaggedAction
│   │   ├── analysis.py            # L2Analysis
│   │   └── compat/umai_engine.py  # InternalRequest/InternalResponse adapters (cutover phase)
│   ├── api/
│   │   ├── app.py                 # FastAPI app factory, lifespan (bundle load, L0 warm-up, poller)
│   │   └── routes.py              # /v1/evaluate, /v1/detect (dev), /v1/bundles(/reload), health
│   ├── pipeline/
│   │   ├── orchestrator.py        # resolve bundle → L0 → L1 → decision; budget; MONITOR mode
│   │   ├── failure.py             # failure policy resolution
│   │   ├── l0_gate.py             # ADR-0014: L0 detection → per-type action + exceptions
│   │   └── merge.py               # (L1) docs/architecture/decision-merge-and-failure.md
│   ├── l0/
│   │   ├── detector.py            # AnalyzerEngine per worker thread (no_op NLP), resolve, hash, redact
│   │   ├── normalize.py           # NFKC, zero-width stripping, offset map, Turkish case folding
│   │   ├── context.py             # Turkish-aware context scoring (replaces Presidio's enhancer)
│   │   ├── target.py              # l0.target → text to scan
│   │   ├── recognizers/
│   │   │   ├── base.py            # RecognizerSpec + Presidio LocalRecognizer that runs it
│   │   │   └── catalog.py         # core entity specs, tag codes; extended types go here
│   │   ├── validators.py          # checksum functions (pure, heavily tested)
│   │   ├── hashing.py             # HMAC per tenant
│   │   └── redact.py              # placeholder generation
│   ├── rules/                     # deterministic, shared by the L0 gate and L1a
│   │   ├── facts.py               # request + L0 summary → facts (no content, no values)
│   │   └── conditions.py          # condition operators, missing facts never match
│   ├── l1/
│   │   ├── rules.py               # L1a evaluation
│   │   ├── questions.py           # compile bundle policies to DecisionRequest
│   │   ├── mapping.py             # answers → action, tags, review flag
│   │   └── decision_models/
│   │       ├── base.py            # DecisionModel protocol, ProviderCapabilities, errors
│   │       ├── jev.py
│   │       ├── openai_decisions.py
│   │       ├── gpt_oss_safeguard.py
│   │       └── fake.py            # deterministic fake for tests and offline evals
│   ├── events/
│   │   ├── base.py                # EventSink, ReviewQueue protocols
│   │   ├── memory.py              # tests
│   │   └── sqlite.py              # R&D default: durable, lease/ack, idempotent by event_id
│   ├── l2/                        # asynchronous review worker — not imported by pipeline/api
│   │   ├── worker.py              # lease → history → analyse → validate → store → ack
│   │   ├── analyst.py             # prompt build, call, parse, validate
│   │   ├── findings.py            # threshold + idempotent finding candidates
│   │   ├── prompts/               # versioned prompt templates
│   │   └── client.py              # OpenAI-compatible chat client (httpx, explicit timeout)
│   ├── session/
│   │   ├── base.py                # SessionHistoryStore protocol
│   │   ├── memory.py              # tests
│   │   └── sqlite.py              # R&D default; Redis later
│   ├── policy/                    # ADR-0012: the engine only reads bundles
│   │   ├── sources.py             # BundleSource: directory (R&D, on-prem); http_pull later
│   │   ├── manifest.py            # SHA-256 + Ed25519 verification
│   │   ├── store.py               # validated, compiled bundles; atomic swap; last-known-good
│   │   ├── validate.py            # semantic validation beyond the schema
│   │   └── cli.py                 # keygen / sign / verify for hand-made bundles
│   └── telemetry/
│       ├── logging.py             # structured logs, raw-content guard
│       └── metrics.py             # Prometheus-style counters/histograms
├── policies/                      # directory bundle source (ADR-0012); synthetic examples
│   └── <bundle_id>/active, <bundle_id>/<version>/bundle.yaml (+ manifest.json)
├── evals/
│   ├── run.py                     # python -m evals.run --suite <name>
│   ├── suites/                    # suite definitions (which dataset, which layers, which metrics)
│   ├── datasets/                  # synthetic JSONL only
│   └── reports/                   # generated; only summary.md files are committed
└── tests/
    ├── unit/                      # no network, no models; providers use fake.py
    ├── integration/               # API + pipeline with fakes
    └── live/                      # marked @pytest.mark.live; real providers; skipped by default
```

## Boundaries

| Module | May import | Must not import |
|---|---|---|
| `contracts` | pydantic only | anything else in the package |
| `l0` | `contracts`, presidio | `l1`, `l2`, `session`, `api` |
| `l1` | `contracts`, `session` (summary only) | `l0` internals, `l2`, `api` |
| `l2` | `contracts`, `session`, `events` | `l0`, `l1`, `pipeline`, `api` |
| `pipeline` | `l0`, `l1`, `events` (emit only), `contracts`, `telemetry` | `l2`, `api` |
| `api` | `pipeline`, `contracts`, `settings` | layer internals |

Only `l0` receives raw content. Layers communicate through `contracts` models; a raw
`str` of user content must not appear in any `l1`, `l2`, `session` or `telemetry`
function signature.

## Settings vs bundle

| Belongs in settings (env) | Belongs in the policy bundle |
|---|---|
| API keys, base URLs, tenant HMAC keys | Which entities, rules, policies, thresholds |
| Process limits, thread pool size | Timeouts per layer, failure behaviour |
| Store connection strings | Provider choice and fallback per bundle |
| Global caps on timeouts | Prompt / policy text |
