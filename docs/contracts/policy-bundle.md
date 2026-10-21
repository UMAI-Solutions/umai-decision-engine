# Contract: policy bundle

**Version:** 0.9

The configuration the engine evaluates against (successor of `umai-engine`'s
`GuardrailSnapshot`). A bundle is a signed, immutable artifact; the engine only reads it
([ADR-0012](../adr/0012-signed-immutable-policy-bundles.md)). Bundles are produced by hand
in R&D and by the policy control plane later ([ADR-0013](../adr/0013-policy-lifecycle-dashboard.md)).

## Artifact layout (directory source)

```text
policies/
├── tenants/                          # tenant-owned bundles (0.8)
│   └── acme/                         # tenant id
│       └── gr-default/1/…            # same layout as below
└── gr-demo-data-protection/          # bundle id (owned by the default tenant)
    ├── active                       # text file: the active version, e.g. "3"
    ├── 2/
    │   ├── bundle.yaml
    │   └── manifest.json
    └── 3/
        ├── bundle.yaml
        ├── manifest.json
        └── tests/                   # golden cases, not signed (policy-tests.md, ADR-0015)
```

`manifest.json`:

```json
{
  "bundle_id": "gr-demo-data-protection",
  "version": 3,
  "tenant_id": "tenant-demo",
  "contract_version": "0.3",
  "engine": ">=0.1,<1.0",
  "created_at": "2026-10-16T09:00:00Z",
  "sha256": "<hex sha256 of bundle.yaml bytes>",
  "key_id": "tenant-demo-2026",
  "signature": "<base64 Ed25519 signature over the canonical JSON of this manifest without 'signature'>"
}
```

Rules: `bundle_id`/`version` must match the directory and `bundle.yaml`; `sha256` must match
the **exact bytes** of `bundle.yaml` (line endings included — `.gitattributes` disables EOL
conversion under `policies/`, otherwise Git on Windows breaks signed bundles); `signature` is verified with the public key `key_id` from settings and is
required when `settings.require_signed_bundles` is true. In development an unsigned bundle may
omit `manifest.json`; the engine then computes the hash itself and marks the bundle `unsigned`.

**Ownership (0.8, ADR-0019 §2).** A bundle belongs to one tenant. Under `tenants/<tenant_id>/`
the directory names the owner; directly under the root, the owner is the deployment's default
tenant (`UMAI_DE_DEFAULT_TENANT_ID`), which keeps single-tenant installs and existing layouts
working. `manifest.tenant_id` is signed with the rest and, when present, must equal the owner
from the layout, otherwise the version does not load. The store resolves `(tenant, bundle id)`
only, so two tenants may use the same bundle id. Tenant directory names follow the tenant id
pattern (`a-z 0-9 -`); `tenants` cannot be used as a bundle id. The policy service writes a
tenant's versions under its directory and stamps `tenant_id`. It writes every version from the
bundle model, with every field of the current contract, and stamps that version in `bundle.yaml`
and the manifest (`contracts/bundle.py` `CONTRACT_VERSION`); until 2026-10-21 it stamped 0.7.
(ADR-0025 §8 later moves SaaS bundles into the tenant store, served by `http_pull`; the
ownership rule stays.)


Secrets never appear in a bundle. Provider credentials and HMAC keys come from settings.

## Shape

```yaml
contract_version: "0.5"
id: gr-demo-data-protection
version: 3
mode: ENFORCE                      # ENFORCE | MONITOR
phases: [PRE_LLM, POST_LLM, TOOL_INPUT, MCP_REQUEST]
on_evaluation_failure: fail_closed # fail_closed | fail_open | step_up

l0:                                # a detection is terminal: the L0 gate decides (ADR-0014)
  target: LAST_MESSAGE             # LAST_MESSAGE | FULL_HISTORY | ATTACHMENTS | FULL_CONTEXT
  entities: [TR_TCKN, TR_IBAN, IBAN_CODE, TR_PHONE, CREDIT_CARD, EMAIL_ADDRESS, UNKNOWN_LINK, CUSTOMER_NO]
  min_score: 0.5
  min_score_overrides: {}
  ner: { enabled: false, model: null }
  custom_recognizers:              # customer-defined types (Lakera "custom data leakage")
    - entity: CUSTOMER_NO
      patterns: [{ name: customer_no, regex: "(?<![A-Z0-9])MUS-[0-9]{6}(?![0-9])", score: 0.3 }]
      context: ["müşteri no", "customer"]
  max_chars: 32000
  allowed_domains: [example-partner.com]   # UNKNOWN_LINK: domains this guardrail trusts
  on_detect:                       # action per detected entity type; never ALLOW
    default: BLOCK                 # BLOCK | STEP_UP_APPROVAL | ALLOW_WITH_MODIFICATIONS
    severity: HIGH
    actions:
      EMAIL_ADDRESS: ALLOW_WITH_MODIFICATIONS
      TR_PHONE: ALLOW_WITH_MODIFICATIONS
  exceptions:                      # deterministic only (no model call); first match per type wins
    - id: ex-kyc-internal
      description: KYC may send national IDs to internal applications
      when:
        all:
          - { field: subject.department, operator: EQUALS, value: KYC }
          - { field: destination.class, operator: EQUALS, value: internal_app }
      entities: [TR_TCKN]          # empty → every detected type
      action: ALLOW                # ALLOW | ALLOW_WITH_WARNINGS | ALLOW_WITH_MODIFICATIONS

l1a:
  rules: []                        # see docs/architecture/l1-policy-decision.md
  legacy_preflight: null           # umai-engine HeuristicConfig (REGEX/EXACT rules), evaluated as L1a rules

l1b:
  enabled: true
  provider: openai                 # a name in UMAI_DE_DECISION_PROVIDERS (settings); adapters built:
                                   # openai_decisions | fake (planned: systemone_compatible, jev, gpt_oss_safeguard)
  fallback_provider: null
  shadow_providers: []             # planned: other providers compared on the same traffic
  timeout_ms: 600
  max_input_chars: 4000
  batch: all                       # all: one call with every question | per_policy (0.9, ADR-0040)
  policies: []                     # see decision-questions.md (enforcement: shadow until evals set thresholds)

l2:                                # asynchronous review (ADR-0010); never affects decisions
  enabled: true
  review_when:                     # bundle-level selection, in addition to per-policy review_when
    any:
      - { field: decision.action, operator: IN, value: [BLOCK, STEP_UP_APPROVAL, ALLOW_WITH_WARNINGS] }
  model_ref: gpt_oss_safeguard_20b # resolved in settings to base_url + model + auth
  reasoning_effort: medium         # low | medium | high
  prompt: l2-review.v1
  history: { window_s: 1800, after_s: 300, max_records: 20 }
  finding_threshold: { severity: HIGH, confidence: medium }
  max_attempts: 5

```

