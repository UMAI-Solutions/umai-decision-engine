# Policy lifecycle: edit in a dashboard, publish live

**Status:** direction accepted ([ADR-0013](../adr/0013-policy-lifecycle-dashboard.md)). The first
control plane is built: the **policy service** ([ADR-0022](../adr/0022-policy-service-control-plane.md),
`umai_decision_engine.control_plane`) with drafts, tests, publish and activate, used by the
console's guardrail page. Library, approval and remote propagation are still to come.

## Flow

```text
POLICY CONTROL PLANE (separate service)                                ENGINE(S)
draft ──▶ validate ──▶ test ──▶ preview ──▶ approve ──▶ publish ──▶ activate ──▶ fetch + verify + atomic swap
(edit)    schema,      golden   replay last  4-eyes     signed,     pointer          (seconds)
          semantics,   cases,   N decisions  for prod   immutable   "prod = v7"          │
          provider     play-    with the                v7                               ▼
          capability   ground   new policy                                   reports "v7 active"
                                                                             dashboard: 3/3 engines on v7
roll back: move the pointer to v6 → engines return to v6
```

## Stages

| Stage | Where | What happens |
|---|---|---|
| Draft | control plane DB | Mutable; never seen by engines |
| Validate | control plane (engine library) | Same validation code the engine runs at load: schema, fact paths, question/option references, provider capabilities, regex safety |
| Test | control plane + engine | Golden cases in the version's `tests/` ([policy-tests](../contracts/policy-tests.md), ADR-0015), run by the same runner as `cli test` and CI today; playground = single input evaluated with an inline bundle (dev/test engines only, synthetic data) |
| Preview impact | engine replay job | Re-evaluate the last N stored decision events with the candidate: "38 of 1 240 decisions would change", with diffs. The L0 gate (`on_detect`, exceptions) and L1 only use entity types/counts, facts and redacted text, which events keep. Recognizer changes cannot be replayed (need raw content) → synthetic cases only |
| Approve | control plane | Roles: editor, approver; four-eyes for production; diff between versions |
| Publish | control plane | Build `bundle.yaml` + `manifest.json` (hash, Ed25519 signature) at the current bundle contract version, whatever version the draft started from; version becomes immutable |
| Activate | control plane | Move the active pointer per bundle id (and later per environment / tenant / traffic share) |
| Propagate | engine | Poll with ETag (default every ~5–10 s) and/or push signal; verify → validate → compile → atomic swap; in-flight requests finish on the old version |
| Monitor | engine + dashboard | Engines report active version and load errors; decision metrics by bundle version |
| Roll back | control plane | Move the pointer back |

Later: **shadow mode** (candidate evaluated alongside the active version on a sample;
caller gets the active decision, differences are recorded) and **staged rollout**
(test → prod, single tenant, traffic percentage).

Until the control plane exists, Git plays its role: pull request = draft + review, CI =
validate + test, the `active` file = activation. Team workflow:
[`workflow/policy-authoring.md`](../workflow/policy-authoring.md).

Policies are versioned in the library and **pinned** into each guardrail version at publish;
editing a policy never changes a published guardrail (ADR-0021).

## Policy service API (ADR-0022)

Bearer token `UMAI_DE_CONTROL_API_TOKEN` on everything but `/healthz`. The console's guardrail
pages are built on these answers; field additions are backwards compatible.

| Endpoint | Answer |
|---|---|
| `GET /v1/guardrails` | Per guardrail: `id`, `name`, `description`, `mode`, `active_version`, `versions`, `has_draft`, `policies`, `policies_on` (0 for bundles without policies), `detectors`, `last_change` (latest change-log entry) |
| `GET /v1/guardrails/{id}` | `active_version`, `versions[]` (`version`, `name`, `mode`, `contract_version`, `policies`, `signed`, `published_at`, `base_version`, `note`, `valid`, `error`), open `draft`, `history` (change log, last 50) |
| `GET /v1/catalog` | Detector types with their tag and group (Türkiye, Europe, Global, Secrets, …) |
| `POST/GET/PUT/DELETE /v1/guardrails/{id}/draft` | Start (from `base_version`, default active), read, save (saved even when invalid, returns `problems`), discard |
| `POST /v1/guardrails/{id}/draft/test` | Golden-case report of the draft |
| `POST /v1/guardrails/{id}/draft/publish` | `{activate, accept[], note}` → new version; 422 with `problems` or `unaccepted` cases |
| `POST /v1/guardrails/{id}/activate` | `{version, note}` → moves the active pointer (rollback) |
| `GET/POST /v1/tenants`, `GET /v1/tenants/{id}` | Tenants (global table, ADR-0025): `{id, name?, region?}`; 409 when it exists |
| `GET/POST /v1/tenants/{id}/keys` | API keys (ADR-0023): create `{name, scopes?, env?, expires_at?}` → `{key, secret, published}` (secret shown once); list records |
| `DELETE /v1/tenants/{id}/keys/{key_id}` | Revoke; effective at engines within one poll interval |
| `POST /v1/tenants/{id}/keys/publish` | Rewrite the tenant's signed registry snapshot |

Drafts are tenant-scoped (Postgres with row-level security in SaaS, SQLite otherwise, ADR-0025);
the tenant a request acts for is the `X-Umai-Tenant` header, else `UMAI_DE_DEFAULT_TENANT_ID`.
Until console login (ADR-0024) exists the control token is an operator credential that may act
for any tenant. Published guardrail versions stay on the shared directory source (self-hosted
format); tenant-owned bundles in the store are the ADR-0025 §8 follow-up.

`published_at`, `base_version` and `note` come from the publish entry in `changelog.jsonl`; they
are empty for versions added through Git. `signed` means the version's manifest carries a
signature.

## Engine requirements (keep these true from the first commit)

| Requirement | Status |
|---|---|
| Bundles loaded only through `BundleSource` + bundle store; no other policy cache | F1 |
| Atomic swap, last-known-good on failure, disk cache | F1 (directory source) |
| Status endpoint `GET /v1/bundles`: loaded versions, active pointer, hash, load errors | F1 |
| Every decision carries bundle id, version, content hash | F1 |
| Inline-bundle evaluation (`bundle_inline`) for playground, disabled in production | F1 |
| Golden-test runner through the real pipeline; CI gate on every change (ADR-0015) | done (`cli test`, `.github/workflows/ci.yml`) |
| Replay endpoint/job over decision events | after F3 |
| Shadow evaluation | later |
| `http_pull` source + push signal | with the control plane |

## Open questions

- Who signs on-prem: the customer's control plane with its own key (fits "users edit
  policies") or UMAI-signed bundles imported as files.
- Which safety steps are in the first dashboard version (proposal: playground, golden tests,
  impact preview, rollback; shadow and staged rollout later).
