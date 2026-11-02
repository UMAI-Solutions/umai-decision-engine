# ADR-0056 — Guardrail and policy authoring in the console: templates, the full L0 editor, L1b policies, golden cases

- **Status:** Proposed
- **Date:** 2026-11-02
- **Deciders:** product owner (direction and working assumptions 1–6 below, 2026-11-02);
  engineering proposal for everything else
- **Builds on:** ADR-0005 (no raw PII beyond L0), ADR-0012 (signed bundles), ADR-0013 (dashboard
  editing), ADR-0014 (L0 gate, exceptions), ADR-0015 (golden tests), ADR-0017 (entity catalog),
  ADR-0021 (policies pinned into guardrails), ADR-0022 (policy service), ADR-0029 (roles,
  four-eyes), ADR-0040 (L1b), ADR-0041 (records without content), ADR-0043 (plans), ADR-0045
  (audit log)
- **Related:** ADR-0057 (evaluating a guardrail against an uploaded dataset, written in parallel):
  how a customer sees a new policy's effect before publishing
- **Contracts (drafts, not implemented):** [guardrails](../contracts/guardrails.md) 0.1 (new),
  [policy-bundle](../contracts/policy-bundle.md) 0.10, [policy-tests](../contracts/policy-tests.md)
  0.4, [entitlements](../contracts/entitlements.md) 0.3, [audit](../contracts/audit.md) 0.2,
  [tag-taxonomy](../contracts/tag-taxonomy.md) 0.4

## Context

**The request.** On 2026-11-02 the product owner asked that customers create new guardrails and
new L0 and L1 policies themselves, in the console. They accepted these working assumptions as the
basis of this proposal (they can still change):

1. A guardrail is created from a template (KVKK data protection, prompt attack, malicious use,
   blank) or as a copy of one of the tenant's guardrails. Templates are signed data, never code
   defaults (AGENTS rule 8). The first version is a draft and goes through test → publish
   (four-eyes) → activate. The number of guardrails is a plan entitlement.
2. The L0 editor is completed: exceptions become editable; custom recognizers can be added with a
   pattern, context words, an optional validator, an instant test against sample text and
   protection against catastrophic regular expressions (ReDoS).
3. An L1b policy is added from a library or written by the customer, with the `DecisionPolicy`
   fields (text, action on violation, shadow or enforce, a timeout bounded by the bundle), and
   starts in shadow.
4. An L1a rule editor comes later.
5. A guardrail cannot be published without golden cases, so the console needs a case editor.
6. Content is never stored, so a new policy cannot be replayed on past traffic. Its effect before
   publishing is shown by golden cases and by the dataset evaluation of ADR-0057; shadow mode shows
   it afterwards.

**What exists** (engine 0762d94, console e4adb02, read on 2026-11-02):

- **No way to create a guardrail.** The policy service's guardrail routes are list, read, draft
  create / read / save / discard, draft test, publish and activate
  (`control_plane/app.py:275-402`). A draft starts from an existing version and refuses a guardrail
  without an active one (`control_plane/service.py:262-270`). The only new bundle is the starter:
  `install_starter` (`service.py:381`) copies the default tenant's `gr-demo-data-protection`
  (`settings.py:149`) with its golden tests into each new organization. The console has no "New
  guardrail" button; its empty state still says a guardrail is a directory in `policies/`.
- **What the console can edit.** A draft is saved as the whole bundle (`PUT …/draft {bundle}`,
  700 ms after the last change) with no revision check: the last write wins. The editor changes
  only `l0.policies` (enabled, name, action, sensitivity, roles, detectors) and `l0.allowed_domains`.
  Exceptions, custom recognizers (their patterns are not even shown), phases, mode, failure
  behaviour, name and description are read-only; L1b decision policies are shown read-only and
  carried over unchanged; L1a rules and `min_score` are not shown.
- **Golden cases** are not part of a draft. A publish copies the base version's `tests/`
  (`service.py:321`); a changed case can be *accepted* (its expectation is rewritten from the new
  decision, ADR-0022 §4), but no case can be written or read in the console.
- **Roles.** `guardrail.draft.write`, `guardrail.draft.test` and `guardrail.publish` belong to owner
  and `policy_editor`; `guardrail.activate` to owner and `approver` (`control_plane/authz.py:116-121`).
  Without the `rbac` entitlement (Community) an admin holds every non-owner permission. Four-eyes
  activation (ADR-0029 §4) is not built.
