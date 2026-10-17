# Gap analysis: standards × the code as it is

**Status:** 2026-10-17, against commit `a7f9c21` (engine) and `8988a79` (console). Standards and
IDs: [`reference/security-standards.md`](../reference/security-standards.md). Every row names
its evidence in the code (`path:line`, line numbers as of that commit) or the absence of it.
Status: **holds** · **partial** · **missing** · **n/a** (the component does not exist yet, so
the requirement cannot be met or broken). "Closes with" points at the ADR or launch-plan item.

Executable form: `tests/readiness/test_saas_readiness.py` (C = holds, T/L/A = gap, see the
[verification plan](verification-plan.md)).

## 1. Where the code stands (facts, not judgements)

| Area | Evidence |
|---|---|
| Authentication | One static bearer token per service, constant-time compare: `api/routes.py:25-38`, `control_plane/app.py:71-79`. Refuses to start without a token outside development: `api/app.py:33`. Health routes open |
| Tenant identity | Body field `tenant_id`: `contracts/evaluation.py` (`EvaluationRequest.tenant_id`); never compared with the credential |
| Per-tenant secrets | HMAC key per tenant from env JSON, deterministic dev fallback: `settings.py:33`, `Settings.tenant_key`; signing key from a file into memory: `settings.py:44`, `control_plane/app.py` (lifespan); `cli keygen` prints the private key: `policy/cli.py:42` |
| Storage | Bundles as files (`policy/sources.py`), drafts in SQLite (`control_plane/drafts.py:26`), change log as JSON lines (`control_plane/repository.py`, `_log`), no decision-event store (`pipeline/orchestrator.py`, `review_queued=False`) |
| Logging | `logging.basicConfig` plain text (`telemetry/logging.py:8-11`); decision log carries ids and numbers only (`orchestrator.py:283-296`); Presidio logger clamped to WARNING |
| Error handling | Exceptions mapped to error kinds (`orchestrator.py:_l0_error_kind`); FastAPI's default 422 handler echoes the offending input (no custom handler in `api/app.py`) — confirmed by readiness test L1 |
| Limits | Regex timeout per pattern (`l0/recognizers/base.py:21`, default 1 s); no body-size, scan-length or rate limit in either service; the console caps playground text at 32 000 characters (`src/lib/engine-request.ts:14`) but the engine does not |
| Integrity | SHA-256 + Ed25519 manifests, required outside development (`policy/manifest.py:40-70`, `settings.py:27`) |
| Console | No authentication, no session, no roles (`src/app/(console)/layout.tsx`); engine and control tokens server-side only (`src/lib/upstream.ts:27-37`); `poweredByHeader: false`, no other security headers (`next.config.mjs`) |
| Supply chain | CI: ruff, pytest, golden tests (`.github/workflows/ci.yml`); no dependency audit, SBOM, image build or signing; no Dockerfile in either repository |

## 2. OWASP ASVS 5.0, Levels 1 and 2

### Authentication and credentials (V6, V7.2, V9, V11, V13.2)

