# AGENTS.md

Instructions for coding agents (Claude Code, Codex, others) working in this repository.
Read this file first, then the document in [`docs/README.md`](docs/README.md) that matches your task.

## What this repository is

`umai-decision-engine` is the **R&D successor of `umai-engine`**, the policy-evaluation engine
of the UMAI AI-security platform. L0 + L1 decide synchronously at runtime; L2 reviews afterwards:

| Layer | Question it answers | Technology |
|---|---|---|
| **L0** | What sensitive entities are in this content? If any: decide here (L0 gate), skip L1 | Microsoft Presidio + Turkish recognizers; per-type action + deterministic exceptions (ADR-0014) |
| **L1a** | Does a deterministic rule decide this already? | Rule engine over structured facts |
| **L1b** | Is this action compliant with the policy, in context? | Decision model, on-prem deployable (open-weight, Jev-compatible API preferred); hosted Jev / OpenAI Decisions API as cloud options |
| **L2** | Afterwards, with session history: risky chain, false positive, policy gap? | LLM analyst (gpt-oss-safeguard) in an asynchronous worker; produces analyses/findings, never changes decisions |

Full design: [`docs/architecture/overview.md`](docs/architecture/overview.md).

UMAI ships as **cloud SaaS** (multi-tenant, EU and US regions) and **self-hosted**, in
**Community** and **Enterprise** editions ([ADR-0018](docs/adr/0018-saas-and-self-hosted-editions.md)).
One release serves all of them: editions differ only in entitlements loaded as data, so never
branch code on an edition name; check the entitlement (limit or feature) instead
([`docs/architecture/deployment-and-editions.md`](docs/architecture/deployment-and-editions.md)).

**Status: standalone R&D.** This is a completely separate project from `umai-platform`; there
is no connection between them for now. Do not read from, import from, call, or modify
`umai-platform`, `umai-service`, or `umai-engine`, and do not add references to them.
Its only client is the console in the separate `umai-control-center` repository, which calls
the HTTP API from its server side with a bearer token
([ADR-0016](docs/adr/0016-control-center-separate-repo.md)).
The contract the engine must eventually honour is recorded in
[`docs/platform-context/`](docs/platform-context/) so the future cutover stays possible.

## Non-negotiable rules

1. **Raw PII never leaves L0.** Downstream layers, logs, metrics, traces, exceptions, eval
   reports and provider calls receive redacted text, entity types, spans and salted hashes
   only. If you are about to pass `text` to anything outside `l0/`, stop and use the
   redacted form. See [ADR-0005](docs/adr/0005-no-raw-pii-beyond-l0.md). The one other
   raw-content zone is the gateway forwarder (`gateway/`): it sends raw content only back to
   the caller and to the route's upstream, after the guardrail allowed it, and keeps it in
   memory for one call ([ADR-0035](docs/adr/0035-raw-content-zone-l0-and-gateway.md)).
2. **Synthetic data only.** Never commit real customer data, real personal data or real
   identifiers. Test vectors for Turkish IDs come from
   [`docs/reference/turkish-identifiers.md`](docs/reference/turkish-identifiers.md).
3. **Models classify, code authorizes.** Permission and enforcement logic lives in
   deterministic code (L1a, decision merge). A model output is an input to that code, never
   the authority itself.
4. **No silent fail-open.** Every failure path maps to an explicit, configured behaviour and
   is recorded. See [`docs/architecture/decision-merge-and-failure.md`](docs/architecture/decision-merge-and-failure.md).
5. **Contracts before code.** Changing a request, response, tag or schema shape means
   updating the contract document in [`docs/contracts/`](docs/contracts/) and its version
   first, in the same change.
6. **Measure, don't assume.** Every layer has a latency budget. Do not add a dependency,
   cache, model or layer without an eval or benchmark showing it is needed.
