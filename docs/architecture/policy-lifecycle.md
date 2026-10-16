# Policy lifecycle: edit in a dashboard, publish live

**Status:** direction accepted ([ADR-0013](../adr/0013-policy-lifecycle-dashboard.md)); the
control plane is not built yet. The engine side is designed for it from the start.

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
| Test | control plane + engine | Golden cases in the bundle's `tests/`; playground = single input evaluated with an inline bundle (dev/test engines only, synthetic data) |
| Preview impact | engine replay job | Re-evaluate the last N stored decision events with the candidate: "38 of 1 240 decisions would change", with diffs. The L0 gate (`on_detect`, exceptions) and L1 only use entity types/counts, facts and redacted text, which events keep. Recognizer changes cannot be replayed (need raw content) → synthetic cases only |
| Approve | control plane | Roles: editor, approver; four-eyes for production; diff between versions |
| Publish | control plane | Build `bundle.yaml` + `manifest.json` (hash, Ed25519 signature); version becomes immutable |
| Activate | control plane | Move the active pointer per bundle id (and later per environment / tenant / traffic share) |
| Propagate | engine | Poll with ETag (default every ~5–10 s) and/or push signal; verify → validate → compile → atomic swap; in-flight requests finish on the old version |
| Monitor | engine + dashboard | Engines report active version and load errors; decision metrics by bundle version |
| Roll back | control plane | Move the pointer back |

Later: **shadow mode** (candidate evaluated alongside the active version on a sample;
caller gets the active decision, differences are recorded) and **staged rollout**
(test → prod, single tenant, traffic percentage).

## Engine requirements (keep these true from the first commit)

| Requirement | Status |
|---|---|
| Bundles loaded only through `BundleSource` + bundle store; no other policy cache | F1 |
| Atomic swap, last-known-good on failure, disk cache | F1 (directory source) |
| Status endpoint `GET /v1/bundles`: loaded versions, active pointer, hash, load errors | F1 |
| Every decision carries bundle id, version, content hash | F1 |
| Inline-bundle evaluation (`bundle_inline`) for playground, disabled in production | F1 |
| Replay endpoint/job over decision events | after F3 |
| Shadow evaluation | later |
| `http_pull` source + push signal | with the control plane |

## Open questions

- Where the control plane lives: a separate service in this repository or its own repository.
- Who signs on-prem: the customer's control plane with its own key (fits "users edit
  policies") or UMAI-signed bundles imported as files.
- Which safety steps are in the first dashboard version (proposal: playground, golden tests,
  impact preview, rollback; shadow and staged rollout later).