| ID | L | Requirement (paraphrase) | Status | Evidence / gap | Closes with |
|---|---|---|---|---|---|
| 7.2.1 | 1 | Validate every API token through a trusted backend | holds | `routes.py:25-38` | — |
| 7.2.2 | 1 | Dynamically generated reference tokens, not static API secrets | **missing** | one static token for all callers | ADR-0023 |
| 7.2.3 | 1 | Reference tokens unique, CSPRNG, ≥ 128 bits | n/a | tokens are operator-chosen strings | ADR-0023 (32 random bytes) |
| 7.2.4 | 1 | New token on re-authentication, old one ended | n/a | no sessions | ADR-0024 |
| 6.5.2 | 2 | Lookup secrets stored hashed | **missing** | token compared in clear from settings (`SecretStr` in memory) | ADR-0023 (SHA-256 of a 256-bit secret) |
| 11.5.1 | 2 | Random values from a CSPRNG, ≥ 128 bits | partial | `event_id` uses `uuid4` (fine as id, not as secret); no secret generation exists | ADR-0023 |
| 13.2.1–13.2.3 | 2 | Backend-to-backend auth with individual service accounts, least privilege, no default credentials | **missing** | console → services with one shared token each; no acting-user identity | ADR-0023 (service credential + on-behalf-of assertion) |
| 6.3.2 | 1 | No default accounts | holds | none exist | — |
| 6.1.1 / 6.3.1 | 1 | Documented and implemented anti-brute-force on authentication | **missing** | no rate limit on token checks | ADR-0023 §7 |
| 6.3.3 | 2 | MFA for console access | **missing** | no login | ADR-0024 §3 |
| 9.1.1–9.1.3, 9.2.1 | 1 | Verify signature, algorithm allowlist, trusted keys, `nbf`/`exp` on self-contained tokens | n/a | no JWTs yet; bundle manifests already verify signature with known keys (`manifest.py`) | ADR-0023 (assertion), ADR-0027 (registry) |
| 9.2.3 | 2 | Audience allowlist | n/a | — | ADR-0023 |
| 6.5.6, 13.1.4, 13.3.4 | 3 (we treat as 2) | Credential revocation, secret rotation | **missing** | rotating the token means reconfiguring every caller | ADR-0023 §6, ADR-0027 §4 |

### Sessions and browser (V3, V7) — console

| ID | L | Requirement | Status | Evidence / gap | Closes with |
|---|---|---|---|---|---|
| 7.1.1, 7.1.2, 7.3.1, 7.3.2 | 2 | Documented and enforced inactivity timeout, absolute lifetime, concurrent-session limit | **missing** | no sessions | ADR-0024 §2 |
| 7.4.1, 7.4.2 | 1 | Terminated sessions unusable; all sessions end when an account is disabled | **missing** | — | ADR-0024 §2 |
| 7.4.3–7.4.5, 7.5.1, 7.5.2 | 2 | End other sessions, visible logout, admin termination, re-auth before sensitive changes | **missing** | — | ADR-0024 §2 |
| 3.3.1–3.3.4 | 1/2 | Cookie `Secure`, `__Host-`, `SameSite`, `HttpOnly` | n/a | no cookies | ADR-0024 §2 |
| 3.5.1 | 1 | CSRF protection on state-changing requests | **missing** | relay routes accept any origin once sessions exist; today they are simply open | ADR-0024 §2 |
| 3.4.1 | 1 | HSTS ≥ 1 year | **missing** | no headers configured (`next.config.mjs`) | launch plan S0 |
| 3.4.2 | 1 | Fixed/allowlisted CORS origin | holds | no CORS middleware in FastAPI (same-origin only); console relays server-side | — |
| 3.4.6 | 2 | CSP `frame-ancestors` | **missing** | — | launch plan S0 |

### Authorization (V8, V15.3)

| ID | L | Requirement | Status | Evidence / gap | Closes with |
|---|---|---|---|---|---|
| 8.1.1 / 8.1.2 | 1/2 | Documented function- and data-level (and field-level) access rules | **missing** | no rules document; this analysis and ADR-0029 are the first | ADR-0029 §3 |
| 8.2.1 | 1 | Function-level access only for permitted consumers | **missing** | `POST /v1/bundles/reload` callable by any caller | ADR-0023 scopes |
| 8.2.2 | 1 | Object-level access (no IDOR / BOLA) | **missing** | any token reads any bundle (`GET /v1/bundles/{id}/versions/{v}`), any guardrail draft | ADR-0023, ADR-0025 |
| 8.2.3 | 2 | Field-level access | n/a | — | with roles |
| 8.3.1 | 1 | Enforce in a trusted service layer, not the client | partial | engine decides policy in code (good); console actions are not authorized anywhere | ADR-0029 §1 |
| 8.4.1 | 2 | Multi-tenant cross-tenant controls | **missing** | `tenant_id` from the body; readiness T1 red | ADR-0023, ADR-0025 |
| 15.3.1 | 1 | Return only necessary fields | holds | response models with `exclude_none`; bundle content is policy data only | — |
| 15.3.3 | 2 | Restrict writable fields (mass assignment) | holds | Pydantic models with `extra="forbid"` on contracts (422 on unknown fields: `tests/integration/test_api.py::test_malformed_request_is_422`) | — |
| 15.3.4 | 2 | Client IP from trusted headers only | n/a | no IP use yet | ADR-0030 (audit `ip`) |