## Field reference

| Field | Type | Default | Notes |
|---|---|---|---|
| `id`, `version` | string, int | — | Immutable pair; a change produces a new version |
| `mode` | Mode | — | `MONITOR` returns `ALLOW` with `would_have` |
| `phases` | Phase[] | — | Evaluations for other phases are rejected with `CONFIG_INVALID` |
| `on_evaluation_failure` | enum | `fail_closed` | See [merge and failure](../architecture/decision-merge-and-failure.md) |
| `l0.*` | | | See [L0](../architecture/l0-entity-detection.md) |
| `l0.on_detect` | `{default, severity, actions{TYPE: action}}` | `{default: BLOCK, severity: HIGH}` | Action of the L0 gate per detected type. `ALLOW` / `ALLOW_WITH_WARNINGS` are rejected (ADR-0014) |
| `l0.exceptions[]` | `{id, description?, when, entities[], action}` | `[]` | Deterministic relaxations; `when` uses the L1a condition language; `entities` ⊆ `l0.entities` (empty = all) |
| `l0.entities[]` | entity type[] | — | Built-in types from the [entity catalog](../architecture/l0-entity-catalog.md) plus the bundle's own custom types |
| `l0.custom_recognizers[]` | `{entity, patterns[{name, regex, score}], context[], validator?}` | `[]` | Customer-defined types, run like built-in recognizers (score + context boost 0.25 ≥ `min_score`). `entity` must not be a built-in type and must be listed in `l0.entities`. `validator`: `luhn`, `iban_mod97`, `tckn`, `vkn`, `us_ssn`, `uk_nino`, `de_tax_id`, `fr_nir`, `es_nif`, `es_nie`, `it_fiscal_code`, `nl_bsn`, `pl_pesel`. Patterns that do not compile, match the empty string or contain nested unbounded quantifiers are rejected; matching runs with a timeout |
| `l0.allowed_domains[]` | lowercase domain[] | `[]` | `UNKNOWN_LINK` is not reported for URLs whose registrable domain is listed (subdomains included), in addition to the engine's popular-domain list (Lakera's "allowed domains") |
| `l1a.rules[]` | `{id, when, effect, severity, tags[], final, force_review, redact[]}` | `[]` | Facts and operators in [L1](../architecture/l1-policy-decision.md) |
| `l1a.legacy_preflight` | `umai-engine` HeuristicConfig | null | Compatibility; converted to L1a rules at load |
| `l1b.provider`, `l1b.fallback_provider` | string | — | Names in `UMAI_DE_DECISION_PROVIDERS`; a missing provider is a failure (`L1B_UNAVAILABLE`), never an allow |
| `l1b.batch` | `all` \| `per_policy` | `all` | One provider call with every applicable question, or one per policy in parallel (0.9) |
| `l1b.policies[]` | DecisionPolicy | `[]` | [decision-questions](decision-questions.md); `enforcement: shadow` records without deciding (0.9) |
| `l2.review_when` | condition group over event fields | see shape | Fields: `decision.action`, `decision.severity`, `tags`, `l1b.min_confidence`, `l1b.refused`, `phase`, `destination.class` |
| `l2.model_ref` | string | — | Key into settings' model registry; no URLs or keys in bundles |
| `l2.finding_threshold` | `{severity, confidence}` | `HIGH`, `medium` | Analysis at or above → finding candidate |