- **Custom recognizers** (policy-bundle 0.5) run on the `regex` module. A bundle-load heuristic
  rejects nested unbounded quantifiers (`l0/recognizers/custom.py:62-77`) and each scan has a
  one-second timeout; on a timeout the pattern is **skipped without a record**
  (`l0/recognizers/base.py:97-98`).
- **L1b batching.** The request path asks only the enforcing policies, in one provider call
  (`l1b.batch: all`); shadow policies always run in a second call after the decision (ADR-0040,
  amendment 2026-10-30). `l1b.timeout_ms` bounds the whole call.
- **Records hold no content**, not even redacted (ADR-0041). ADR-0013 §4 expected to replay stored
  events against a candidate bundle because "L1 only uses facts and redacted text"; that premise no
  longer holds for L1b, and recognizer changes could never be replayed.

**Measurements.**

ReDoS ([report](../../evals/reports/2026-11-02-redos-custom-patterns/summary.md)): two common
exponential shapes pass today's load check, `(\w|\d)+$` and `([a-z0-9]{1,20})+$`. From 4 000
characters (the engine accepts 32 000) each scan runs into the one-second timeout, so the input is
scanned as if the recognizer did not exist, and one of four L0 threads is busy for a second per
pattern and chunk. RE2 (`google-re2`) scanned every attack input in under 0.3 ms at every length.
On ordinary text it costs 0.15–0.4 ms per pattern per 32 000 characters, against 0.025 ms for
`regex`. RE2 has no lookarounds: the contract's own example pattern
`(?<![A-Z0-9])MUS-[0-9]{6}(?![0-9])` does not compile.

L1b cost per policy, measured on umai-beta with OpenAI Decisions `gpt-6-luna`
([l1-latency report](../../evals/reports/2026-10-30-l1-latency-split-beta-westeurope/summary.md),
ADR-0040 amendment of 2026-10-30):

| Policies in the call | Input tokens per evaluation | p50 ms | p95 ms |
|---|---|---|---|
| 2 (`prompt-attack`, `malicious-use`) | ≈ 2 500 – 2 600 | 419 – 426 | 607 – 617 |
| 9 (all converted policies) | ≈ 10 000 – 11 000 | 409 – 414 | 635 – 667 |

So one converted policy (a long instruction text and one question) adds about 1 070 input tokens.
At $0.10 per million input tokens (ADR-0040) that is about $0.0001 per evaluation, or about $11 per
100 000 evaluations a month. Latency did not grow measurably from two to nine questions in one
call. Shadow policies cost the same tokens in their background call and add no latency.

## Decision

### 1. Structured editors over the bundle model; no free YAML editor

The console edits guardrails through forms that write contract fields of the bundle, and the policy
service validates every draft with the engine's own code (schema, `validate_bundle`, golden runner),
as it does today. What the forms cannot express is not customer-editable: L1a rules, the `l2`
block, `min_score_overrides`, L1b questions other than the forms of §5, and the choice of decision
provider. Those are written in bundle files by UMAI (the catalog bundles of §3; R&D through Git). A
draft that contains such fields keeps them unchanged and the console shows them read-only, as it
shows L1b policies today.

### 2. Creating a guardrail: from a template or as a copy, always as a draft

- `POST /v1/guardrails` with `{name, description?, id?, from}`, where `from` is
  `{"template": <template id>}` or `{"guardrail": <id>, "version"?: <n>}` (default: the active
  version). It creates a **draft without a base version**: the guardrail appears in the list with
  `active_version: null` and `has_draft: true`, nothing is published, and no engine sees it.
- The draft holds the template's or source's bundle with the new id and name, and its golden cases
  (§6). The first publish creates version 1; activation, rollback and applications are unchanged.
- The id defaults to `gr-` and a slug of the name (Turkish letters transliterated; a number appended
  on collision); an explicit id follows the bundle id pattern and is refused with 409
  `guardrail_exists` when taken. Ids are per tenant (policy-bundle 0.8).
- A copy is made from the same tenant only. Templates are copied into the tenant, never referenced
  across tenants (ADR-0021 §7). The change log's first entry records where the guardrail came from
  (`{"action": "create", "from": …}`).