### Logging and error handling (V16)

| ID | L | Requirement | Status | Evidence / gap | Closes with |
|---|---|---|---|---|---|
| 16.1.1 | 2 | Log inventory per layer (events, formats, storage, retention) | partial | `docs/architecture/overview.md` principles; no inventory | ADR-0030 §6 |
| 16.2.1–16.2.4 | 2 | When/where/who/what, UTC, documented sinks, correlatable format | partial | request id and ids in messages; plain text, no `who` | ADR-0030 §6 |
| 16.2.5 | 2 | Sensitive data never logged; hash or mask | **holds** | leak suite, readiness C1; Presidio logger clamped | keep; extend to traces |
| 16.3.1–16.3.4 | 2 | Log authentication attempts, failed authorization, control bypasses, unexpected errors | partial | `l0.failed`, `config_failure` logged; 401s not logged; no authorization exists | ADR-0030 §1 |
| 16.4.1–16.4.3 | 2 | Encode log data, protect logs, ship to a separate system | **missing** | stdout only; no pipeline | launch plan S0 |
| 16.5.1 | 2 | Generic error messages, no internals | partial | mapped error kinds; **422 echoes input** (readiness L1 red) | ADR-0030 §6 |
| 16.5.2, 16.5.3 | 2 | Fail securely, no fail-open | **holds** | failure policy explicit (`pipeline/failure.py`), `L1_NOT_IMPLEMENTED` never allows silently | — |

### Data protection and cryptography (V11, V12, V14)

| ID | L | Requirement | Status | Evidence / gap | Closes with |
|---|---|---|---|---|---|
| 14.1.1 / 14.1.2 | 2 | Classify sensitive data; document per-level protection | partial | ADR-0005 defines the raw-content rule; the [threat model](threat-model.md) §2 is the first classification | ADR-0025, ADR-0030 |
| 14.2.1 | 1 | Sensitive data only in body/headers, never URL | holds | all content in POST bodies | — |
| 14.2.2 | 2 | Intermediaries must not cache sensitive data | **missing** | no `Cache-Control` on responses | launch plan S0 |
| 14.2.3 | 2 | No sensitive data to untrusted third parties | holds | no providers yet; design gates them by region (ADR-0019 §5) | ADR-0026 |
| 14.3.2 / 14.3.3 | 2 | Anti-caching headers; no sensitive data in browser storage | partial | playground keeps results in React state only (console AGENTS rule 2); no `no-store` header | ADR-0024 §2 |
| 11.3.2 / 11.3.3 | 1/2 | Approved ciphers, authenticated encryption | n/a | nothing encrypted at rest yet | ADR-0025 §4 (AES-256-GCM) |
| 11.1.1 / 11.1.2 | 2 | Key-management policy and inventory | **missing** | keys in files and env | ADR-0027 |
| 12.1.1, 12.3.1–12.3.3 | 1/2 | TLS 1.2+ everywhere, cert validation, internal traffic encrypted | **missing** | processes serve plain HTTP; no ingress exists | launch plan S0 |

### Configuration, supply chain, anti-automation (V2, V4, V13, V15)