7. **Vendor facts are newer than your training data.** Jev, the OpenAI Decisions API and
   recent Presidio / gpt-oss-safeguard details may post-date your knowledge cutoff. Use
   [`docs/reference/`](docs/reference/) and re-verify on the web before relying on an API
   shape. Never invent an endpoint, field or parameter. If the reference says
   "unverified", treat it as unknown.
8. **Policies are data, loaded live.** All policy comes from signed bundles through the bundle
   store (ADR-0012) and must be replaceable at runtime without a restart, because users will
   edit and publish policies from a dashboard (ADR-0013). No policy defaults in code, no
   policy caches outside the store, and every decision records bundle id, version and hash.
9. **Decisions are recorded.** A choice between alternatives (library, provider, schema,
   behaviour) gets an ADR in [`docs/adr/`](docs/adr/).

## Stack and conventions

- Python 3.12, FastAPI, Pydantic v2, httpx (async), pytest, ruff.
- Source lives under `src/umai_decision_engine/`; layout in
  [`docs/architecture/code-layout.md`](docs/architecture/code-layout.md).
- Primary development OS is Windows (PowerShell). Keep scripts and paths cross-platform;
  use `pathlib`, never hard-coded `/` or `\\` separators.
- Async everywhere on the request path. No blocking I/O in the pipeline.
- Every external call (decision model, L2 model, session store) has an explicit timeout
  taken from the policy bundle or settings, never a library default.
- Logging is structured and never contains raw content. Log ids, entity types, tags,
  latencies and decisions.
- Code, identifiers, docs and commit messages are in English.

