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
uvicorn umai_decision_engine.gateway.app:app --port 9300        # UMAI gateway (ADR-0034); applications/<tenant>.yaml
python dev/local_stack.py engine | policy-service | console | gateway  # local stack (shared dev settings)
python -m umai_decision_engine.policy.cli validate policies/<bundle_id>/<version>
python -m umai_decision_engine.policy.cli test policies --require-tests   # golden tests (ADR-0015)
python -m umai_decision_engine.policy.cli keygen | sign | verify  # bundle signing (ADR-0012)
python -m umai_decision_engine.db.migrate <database url>         # tenant store migrations (ADR-0025)
python -m evals.run --suite l0-pii-tr --split dev --tag <label>  # L0 accuracy (also: --suite leak)
python -m evals.run --suite gateway-latency --tag <label>        # gateway cost per call (also: masking-survival, live)
python -m evals.run --suite admission --tag <label>              # admission cost per request (ADR-0046)
python -m evals.run --suite l0-latency --tag <label>             # L0 by input size + admission per step (S0-15)
python -m evals.run --suite revocation --tag <label>             # local stack: revoked key refused after (S0-2)
python -m evals.run --suite load --tag <label>                   # local stack: throughput, p50/p95/p99 (S0-3)
python -m evals.run --suite l1-cold-start --tag <label>          # live: first L1b calls of a new process (also: l1-latency)
python dev/api_docs.py openapi [--check]                         # published OpenAPI documents (ADR-0054)
python dev/api_docs.py build --out site/docs                     # customer API documentation site
docker build -t umai-decision-engine .                            # one image: engine API, policy service, gateway
az deployment group create -g <rg> -f deploy/azure/main.bicep -p deploy/azure/main.bicepparam  # starter VM (ADR-0032)
az deployment group what-if -g UMAI -f deploy/azure/main.bicep -p deploy/azure/prod.bicepparam  # umai-prod (ADR-0050); runbook deploy/azure/README.md
az deployment group what-if -g UMAI -f deploy/azure/monitoring.bicep -p deploy/azure/monitoring.bicepparam  # alerts, availability tests (ADR-0051)
deploy/vm/ship.sh <user@host>                                    # build locally, ship images to the VM
deploy/vm/umai.sh init | up [--recreate] | backup | restore-test [ARCHIVE|BLOB] | status  # on the VM; runbook: deploy/vm/README.md
```

Not available yet: `python -m pytest -m live` (no live providers before L1b) and the eval suites
`docs/evals/README.md` lists as planned (`l1-decisions`, `l1-providers`, `l1-calibration`,
`l2-chains`, `pipeline-e2e`, `l2-worker`). Do not invent replacements; update this section when
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
golden tests on Linux and Windows). L1b (2026-10-20, ADR-0040): decision policies through a
provider adapter (`l1/`; OpenAI Decisions API, `fake`), on L0-redacted input, after the L0
gate's floor (ADR-0036 in effect), with probability thresholds, fallback, failure policy and
shadow mode; `gr-demo-data-protection` v5 holds nine policies converted from `OpenAI_Decisions`,
all in shadow; golden cases stub the model (`model_answers`); live suite `l1-latency`. L1a rules
are not implemented: a bundle with L1a rules is answered with `on_evaluation_failure` and
`L1_NOT_IMPLEMENTED`. L1b cold start (2026-10-26, ADR-0040 amendment): engine and gateway warm
each provider at startup (one synthetic decision, never caller content), keep idle connections
120 s, and `l1b.timeout_ms` bounds the whole call; live suite `l1-cold-start`. L1b shadow split
(2026-10-30, ADR-0040 amendment): the request path asks only the enforcing policies; shadow
policies always run in their own call after the decision, and the evaluation's L1b usage (both
calls) is written once, by its last call; `l1-latency --modes all,split` measures it.
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
contract `organizations` 0.3, `db/organizations.py` (users, memberships with `member_self`,
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
UMAI gateway (2026-10-19/20, ADR-0034 to ADR-0039, contract `gateway-api` 0.12): `gateway/` on port
9300 speaks OpenAI Chat Completions, the Responses API, embeddings and `/v1/models`. Applications
are the protected unit (ADR-0039): one guardrail on every path, its own keys (credentials 0.6,
tenant-store migration 0004; evaluation API 0.11 takes the active guardrail from the key's application),
optional allowed providers, model names, aliases and a fallback target. The AI gateway has the
organization's providers (a built-in pass-through OpenAI provider, the default unless another is
marked; `@<provider>/<model>` picks one) with `pass_through` / `secret` / `none` credentials, and
defaults an application may override. One YAML file per tenant (`applications.py`), managed
through the policy service's `/v1/applications`, `/v1/providers` and `/v1/gateway/settings`;
input, tool-result, answer and
tool-call checks through the in-process pipeline, per-call placeholders restored for the caller,
streaming modes `window` / `buffer` / `after`; Compose profile `gateway` on the single VM
(`docs/workflow/gateway.md`). Prompt-attack detection (gateway WP4) is paused until L1: groundwork
in `l1/attacks/` (not wired in) and the `pi-tr` eval suite. `umai-beta` runs the gateway since
2026-10-20 (enforcing; OpenAI with the caller's provider key).
Records (2026-10-26/30, ADR-0041, ADR-0042, contracts tagged-action 0.5 and usage 0.3): the
engine API and the gateway store a decision event and an `evaluations` usage record per
evaluation (per check on the gateway; a stream's window re-checks collapse into its final check),
an L1b outcome record per policy (shadow included), and one usage record per gateway call
(`gateway_calls`, provider tokens) in append-only tenant tables (migrations 0006–0007), through a
bounded in-memory queue and a batched writer (`events/`; `UMAI_DE_RECORDS_SINK=database`); no
content, not even redacted. The policy service reads them (`GET /v1/decision-events`,
`GET /v1/decision-events/summary`, `GET /v1/usage` with `daily`) and deletes them after
retention (`control_plane/records.py`).
Data access (2026-10-30, ADR-0047, tagged-action 0.6, data-export 0.1, organizations 0.5): decision
search by event, request or gateway call id (`q`) and filters `entity_type`, `policy_id` on the
list and summary; `GET /v1/decision-events/export.csv` (list filters, 100 000 rows) and
`GET /v1/usage/export.csv` (a month per day and dimension), refused with 422 `export_too_large`
above the cap, never cut; owner-only `GET /v1/organizations/{id}/export.zip` (ZIP of JSON files,
250 000 records, built before sending, allow-listed fields, no secret or hash;
`control_plane/exports.py`). Erasure is not built: billing records have a statutory retention
period (Q61).
Plans and quotas (2026-10-30, ADR-0043, contract entitlements 0.1): the policy service reads the
plans file (`UMAI_DE_PLANS_FILE`, `deploy/plans.yaml`) and writes each tenant's limits, with
operator overrides (`PUT /v1/tenants/{id}/plan`, migration 0008), into its signed snapshot
(credentials 0.7); the engine API and the gateway count requests per tenant and month in memory
from the usage ledger (`tenancy/quota.py`, `events/ledger.py`) and answer 429 `quota_exceeded`
(evaluation API 0.13, gateway API 0.13); `GET /v1/entitlements` shows plan and count.
Billing (2026-10-30, ADR-0044, contract billing 0.1): the policy service sells plans with a
`billing` block (entitlements 0.2, edition `pro`) through iyzico subscriptions (`billing/`:
async client with `IYZWSv2` signing, the state machine, reconciliation, `billing.cli sync`);
billing details, hosted checkout confirmed by token, plan change, cancel at period end, 14-day
grace with retries (`/v1/billing…`), signed webhooks on the public `/hooks/iyzico`; migration 0009.
Tested against a fake iyzico; the sandbox run waits for keys.
Key network restrictions (2026-10-30, ADR-0046, credentials 0.9): a key may carry
`allowed_ips` (1–50 CIDRs, IPv4 and IPv6; set at creation, replaced with
`PATCH /v1/tenants/{id}/keys/{key_id}`, permission `key.update`, migration 0013, snapshot payload
0.5); the engine API and the gateway answer 403 `ip_not_allowed` from other addresses and 429
`too_many_failed_attempts` to a source (IPv4 address, IPv6 /64) with too many bad credentials,
before any lookup (`tenancy/network.py`, `FailedAuthThrottle`; evaluation API 0.14, gateway API
0.14). The client address is the socket peer or, behind `UMAI_DE_TRUSTED_PROXIES` only (default
none; the VM trusts Caddy at a fixed address), `X-Forwarded-For` read from the right.
Webhooks (2026-10-30, ADR-0048, contract webhooks 0.1): organizations register HTTPS or
Slack endpoints (`/v1/webhooks…`, `webhook.read` / `webhook.manage`); a worker in the policy service
(`control_plane/webhooks.py`) reads the tenant's records and sends `decision.blocked` (one event per
5-minute window), `quota.threshold` (80 %, 100 %) and `billing.*`, signed with `X-Umai-Signature`
(secrets derived from `UMAI_DE_WEBHOOK_MASTER_KEY`, URLs encrypted at rest), through an SSRF guard
that connects to the address it checked (`webhooks/`); retries 1 m–6 h, failing endpoints turned
off; migration 0012 (`webhook_endpoints`, `webhook_messages`, append-only `webhook_deliveries`).
Console sessions (2026-10-30, ADR-0024 §2 amendment, contract console-sessions 0.1, credentials
0.8): a record per console sign-in in the global table `console_sessions` (migration 0011; the
session id only as a hash, a coarse device label, no IP); the policy service refuses an ended
session on every call made for the person (401 `session_ended`; `UMAI_DE_CONSOLE_SESSIONS`
`required` in production), the console middleware checks it with `POST /v1/sessions/check` (30 s
cache, also answers membership of the active organization), and `/v1/me/sessions…` lists and ends
a person's sessions (`control_plane/sessions.py`, `db/sessions.py`).
Audit log (2026-10-30, ADR-0045, contract audit 0.1): every changing policy-service route
declares an audit action (`audit: Audit = _AUDIT[...]`) and the router's `AuditRoute`
(`control_plane/audit.py`) writes one event per call to the append-only tenant table
`audit_events` (migration 0010): `success`, or `denied` for a 403; actor as subject and user id,
before/after of non-secret fields, never secrets or billing details. `GET /v1/audit-events`
(filters, cursor) and `GET /v1/audit-events/export` (CSV) behind `audit.read` (owner, admin);
400 days (`UMAI_DE_AUDIT_RETENTION_DAYS`). A route-walk test fails for a new changing route
without an action.
Supply chain (2026-10-30, ADR-0049): `.github/workflows/supply-chain.yml` runs `pip-audit`, gitleaks
over the history (`.gitleaks.toml`: exact synthetic values only) and the image build with a Trivy
scan and a CycloneDX SBOM artifact, on pull requests, `main` and weekly; Dependabot proposes
updates weekly. Actions are pinned by SHA, scanners and base images by digest
(`tests/unit/test_supply_chain.py`); an accepted finding goes to `.trivyignore.yaml` with a
statement and an expiry at most 90 days ahead.
VM operations (2026-10-31, ADR-0052 proposed): `umai.sh up` replaces services side by side without
downtime (Caddy 2.11.7 routes by Docker DNS; network, Postgres and Caddy changes need
`up --recreate`; migrations expand, then contract); `umai.sh restore-test` restores a backup into a
scratch project and checks it (S0-14 on the VM: RPO 24 h, RTO 1 h / 4 h).
Stage 0 measurements (2026-10-31, launch plan S0-2, S0-3, S0-4, S0-15): `evals/stack.py` starts
the policy service, engine API and gateway as real processes with production settings, plus stubs
for the upstream and the L1b provider (`evals/stubs.py`); suites `revocation` (a revoked key is
refused within one 5 s registry poll), `load` (throughput and percentiles per endpoint, CPU per
request, the B2s estimate; the beta plan is `docs/workflow/load-test-beta.md`), `l0-latency` with
admission step by step, and the `leak` suite's error paths over every refusal of the three
services (also a CI test). The policy service's 422 no longer repeats the input
(`control_plane/errors.py`, organizations 0.6).
Customer API documentation (2026-10-31, ADR-0054, Proposed): `docs/api/` holds the pages in English
and Turkish and the published OpenAPI documents, generated from the apps through an allow-list
(`api/openapi.py` `PUBLIC_OPERATIONS`: `/v1/evaluate` and the gateway's four routes; nothing
internal); `dev/api_docs.py` builds the static site served at `/docs/`; `tests/unit/test_api_docs.py`
holds code, contract versions (`API_VERSION` in each app), error catalogue and examples together.
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
| Customer-facing API documentation, published OpenAPI, error catalogue | [`docs/api/README.md`](docs/api/README.md), ADR-0054 |
| Console structure, applications, how the AI gateway fits | [`docs/architecture/console-information-architecture.md`](docs/architecture/console-information-architecture.md), ADR-0039 |
| Sign-in, onboarding, organizations, invitations, login branding | [`docs/architecture/sign-in-and-onboarding.md`](docs/architecture/sign-in-and-onboarding.md), ADR-0033, ADR-0024 |
| UMAI secure gateway, guardrail hooks in other gateways | [`docs/architecture/ai-gateway.md`](docs/architecture/ai-gateway.md), [`docs/contracts/gateway-api.md`](docs/contracts/gateway-api.md), ADR-0034 to ADR-0036, [`docs/reference/ai-gateways.md`](docs/reference/ai-gateways.md) |
| Search, CSV exports, the organization archive (data portability) | [`docs/contracts/data-export.md`](docs/contracts/data-export.md), ADR-0047 |
| Webhooks, Slack, outbound calls to customer URLs (SSRF) | [`docs/contracts/webhooks.md`](docs/contracts/webhooks.md), ADR-0048, [`docs/reference/slack-incoming-webhooks.md`](docs/reference/slack-incoming-webhooks.md) |
| Billing, paid plans, iyzico | [`docs/contracts/billing.md`](docs/contracts/billing.md), ADR-0044, [`docs/reference/iyzico-subscriptions.md`](docs/reference/iyzico-subscriptions.md) |
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