| ID | L | Requirement | Status | Evidence / gap | Closes with |
|---|---|---|---|---|---|
| 13.3.1 / 13.3.2 | 2 | Secrets in a secrets-management solution, least privilege | **missing** | `.env` files and env JSON | ADR-0027 §7 |
| 13.4.2 | 2 | Debug modes off in production | holds | `expose_detect_endpoint` and inline bundles default off outside development (`settings.py`) | — |
| 13.4.5 | 2 | Documentation/monitoring endpoints not exposed unintentionally | partial | FastAPI serves `/docs` and `/openapi.json` by default on both services | launch plan S0 (disable outside development) |
| 13.2.4 / 13.2.5 | 2 | Egress allowlists | n/a | no outbound calls yet | ADR-0026 §4 |
| 15.1.2 | 2 | Dependency inventory (SBOM) | **missing** | — | launch plan S0 |
| 15.2.1 | 1 | Components updated within documented timeframes | **missing** | no Dependabot, no audit | launch plan S0 |
| 15.2.3 | 2 | No test/dev features in production | holds | development gates above | — |
| 4.1.1 | 1 | Correct `Content-Type` | holds | FastAPI JSON responses | — |
| 2.2.2 | 1 | Server-side input validation | holds | Pydantic contracts, bundle validation | — |
| 2.1.3, 2.3.2, 2.4.1 | 2 | Documented and implemented business limits; anti-automation | **missing** | no quota, size or rate limit; readiness T2 red | ADR-0019 §4, ADR-0023 §7, ADR-0028 |
| 15.1.3 / 15.2.2 | 2 | Resource-intensive functions documented and protected | partial | regex timeout exists; scan length unbounded | ADR-0019 §4 |

ASVS tally over the rows above: **holds 14 · partial 9 · missing 29 · n/a 10**.

## 3. SOC 2 common criteria CC6–CC8

| Criterion | What an auditor would ask to see | Status | Gap | Closes with |
|---|---|---|---|---|
| CC6.1 logical access over protected assets | Who can call what, how identities are managed, how keys are protected | **missing** | shared tokens; keys in files; no console login | ADR-0023, 0024, 0027 |
| CC6.2 register/authorize users; remove when invalid | Member lifecycle, SCIM or manual deprovisioning, key revocation | **missing** | no users, no revocation | ADR-0024, ADR-0023 §6 |
| CC6.3 role-based access, least privilege, segregation of duties | Role matrix, four-eyes on production changes, access reviews | **missing** | no roles | ADR-0029 |
| CC6.4 / CC6.5 physical access, disposal | Cloud provider's reports; data erasure procedure | n/a (company) | provider SOC reports to collect; erasure design exists | ADR-0025 §9, launch plan |
| CC6.6 protection against external threats | TLS, ingress, rate limiting, WAF | **missing** | none deployed | launch plan S0 |
| CC6.7 restrict transmission/removal of information | Content never leaves L0; exports controlled; encryption in transit | partial | ADR-0005 holds in code; no TLS, no export controls yet | ADR-0030, S0 |
| CC6.8 malicious software prevention/detection | Dependency scanning, image scanning, signed images | **missing** | — | launch plan S0 |
| CC7.1 detect configuration changes and vulnerabilities | Audit events, IaC drift detection, vulnerability scanning | **missing** | change log without actor (readiness A1) | ADR-0030, S0 |
| CC7.2 monitor for anomalies | Metrics, alerts, usage anomalies | **missing** | no metrics pipeline | ADR-0028 §7, S0 |
| CC7.3 / CC7.4 evaluate events; incident response program | IR plan, on-call, runbooks, status page | **missing** | — | launch plan S0 (company + ops) |
| CC7.5 recovery | Backups, restore drill | **missing** | nothing to back up yet; drill defined | ADR-0025, verification plan |
| CC8.1 change management | PR review, CI gates, approvals, release signing | partial | CI with tests and golden gates; no branch protection evidence, no release process | launch plan S0 |

## 4. OWASP API Security Top 10 (2023)