- **Limit:** entitlement `max_guardrails` (§9) counts the tenant's guardrails that have a version or
  a draft, archived ones excluded; over it, 409 `guardrail_limit_reached`. A lower limit after a
  plan change never touches existing guardrails; it only refuses new ones.
- **Archive:** `POST /v1/guardrails/{id}/archive` hides a guardrail from the list and the count and
  removes its active pointer, so engines stop serving it. It is refused with 409 `guardrail_in_use`
  while an application uses it. Versions and the change log stay: decision events and the
  organization archive refer to them. Deleting versions is not offered; restoring an archived
  guardrail and how long its versions are kept is Q83.

### 3. Templates and the policy library are signed bundles in a catalog tenant

- Templates and the L1b policy library are ordinary bundles, signed, validated and golden-tested like
  any guardrail, owned by a **catalog tenant** (`UMAI_DE_CATALOG_TENANT_ID`, default: the deployment's
  default tenant, where the starter lives today). `UMAI_DE_GUARDRAIL_TEMPLATES` lists the template
  bundle ids in display order; `UMAI_DE_POLICY_LIBRARY` names the library bundle. Their active
  versions are what customers see: a template update is a publish in the catalog tenant and never
  changes a customer's guardrail (pinning, ADR-0021 §2).
- **First templates:**

  | Template | Content |
  |---|---|
  | `tpl-kvkk-data-protection` | The starter's L0 policies (identity, payment, contact data, secrets, unknown links) and the KYC exception, with its L0 cases |
  | `tpl-prompt-attack` | The `prompt-attack` decision policy in shadow, L0 `secrets` |
  | `tpl-malicious-use` | The `malicious-use` decision policy in shadow, L0 `secrets` |
  | `tpl-blank` | No policies; phases `PRE_LLM`, `POST_LLM`; `ENFORCE`; `fail_closed`; one case: ordinary text is allowed |

  The starter of new organizations (`UMAI_DE_STARTER_GUARDRAIL`) may name a template. The first set,
  its language and whether the KVKK template also carries decision policies in shadow (as the
  starter does today) is Q67.
- **The library** (`lib-decision-policies`) holds the decision policies customers can add, each with
  its cases in `tests/<policy id>.yaml`, so adding a policy copies exactly its cases. First content:
  the nine policies converted from `OpenAI_Decisions` (ADR-0040 §8; six of them have no golden case
  yet)
  and a financial-advice policy. Policies that need the customer's own words (off-topic: the allowed
  topics; competitor mention: the names) are written with the form of §5 in the first version; a
  library entry with parameters needs a contract change and waits for Q69.
- Self-hosted deployments import UMAI's catalog bundles and re-sign them with their own key
  (ADR-0027 §3, Q18).

### 4. The complete L0 editor

- **Exceptions** become editable: id, description, the types they relax (a subset of the
  guardrail's types; empty means all), the action (`ALLOW`, `ALLOW_WITH_WARNINGS`,
  `ALLOW_WITH_MODIFICATIONS`) and a condition built from a fixed list of fields and operators:
  `subject.department`, `subject.role`, `destination.class`, `destination.app`, `phase`, with
  `EQUALS`, `NOT_EQUALS`, `IN`, `NOT_IN`, combined with `all` / `any`. `MATCHES_REGEX` and fact paths
  outside the list stay operator-only. An exception relaxes a block, so it goes through publish and
  activation like any change; the change summary lists it first.
- **Custom detectors** (`l0.custom_recognizers`): an entity type name (not a catalog type), a display
  name (policy-bundle 0.10), one to five patterns with a name and a score, context words, an optional
  validator from the existing list, and **`boundary`**: `alnum` (the default for new detectors) drops
  a match whose neighbouring character is a Unicode letter or digit, which replaces the lookarounds
  RE2 lacks; `none` keeps every match. A detector is then put into a policy (one type, one policy:
  ADR-0021 §6).
