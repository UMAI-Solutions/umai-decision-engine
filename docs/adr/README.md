# Architecture decision records

One file per decision, numbered, never renumbered. Change a decision by writing a new ADR
that supersedes the old one; update the old one's status line only.

Status meanings: **Accepted** — decided by the product owner; build against it.
**Proposed** — design recommendation not yet confirmed; build against it unless it is
listed in [`../open-questions.md`](../open-questions.md) as blocking, and flag deviations.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-layered-l0-l1-l2-pipeline.md) | Layered L0 → L1 → L2 pipeline | Accepted |
| [0002](0002-l2-synchronous.md) | L2 runs synchronously | Superseded by 0010 |
| [0003](0003-l2-conditional-escalation.md) | L2 runs only for escalated actions | Superseded by 0010 |
| [0004](0004-decision-model-provider-abstraction.md) | L1b uses on-prem deployable decision models behind a provider interface | Accepted (default model pending F0) |
| [0005](0005-no-raw-pii-beyond-l0.md) | Raw PII never leaves L0 | Proposed |
| [0006](0006-l2-override-rules.md) | What L2 may override | Superseded by 0010 |
| [0007](0007-presidio-for-l0.md) | Microsoft Presidio for L0 | Accepted |
| [0008](0008-standalone-rnd-repository.md) | Standalone R&D repository, English documentation | Accepted |
| [0009](0009-python-stack.md) | Python 3.12 / FastAPI / pip + pyproject | Proposed |
| [0010](0010-l2-asynchronous-review.md) | L1 decides; L2 reviews asynchronously | Accepted |
| [0011](0011-l0-does-not-decide.md) | L0 detects, it does not decide | Superseded by 0014 |
| [0012](0012-signed-immutable-policy-bundles.md) | Policies are signed, immutable bundles; the engine only reads them | Accepted |
| [0013](0013-policy-lifecycle-dashboard.md) | Policies are edited and published live from a dashboard | Accepted (direction; not built) |
| [0014](0014-l0-detection-is-terminal.md) | An L0 detection is terminal: L0 decides, L1 does not run | Accepted |
| [0015](0015-policy-golden-tests.md) | Policy changes are gated by golden tests that run through the real pipeline | Accepted |
| [0016](0016-control-center-separate-repo.md) | The engine's console is a separate repository that calls the API over HTTP with a service token | Accepted |
| [0017](0017-l0-coverage-lakera-parity-tr-eu.md) | L0 coverage target: Lakera PII parity, extended to Türkiye and Europe | Accepted |
| [0018](0018-saas-and-self-hosted-editions.md) | UMAI ships as cloud SaaS and self-hosted, in Community and Enterprise editions | Accepted |
| [0019](0019-editions-as-runtime-entitlements.md) | Editions are runtime entitlements: tenant keys, signed licences, engine-enforced limits | Proposed |
| [0020](0020-deterministic-baselines-names-addresses.md) | Names and addresses start with deterministic recognizers; NER is added on evidence | Accepted (NER deferred) |
| [0021](0021-guardrail-model-policies-pinning-hierarchy.md) | Guardrail model: versioned policies pinned into guardrails; company → project → guardrail | Accepted |
| [0022](0022-policy-service-control-plane.md) | Policy service: drafts, tests and publishing from the console | Accepted |

New ADR: copy [`0000-template.md`](0000-template.md), or use skill `write-adr`.