## Guardrail policies (0.6, ADR-0021)

A guardrail states what happens to each detected type through named, versioned **policies**
pinned into the bundle. With `l0.policies`, `l0.entities` and `l0.on_detect.actions` are not
set (the entities are derived from the policies).

```yaml
contract_version: "0.6"
id: gr-demo-data-protection
version: 4
name: Demo veri koruma
mode: ENFORCE
phases: [PRE_LLM, POST_LLM, TOOL_INPUT, TOOL_OUTPUT]
l0:
  allowed_domains: [example-partner.com]
  policies:
    - id: identity-data
      version: 2                     # library version pinned into this guardrail version
      name: Kimlik verileri
      entities: [TR_TCKN, TR_VKN, US_SSN]
      action: BLOCK                  # BLOCK | STEP_UP_APPROVAL | ALLOW_WITH_MODIFICATIONS
      severity: HIGH
      roles: [user, assistant, tool, tool_call, memory]
      sensitivity: L3                # L1 0.80 · L2 0.65 · L3 0.50 · L4 0.35
    - id: contact-data
      name: İletişim bilgileri
      entities: [EMAIL_ADDRESS, TR_PHONE]
      action: ALLOW_WITH_MODIFICATIONS
      severity: MEDIUM
      roles: [user, assistant]
  exceptions:
    - id: ex-kyc-internal
      when: { all: [{ field: subject.department, operator: EQUALS, value: KYC }] }
      entities: [TR_TCKN]
      action: ALLOW
```

| Field | Notes |
|---|---|
| `name`, `description` | Guardrail display data (console) |
| `l0.policies[].id`, `version` | Library id and pinned version; ids unique per guardrail |
| `l0.policies[].entities` | Catalog or custom types; a type belongs to exactly one policy |
| `l0.policies[].roles` | `user` → `PRE_LLM`, `assistant` → `POST_LLM`, `tool_call` → `TOOL_INPUT` / `MCP_REQUEST`, `tool` → `TOOL_OUTPUT` / `MCP_RESPONSE`, `memory` → `MEMORY_WRITE`; default all; at least one must map to an enabled phase |
| `l0.policies[].enabled` | `true` (default) or `false`: an off policy stays in the guardrail with its configuration and is not evaluated (0.7, ADR-0022) |
| `l0.policies[].sensitivity` | Minimum detection score of the policy's types; `min_score_overrides` still wins per type |

## Validation beyond the schema

The loader rejects a bundle when:

- an `l1a` rule references an unknown fact path or an entity not in `l0.entities`;
- an `l1b` policy's `decide` references a question or option it does not declare;
- `l1b.provider` or `fallback_provider` lacks a capability a policy needs (e.g. a `score`
  question on a provider without score support, or more options than the provider allows);
- `l1b.timeout_ms` exceeds the settings cap for the runtime path;
- a regex fails to compile or is flagged as catastrophic-backtracking prone;
- the `l0` block contains decision fields (`hard_block`, `redact_on`) from 0.1.

## Changelog

- 0.9 (2026-10-20, ADR-0040): L1b implemented. `l1b.batch`; decision policies gain
  `enforcement`, `name`, `description`, `source` and probability thresholds (`p_gte`) in
  `decide` (decision-questions 0.4). `redact` in an L1b `decide` rule is refused at load (not
  supported yet).
- 0.8 (2026-10-19): tenant-owned bundles: `tenants/<tenant_id>/` layout, signed
  `manifest.tenant_id`, unowned bundles belong to the default tenant (ADR-0019 §2). Manifests
  without `tenant_id` verify unchanged.
- 0.7 (2026-10-17): `l0.policies[].enabled` (on / off, ADR-0022).
- 0.6 (2026-10-17): `l0.policies` (named, versioned, roles, sensitivity) and guardrail
  `name` / `description` (ADR-0021). Bundles ≤ 0.5 still load.
- 0.5 (2026-10-17): `l0.allowed_domains`; `l0.custom_recognizers` supported (validators, regex
  safety checks); `l0.entities` covers the full [entity catalog](../architecture/l0-entity-catalog.md)
  (ADR-0017). 0.3 / 0.4 bundles still load.
- 0.4 (2026-10-16): `l0.on_detect` and `l0.exceptions` — an L0 detection is terminal
  (ADR-0014). 0.3 bundles still load; without `on_detect` every detection blocks.
- 0.3 (2026-10-16): breaking — bundles are directory artifacts with `manifest.json`
  (SHA-256 + Ed25519) and an `active` pointer (ADR-0012); `signature`/`key_id` removed from
  `bundle.yaml`.
- 0.2 (2026-10-15): breaking — removed `l0.hard_block` and `l0.redact_on` (now L1a rules,
  ADR-0011); moved `legacy_preflight` to `l1a`; `l2` block redefined for asynchronous review
  (`review_when`, `finding_threshold`, `max_attempts`; removed `timeout_ms`, `on_failure`)
  per ADR-0010; `force_escalate` → `force_review`; L1a `redact` added.
- 0.1 (2026-10-15): initial draft.