- **Pattern dialect: RE2.** In bundles at contract 0.10 and later, custom patterns are compiled with
  RE2 (`google-re2`): linear-time matching, so no input can make a pattern slow. Syntax RE2 does
  not support (lookarounds, backreferences) is a draft problem in plain words, pointing to
  `boundary`. Further limits: at most 512 characters per pattern, RE2's default program-memory
  budget (a pattern over it does not compile), a pattern must not match the empty string (kept), and
  at most 50 patterns per guardrail. The `l0-latency` suite confirms the cap before release.
  Bundles up to 0.9 keep the `regex` matcher, so nothing that loads today stops loading; the console
  writes 0.10 at the next publish and shows any pattern that must change.
- **No silent skip.** A matcher timeout or error, for any pattern including the built-in ones,
  raises inside L0 and becomes `L0_ERROR` under the guardrail's `on_evaluation_failure` (the
  existing path, `pipeline/orchestrator.py`), instead of scanning on without the pattern
  (AGENTS rule 4). This part does not depend on the rest of the ADR.
- **Instant test.** `POST /v1/guardrails/{id}/draft/recognizer-test` takes a detector definition
  and sample text (at most 4 000 characters) and answers the problems, the matched spans with
  pattern name, score, validation and context result, and the time taken. The text is held in
  memory for the call only; it is not stored, logged or put into the audit event, and the answer
  carries offsets, not values (the console highlights its own copy). *Save as case* turns the
  sample into a golden case (§6).
- **Guardrail settings** become editable: name, description, mode (`ENFORCE` / `MONITOR`), the
  phases the guardrail checks, `on_evaluation_failure` (a change to `fail_open` asks for
  confirmation and is listed first in the change summary), allowed domains (already editable), and
  `l1b.timeout_ms` within the deployment's cap (see Q62).

### 5. L1b policies: from the library or written in a simple form, always starting in shadow

- **From the library:** the policy is copied with its library id and version (pinned, ADR-0021 §2)
  and its cases. The console later says when the library has a newer version, as a proposed
  draft change; nothing updates by itself. If the guardrail has no `l1b` block yet, the library
  bundle's `l1b` settings (provider alias, timeout, input size, batch) are copied with it: data, not
  code.
- **Both are added by the policy service** (`POST /v1/guardrails/{id}/draft/decision-policies`), so
  shadow, pinning and the copied cases do not depend on the console; later edits are ordinary draft
  saves.
- **Written by the customer:** name, description, policy text (≤ 4 000 characters, the existing
  limit), the action and severity on violation, and the roles it applies to. The policy service
  turns the form into a regular `DecisionPolicy` by filling in the library's form entry
  (`custom-policy-form`), so the question wording every written policy shares is versioned,
  evaluated data, not code: one `condition` question `violation` asking whether the content
  violates the policy text, a rule
  `{violation: {p_gte: <threshold>}}` → the chosen action, severity and tag `custom.<id>` (hyphens
  as underscores; tag-taxonomy 0.4), then `default: ALLOW`, and `review_when.actions` = the chosen
  action. The form is how the console reads such a policy back; a policy that does not have this
  shape is shown read-only (§1).
- **Editable on every L1b policy:** name, description, roles, enforcement, and the action, severity
  and `p_gte` of each `decide` rule. Questions and options of library policies are not editable in
  the console.
- **Shadow first.** Every added or written policy starts with `enforcement: shadow`. Switching to
  `enforce` needs the policy's cases (§6) and an explicit confirmation of its threshold, which the
  console shows next to ADR-0057's result for this policy when one exists. Whether a dataset
  evaluation must exist first is Q68.
- **No per-policy timeout.** The request path makes one call for all enforcing policies, bounded by
  the guardrail's `l1b.timeout_ms` (itself bounded by the deployment's cap); a per-policy timeout
  would only mean something with `batch: per_policy`, which the console does not offer.
- **Cost is shown and capped.** The editor shows each policy's size in input tokens and the
  measured cost per 100 000 evaluations. Entitlement `max_decision_policies` (§9) caps the L1b
  policies per guardrail, shadow ones included because they cost the same tokens. Over the cap a
  draft shows a problem and cannot be published; versions published under a higher limit keep
  working.

### 6. Golden cases live in the draft, are written in the console, and cover every policy

- **A draft carries its cases** (the `tests/` files of policy-tests, as JSON). They start as the
  base version's, the template's or the source guardrail's cases; publish writes them as the new
  version's `tests/`, and accepting a changed case rewrites it in the draft. The case editor writes
  single cases (`PUT` / `DELETE /v1/guardrails/{id}/draft/cases/{case_id}`); new cases go to
  `tests/console.yaml`. `POST …/draft/test` takes an optional list of case ids to run only those.