| Item | Status | Evidence / gap |
|---|---|---|
| API1 Broken object-level authorization | **missing** | any token reads any bundle version or guardrail draft |
| API2 Broken authentication | **missing** | static shared secret, no rotation or revocation |
| API3 Broken object property-level authorization | holds | `extra="forbid"` contracts; response models exclude internals |
| API4 Unrestricted resource consumption | **missing** | no body, scan-length or rate limits (readiness T2) |
| API5 Broken function-level authorization | **missing** | `reload` and publish/activate open to any token holder |
| API6 Unrestricted access to sensitive business flows | **missing** | publish → activate without approval or actor |
| API7 SSRF | holds | no user-supplied URLs are fetched (allowed domains are matched, not fetched) |
| API8 Security misconfiguration | partial | dev gates good; `/docs` exposed; no TLS/headers |
| API9 Improper inventory management | holds | one versioned contract, OpenAPI generated, dev endpoints gated |
| API10 Unsafe consumption of APIs | n/a | no upstream APIs yet; provider outputs are inputs to code by rule 3 |

## 5. OWASP Top 10 for LLM Applications (2026), where it touches this product

| Item | Relevance | Status |
|---|---|---|
| LLM02 Sensitive information disclosure | The product's purpose; also our own logs/telemetry | holds in code (ADR-0005, leak suite); 422 echo is the exception |
| LLM06 Unbounded consumption | Our engine must not be the unbounded component | **missing** (limits) |
| LLM08 Hidden context exposure | Policy bundles and exceptions are "hidden context" for a customer's agents | **missing** (bundles readable by any token holder) |
| LLM04 Supply chain | Models (future L1b/L2), Presidio, dependencies | **missing** (no SBOM/audit) |
| LLM01 Prompt injection | Content is data to L0; no model on the request path today | holds by design until L1b arrives |

## 6. Documentation versus code

| # | Statement | Reality | Action |
|---|---|---|---|
| D1 | `.env.example` documents `UMAI_DE_DECISION_PROVIDERS`, `L2_MODELS`, `EVENT_SINK`, `EVENT_DB`, `L2_WORKER_CONCURRENCY`, `L2_CALL_TIMEOUT_S`, `SESSION_STORE`, `REDIS_URL` | `Settings` has none of them (future phases); `UMAI_DE_POPULAR_DOMAINS_FILE` is read by `os.getenv` in `l0/recognizers/network.py:171`, outside `Settings` | Mark future settings as such in `.env.example`; move the domains file into `Settings` when touched |
| D2 | `contracts/evaluation.py` docstring: "v0.3" | `CONTRACT_VERSION = "0.5"` | Fix the docstring |
| D3 | `deployment-and-editions.md`: "HMAC keys … per tenant (already the case)" | True for the hashing code; production has no key management — one env JSON map, no generation, wrapping or rotation | ADR-0027 §5 |
| D4 | Tagged-action contract 0.2 describes the stored decision event | No sink exists; `review_queued` is always `False` | ADR-0030; note in the contract's status line |
| D5 | `docs/README.md` map: "roadmap … phases F0–F5" | Roadmap has F6 | Fixed in this change |
| D6 | Evaluation API contract: errors are 401 and 422 only | Correct for the code; ADR-0019's 403/413/429 are design, not contract | Contract 0.6 lands with ADR-0023/0028 code |
| D7 | Console AGENTS.md rule 1: "the browser never calls the engine" | Holds; but nothing authenticates the browser to the console | ADR-0024 |

## 7. Summary

The engine's **content handling is where a security product should be**: raw PII stays in L0,
failure modes are explicit, bundles are signed, development features are gated. Everything
that makes it a *multi-tenant service* — identity, tenancy, storage, limits, audit, transport,
supply chain — is missing, by the project's own account (ADR-0018 "what the engine does
today"). The 29 missing ASVS rows collapse into eight decisions (ADR-0023 to ADR-0030), the Azure
mapping of ADR-0031, and a short list of deployment work (TLS at Application Gateway, headers,
limits, CI scanning) in the launch plan's stage 0.
