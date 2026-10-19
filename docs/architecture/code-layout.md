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
│   │   ├── policy_tests.py        # golden case files (policy-tests contract)
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
│   │   └── cli.py                 # keygen / validate / test / sign / verify
│   ├── control_plane/             # ADR-0022 policy service: drafts, tests, publish, activate
│   │   ├── app.py                 # FastAPI app (port 9200), bearer token; every route authorized
│   │   ├── authz.py               # ADR-0029 permission matrix, actor, tenant binding
│   │   ├── actors.py              # ADR-0023 §1 on-behalf-of assertion (ES256) verification
│   │   ├── organizations.py       # ADR-0033 organizations, members, invitations (rules)
│   │   ├── service.py             # draft → validate → golden tests → publish; accepted cases
│   │   ├── repository.py          # the only writer of the bundle source, per tenant; changelog
│   │   └── drafts.py              # SQLite draft store
│   ├── golden/
│   │   └── runner.py              # ADR-0015: golden cases → BundleStore + Evaluator → report
│   ├── tenancy/                   # planned (F6, ADR-0019): admission before the pipeline
│   │   ├── keys.py                # credential → tenant, key id, scopes (hashed keys only)
│   │   ├── entitlements.py        # signed snapshots / licence file; verify, swap, last-known-good
│   │   ├── limits.py              # compares a size (measured by l0/size.py, Q19) with the limit
│   │   └── quota.py               # monthly usage counter (memory; Redis or Postgres, Q23)
│   ├── export/                    # planned (F6): SIEM exporter, off the request path
│   ├── gateway/                   # planned (ADR-0034): OpenAI-compatible secure gateway, own port
│   │   ├── app.py                 # FastAPI app: /v1/chat/completions, /v1/models, health
│   │   ├── routes.py              # route resolution (tenant store), upstream allow-list
│   │   ├── chat_completions.py    # wire format ↔ EvaluationRequest, decisions on the wire
│   │   ├── streaming.py           # SSE parsing, buffer / window / after, tool-call buffering
│   │   ├── masking.py             # per-call placeholder map, exact-match restoration (memory only)
│   │   └── upstream.py            # httpx client per upstream kind, explicit timeouts, credentials
│   └── telemetry/
│       ├── logging.py             # structured logs, raw-content guard
│       └── metrics.py             # Prometheus-style counters/histograms
├── policies/                      # directory bundle source (ADR-0012); synthetic examples
│   └── <bundle_id>/active, <bundle_id>/<version>/bundle.yaml (+ manifest.json)
├── evals/
│   ├── run.py                     # python -m evals.run --suite <name>
│   ├── metrics.py                 # span matching, per-type and per-category scores
│   ├── baselines/                 # comparison detectors (naive regex; umai-engine rules)
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
| `golden` | `pipeline`, `policy`, `l0` (detector construction), `contracts`, `settings` | `api`, `l2`; nothing on the request path imports `golden` |
| `control_plane` | `golden`, `policy`, `contracts`, `l0` (detector for tests), `settings` | `api`; the engine never imports it |
| `tenancy` (planned) | `contracts`, `policy` (signature verification), `settings` | `l0`, `l1`, `l2`, `pipeline`; it receives a content size, never content (the size is measured in `l0/size.py`) |
| `export` (planned) | `contracts`, `events` | `l0`, `l1`, `pipeline`, `api` |
| `gateway` (planned, ADR-0034) | `pipeline`, `contracts`, `tenancy`, `settings` | `l1`, `l2`, `api`, `control_plane`; nothing imports `gateway` |

Only `l0` and `gateway` receive raw content (ADR-0035): the gateway forwards it only to the
caller and the route's upstream, after the guardrail allowed it. Layers communicate through
`contracts` models; a raw `str` of user content must not appear in any `l1`, `l2`, `session` or
`telemetry` function signature.

## Settings vs bundle

| Belongs in settings (env) | Belongs in the policy bundle |
|---|---|
| API keys, base URLs, tenant HMAC keys | Which entities, rules, policies, thresholds |
| Process limits, thread pool size | Timeouts per layer, failure behaviour |
| Store connection strings | Provider choice and fallback per bundle |
| Global caps on timeouts | Prompt / policy text |