- **Coverage rule** (policy-tests 0.4), checked by the golden runner and enforced at publish: every
  enabled L0 policy and every exception has a passing case in which it decides (it appears in
  `decision.matched`); every enforcing L1b policy has one passing case where it matches and one
  where it is asked and does not; and the guardrail has at least one passing `ALLOW` case. Shadow
  policies need no case to publish, only to be switched to enforce. The coverage rule applies to
  versions published by the policy service from contract 0.10; the CLI exposes it as
  `--require-coverage`.
- **A blank guardrail** therefore publishes with the template's single `ALLOW` case, and each policy
  added later asks for its own cases. Adding a policy offers to write them at once: for an L0
  policy, a case with a synthetic value of one of its types; for an L1b policy, a violating and a
  non-violating case with `model_answers` filled in from the policy's question.
- **Content in cases.** Case inputs are the one place where customers store text in UMAI. They are
  tenant configuration, stored with the draft (tenant store) and with each published version
  (`tests/`), and included in the organization archive (ADR-0047). They never appear in logs,
  decision events or audit details (the audit event records case ids and counts). The policy
  service passes case text only to L0 code in its test stage, as the golden runner does today; L1b
  answers in cases are stubbed, so no case text reaches a decision provider. The editor says
  "synthetic data only" and offers *Insert a test value* per entity type: synthetic values served by
  the policy service, for Turkish identifiers the reference vectors
  (`docs/reference/turkish-identifiers.md`). It does not try to detect real data: a valid test
  identifier and a real one look the same. Q80 asks the product owner to accept this.

### 7. Drafts get a revision

Every draft carries `revision`, incremented by each change (bundle or cases). Changing calls send
`If-Match: "<revision>"`; a stale revision is answered 412 `draft_changed` with the current
revision. The console reloads and shows what changed instead of silently overwriting another
editor's work. In guardrails 0.1 the header is optional (last write wins without it, as today); it
becomes required once the console sends it.

### 8. Permissions and audit

| Action (ADR-0029 matrix) | Routes | Roles | Audit action (ADR-0045) |
|---|---|---|---|
| `guardrail.read` (existing) | list, read, draft read, `GET /v1/guardrail-templates`, `GET /v1/policy-library` | owner, admin, policy editor, approver, viewer, operator | — |
| `guardrail.create` (new) | `POST /v1/guardrails` | owner, policy editor | `guardrail.create` |
| `guardrail.draft.write` (existing) | draft create, save, discard; add a decision policy; case save and delete | owner, policy editor | `guardrail.draft.create` / `.save` / `.discard`; `guardrail.draft.policy.add`, `guardrail.draft.case.save`, `guardrail.draft.case.delete` (new) |
| `guardrail.draft.test` (existing) | draft test (all or some cases), recognizer test | owner, policy editor | `guardrail.draft.test`; `guardrail.draft.recognizer.test` (new) |
| `guardrail.publish` (existing) | publish | owner, policy editor | `guardrail.publish` |
| `guardrail.activate` (existing) | activate, archive | owner, approver | `guardrail.activate`; `guardrail.archive` (new) |

Creating a guardrail publishes nothing, so it needs the editor's permission, not the approver's;
archiving changes what engines serve, so it needs the approver's. Without `rbac`, admins hold all of
these (ADR-0029 §2). Four-eyes activation (ADR-0029 §4) applies unchanged when built (Q82). Audit
details never contain case text, sample text or patterns' matches, only ids, counts and names.

### 9. Entitlements

| Limit (entitlements 0.3) | Counts | Checked |
|---|---|---|
| `max_guardrails` | Guardrails of the tenant with a version or a draft, archived excluded | `POST /v1/guardrails`: 409 `guardrail_limit_reached` |
| `max_decision_policies` | L1b policies in one guardrail, shadow included | Draft problem; publish refused with 422 |

Both are null (no limit) unless the plans file sets them, like every limit. Values per edition are
Q66. The policy service enforces them; engines ignore them. Whether custom detectors are offered in
Community is Q81: a feature entitlement, which the plans file cannot express yet.

### 10. Seeing a new policy's effect