## Commands

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m pytest -q                      # unit + integration tests, no network
ruff check . ; ruff format --check .
uvicorn umai_decision_engine.api.app:app --reload --port 9100   # reads policies/ and .env
uvicorn umai_decision_engine.control_plane.app:app --port 9200  # policy service (ADR-0022)
uvicorn umai_decision_engine.gateway.app:app --port 9300        # UMAI gateway (ADR-0034); gateways: gateways/<tenant>.yaml
python dev/local_stack.py engine | policy-service | console | gateway  # local stack (shared dev settings)
python -m umai_decision_engine.policy.cli validate policies/<bundle_id>/<version>
python -m umai_decision_engine.policy.cli test policies --require-tests   # golden tests (ADR-0015)
python -m umai_decision_engine.policy.cli keygen | sign | verify  # bundle signing (ADR-0012)
python -m umai_decision_engine.db.migrate <database url>         # tenant store migrations (ADR-0025)
python -m evals.run --suite l0-pii-tr --split dev --tag <label>  # L0 accuracy (also: --suite leak)
python -m evals.run --suite gateway-latency --tag <label>        # gateway cost per call (also: masking-survival, live)
docker build -t umai-decision-engine .                            # one image: engine API, policy service, gateway
az deployment group create -g <rg> -f deploy/azure/main.bicep -p deploy/azure/main.bicepparam  # starter VM (ADR-0032)
deploy/vm/ship.sh <user@host>                                    # build locally, ship images to the VM
deploy/vm/umai.sh init | up | backup | status                     # on the VM; runbook: deploy/vm/README.md
```

Not available yet: `python -m pytest -m live` (no live providers before L1b) and eval suites
other than `l0-pii-tr` and `leak`. Do not invent replacements; update this section when
commands change.

Implemented so far (2026-10-17): contracts for the evaluation API, policy bundle, L0
output (incl. policy-bundle 0.6: guardrail policies with roles and sensitivity, custom
recognizers, allowed domains; evaluation API 0.5: decision provenance) and policy golden tests; bundle store (directory source, Ed25519, hot reload, active pointer); L0 core
entities, the full L0 entity catalog (ADR-0017: Lakera parity, Türkiye, Europe, secrets; 27
types) and the L0 gate (ADR-0014: a detection is terminal, L1 skipped); shared condition
evaluator (`rules/`); `/v1/evaluate`, `/v1/detect` (dev), `/v1/bundles`, `/v1/bundles/reload`,
health; bearer-token auth on `/v1` (ADR-0016); the policy service (ADR-0022: drafts, golden
tests with accepted changes, publish, activate, rollback); eval harness with the `l0-pii-tr` and `leak`
suites; golden-test runner (`cli test`) and CI (`.github/workflows/ci.yml`: ruff, pytest,
golden tests on Linux and Windows). L1 is not implemented: input without detected entities, evaluated against a bundle
with L1 rules or policies, is answered with `on_evaluation_failure` and `L1_NOT_IMPLEMENTED`.
Tenancy (2026-10-18): per-tenant API keys with scopes and rate limits at admission (ADR-0023:
`tenancy/`, contract `credentials` 0.1, evaluation API 0.6; shared token kept for self-hosted),
key management in the policy service (`/v1/tenants`, `/v1/tenants/{id}/keys`, signed registry
snapshots), the tenant store `db/` (SQLAlchemy async + Alembic; Postgres row-level security
with FORCE and `WITH CHECK`, SQLite for development; isolation tests in `tests/db/`, run in CI
against a Postgres service container). Slice (b) groundwork (2026-10-19): bundles are tenant-owned (policy-bundle 0.8:
`policies/tenants/<tenant>/`, signed `manifest.tenant_id`, unowned bundles belong to the default
tenant; evaluation API 0.7 reads one tenant; 0.8: 422 bodies without the caller's input, gap L1
closed; 0.9: 413 `input_too_large` above 32 000 characters, gap T2 closed), the policy service verifies on-behalf-of assertions
(credentials 0.2, `control_plane/actors.py`) and authorizes every route with the permission
matrix (`control_plane/authz.py`, ADR-0029). Organizations (2026-10-19, ADR-0033 store first):
contract `organizations` 0.2, `db/organizations.py` (users, memberships with `member_self`,
invitations; migration 0002), policy-service routes `/v1/organizations`, `/v1/me/organizations`,
`/v1/invitations/accept`; roles read from the store; starter guardrail per organization. Bundles
move into the tenant store with `http_pull` later (ADR-0025 §8).
Deployment (2026-10-17): single-VM starter on Azure (ADR-0032: `Dockerfile`, `deploy/vm/` Docker
Compose with Caddy, `deploy/azure/main.bicep`); the console image lives in `umai-control-center`.
Slice (c) (2026-10-19): `umai-beta` runs organizations with Auth0 sign-in (the console's
application is Terraform in `umai-control-center/identity/auth0`); `umai.sh` generates the
session secret, the console's assertion key and the tenant HMAC master key (ADR-0032 amendment:
keys for runtime-created tenants are derived on single-VM installs) and takes external secrets
with `umai.sh set-secret`; Caddy's basic auth is a switch (`UMAI_CONSOLE_BASIC_AUTH`), off on beta.
SaaS readiness (2026-10-17): threat model, gap analysis, launch plan and eight proposed ADRs in
`docs/security/` and `docs/adr/0023`–`0031` (0031: UMAI Cloud runs on Azure); `tests/readiness/`
measures the open gaps (xfail).
UMAI gateway (2026-10-19/20, ADR-0034 to ADR-0038, contract `gateway-api` 0.9): `gateway/` on port
9300 speaks OpenAI Chat Completions, the Responses API, embeddings and `/v1/models`; per-tenant
gateways (ADR-0038: one provider connection and one guardrail each, models pass through, optional
aliases and allow-list; YAML files, also managed through the policy service's `/v1/gateways`)
with `pass_through` / `secret` / `none` credentials and fallbacks; keys bind to a gateway
(credentials 0.5, tenant-store migration 0003); input, tool-result, answer and
tool-call checks through the in-process pipeline, per-call placeholders restored for the caller,
streaming modes `window` / `buffer` / `after`; Compose profile `gateway` on the single VM
(`docs/workflow/gateway.md`). Prompt-attack detection (gateway WP4) is paused until L1: groundwork
in `l1/attacks/` (not wired in) and the `pi-tr` eval suite. `umai-beta` runs the gateway since
2026-10-20 (enforcing; OpenAI with the caller's provider key).
Tenants are organizations created in the console; API keys, bundles and HMAC keys are per tenant.
The console signs people in with OIDC (Auth0 development tenant, `umai-control-center`, locally
since 2026-10-18 and on `umai-beta` since 2026-10-19).

## Where to look

| Task | Read first |
|---|---|
| Understand the system | [`docs/architecture/overview.md`](docs/architecture/overview.md) |
| Work on L0 / add a recognizer | [`docs/architecture/l0-entity-detection.md`](docs/architecture/l0-entity-detection.md), [`docs/architecture/l0-entity-catalog.md`](docs/architecture/l0-entity-catalog.md), skill `add-pii-recognizer` |
| Work on L1 rules or decision models | [`docs/architecture/l1-policy-decision.md`](docs/architecture/l1-policy-decision.md), skill `add-decision-provider` |
| Work on L2 | [`docs/architecture/l2-llm-analyst.md`](docs/architecture/l2-llm-analyst.md) |
| Change a request/response/tag shape | [`docs/contracts/`](docs/contracts/), skill `change-contract` |
| Sign-in, onboarding, organizations, invitations, login branding | [`docs/architecture/sign-in-and-onboarding.md`](docs/architecture/sign-in-and-onboarding.md), ADR-0033, ADR-0024 |
| UMAI secure gateway, guardrail hooks in other gateways | [`docs/architecture/ai-gateway.md`](docs/architecture/ai-gateway.md), [`docs/contracts/gateway-api.md`](docs/contracts/gateway-api.md), ADR-0034 to ADR-0036, [`docs/reference/ai-gateways.md`](docs/reference/ai-gateways.md) |
| SaaS vs self-hosted, editions, quotas, tenancy | [`docs/architecture/deployment-and-editions.md`](docs/architecture/deployment-and-editions.md), ADR-0018, ADR-0019 |
| Policy bundles, loading, hot reload | [`docs/contracts/policy-bundle.md`](docs/contracts/policy-bundle.md), [`docs/architecture/policy-lifecycle.md`](docs/architecture/policy-lifecycle.md) |
| Change a policy, write golden cases | [`docs/workflow/policy-authoring.md`](docs/workflow/policy-authoring.md), [`docs/contracts/policy-tests.md`](docs/contracts/policy-tests.md) |
| Evals, datasets, metrics | [`docs/evals/`](docs/evals/), skill `run-evals` |
| A vendor's API or limits | [`docs/reference/`](docs/reference/), skill `verify-vendor-facts` |
| Security, tenancy, SaaS readiness (threat model, gaps, launch stages, Azure) | [`docs/security/`](docs/security/), ADR-0023 to ADR-0031, `tests/readiness/` |
| Why something is the way it is | [`docs/adr/`](docs/adr/) |
| What the old engine did | [`docs/platform-context/`](docs/platform-context/) |
| What is undecided | [`docs/open-questions.md`](docs/open-questions.md) |
| Terms (TR/EN) | [`docs/glossary.md`](docs/glossary.md) |

## Definition of done

- Tests added or updated; `pytest -q` and `ruff check` pass.
- A readiness test in `tests/readiness/` that starts passing has its `xfail` marker removed and
  `docs/security/gap-analysis.md` updated in the same change (a closed gap is recorded, not hidden).
- Policy golden tests pass (`cli test policies --require-tests`). A policy change comes with
  cases that show its effect and its boundary.
- No raw content in logs, fixtures or snapshots (rule 1); synthetic data only (rule 2).
- Latency-relevant changes include a benchmark or eval result.
- Docs touched by the change are updated in the same change: contracts, architecture,
  this file's commands, `docs/open-questions.md` when a question is answered.
- A new decision has an ADR.