Before publishing: golden cases (deterministic; L1b with stubbed answers) and the dataset evaluation
of ADR-0057 (the real model on the customer's own labelled examples). After publishing: shadow
policies record their would-be decisions as L1b outcome records (ADR-0041), and a whole guardrail
can run in `MONITOR` mode. Past traffic is not replayed (Context).

### 11. Out of scope

- **L1a rule editor.** L1a is not implemented (a bundle with L1a rules is answered with
  `L1_NOT_IMPLEMENTED`), the callers that would send its facts (`subject.role`, `department`,
  destination class; Q10) are not known, and the deterministic relaxation the console needs first is
  the L0 exception, which uses the same condition language. It comes after L1a itself.
- The `l2` block, `min_score_overrides`, provider choice, `batch: per_policy`, L1b questions of other
  shapes, sharing policies between tenants, and a free YAML editor (Q84).

### 12. Console flows (short form)

- **Create.** Guardrails › *New guardrail* → choose a template card or *Copy of* (guardrail and
  version) → name → *Create* → the draft editor opens, marked "never published".
- **Edit L0.** Tabs *Policies*, *Custom detectors*, *Exceptions*, *Settings*. A custom detector:
  name, type, patterns with live problems, validator, *whole values only* (on), context words; sample
  text → *Test* → highlighted matches → *Save as case*; then add the type to a policy.
- **Add an L1b policy.** *AI risk* → *Add policy* → *From the library* (name, description, size and
  cost) or *Write your own* (name, text, roles, action and severity on violation) → added in shadow
  with its cases → later *Enforce*: needs its cases, shows the threshold and ADR-0057's result.
- **Write a case.** *Tests* → list of cases with their last result → *New case*: text or messages,
  role and phase, context (department, destination), for L1b the model's answer per question,
  expected decision (optionally matched policies and masked types) → *Run* → *Save*. *Insert a test
  value* per type.
- **Publish.** *Review* → change summary, test results and coverage → accept changed cases → note →
  *Publish* → *Activate* (by the same person where approval is not required; otherwise by an
  approver).

## Alternatives considered

| Option | Why not |
|---|---|
| A free YAML editor for the whole bundle | Fastest to build and covers every field, but customers would write question sets, L1a rules and regular expressions with no guidance, and validation errors arrive as schema paths. Forms keep invalid shapes unrepresentable; an operator-only YAML view is Q84 |
| Templates as a separate catalog contract (fragments composed into a guardrail) | A second artifact to sign, validate and test; fragments cannot be golden-tested as a whole. A template that is a bundle loads, signs and tests with the code that exists |
| Templates only, no copy | Customers with a tuned guardrail would rebuild it by hand for a second application |
| Copy only, no templates | A new organization has one guardrail (the starter); every other shape would start from the wrong one |
| Library policies referenced at runtime (no pinning) | A library change would change live decisions silently (rejected by ADR-0021) |
| ReDoS: keep `regex`, a better heuristic and the timeout | The measured heuristic misses common shapes; the timeout costs a second of a thread per pattern and turns into a silent miss. A shorter timeout still burns shared CPU in a multi-tenant engine |
| ReDoS: static analysis of patterns (e.g. regexploit) | Finds known shapes, cannot prove the absence of others; still needs the runtime bound |
| ReDoS: Rust `regex` through Python bindings | Same linear-time guarantee and the same missing lookarounds as RE2. RE2 was measured, and Google publishes `google-re2` 1.1.20251105 wheels for CPython 3.12 on Windows (`win_amd64`) and Linux (`manylinux_2_28_x86_64`, which the `python:3.12-slim` image satisfies) |
| RE2 for every pattern, built-ins included | Built-in patterns are ours, use lookarounds (29 in `catalog.py` and `secrets.py`) and are measured by the `l0-*` suites; only tenant-written patterns are untrusted input |
| Replay a new policy on past traffic | Records keep no content (ADR-0041, ADR-0005). Replaying L0 action changes over stored entity types is possible later; it does not cover new detectors or L1b |
| A full L1b question editor (choice and score questions, several rules) | Writing good decision questions needs evals (ADR-0040); a single violation question with an action is what customers can write and test. Library policies keep their richer shapes |
| Per-policy L1b timeouts | One call carries every enforcing policy; a per-policy timeout has no meaning there |
| No cap on L1b policies, bill tokens instead | Plans are counted in requests (ADR-0043); an unbounded policy count makes the cost of a request unbounded |
| `--require-tests` only (at least one case) | One case says nothing about a policy added later; the coverage rule makes every deciding policy show its effect, as AGENTS' definition of done asks of policy changes |
| Scan case inputs with L0 and refuse real-looking values | Test identifiers are checksum-valid; real and synthetic values cannot be told apart |

## Consequences

**To build** (sizes: S ≤ 1 day, M 2–4 days, L 1–2 weeks):

| Step | Piece | Size |
|---|---|---|
| E1 | Engine: a matcher timeout or error raises → `L0_ERROR`; regression test with the measured attack patterns | S |
| E2 | Engine: RE2 for custom patterns in 0.10 bundles, `boundary`, `name`, pattern limits; `google-re2` dependency through the supply-chain checks (ADR-0049); `dev/bench_redos.py`; `l0-latency` at the pattern cap | M |
| E3 | Policy service: drafts without a base version, with cases and `revision` (tenant-store migration: `drafts.base_version` nullable, `tests`, `revision`); `If-Match` | M |
| E4 | Policy service: `POST /v1/guardrails`, archive, templates and library listing, catalog settings, `max_guardrails`, change-log `create` entry | M |
| E5 | Golden runner: coverage rule, `--require-coverage`, case subset; case routes | M |
| E6 | Policy service: recognizer test route; L1b form compiler and reader; `max_decision_policies` | M |
| E7 | Permission matrix, audit actions, entitlements 0.3 (plans file, snapshot, `GET /v1/entitlements`) | S |
| E8 | Catalog content: the four templates and the library bundle, each with cases (six converted policies have none yet) | M |
| C1 | Console: *New guardrail* (templates, copy), archive | M |
| C2 | Console: exceptions, custom detectors with the instant test, guardrail settings | L |
| C3 | Console: L1b library and form, shadow → enforce with the coverage gate, cost display | M |
| C4 | Console: *Tests* tab and case editor, coverage view, test values | L |
| C5 | Console: revision handling (412), buttons by role | S |

**Order.** E1 now (it fixes a silent fail-open and needs nothing else). Then E3 + E7 → E4 + E8 →
C1: customers can create guardrails and edit them with today's editor. Then E5 + C4, because new
policies need cases before they can be published. Then E2 + E6 + C2, then C3, with ADR-0057 informing
its enforce step. Four-eyes (ADR-0029 §4) can be built alongside; it is needed before Enterprise
customers enforce their own policies (Q82).

**To measure.** `l0-latency` with a guardrail at the custom-pattern cap (p95 at 2 000 and 32 000
characters) before the cap ships; `l1-latency` with a guardrail at the proposed
`max_decision_policies` enforcing, before the cap value is set; the time of a template's test run
in the publish path; with the design partner, the time to create a guardrail and a custom detector.

**Easier.** Customers protect a second application with its own guardrail without UMAI staff;
UMAI ships new protections as catalog publishes, not releases; every customer policy arrives with
cases that show what it does.

**Harder.** Customer-written cases are customer content in the policy service and the bundle source
(Q80). More L1b policies mean more provider tokens per evaluation. Catalog bundles need owners,
cases and releases. RE2 is a new native dependency on both platforms. Two pattern matchers run
until no bundle older than 0.10 with custom recognizers remains.

## Validation

- Unit and API tests: creating from a template and from a copy (cases copied, change-log entry,
  version 1 on first publish), the limit and archive refusals, revision conflicts (412), the
  coverage rule (each clause failing on its own), the L1b form round trip (form → `DecisionPolicy` →
  form), the permission matrix rows and the audit route walk for every new route.
- ReDoS regression: the report's attack patterns compile under RE2 and scan 32 000 characters in
  under 5 ms; under the `regex` path a timeout yields `L0_ERROR`, never a pass.
- Leak suite: recognizer-test and case texts never appear in logs, errors, audit events or records.
- The catalog bundles pass `cli test --require-tests --require-coverage` in CI.
- Revisit when customers ask for L1a rules or parameterized library policies, when the RE2 cost at
  the cap exceeds the L0 budget, or when ADR-0057 shows that customer-written policies need richer
  question shapes.
