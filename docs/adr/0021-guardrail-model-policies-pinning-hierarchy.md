# ADR-0021 — Guardrail model: versioned policies pinned into guardrails; company → project → guardrail

- **Status:** Accepted
- **Date:** 2026-10-17
- **Deciders:** product owner (UMAI)

## Context

The product owner defined the product shape: policies of the L0 / L1 / L2 layers are edited,
packaged and activated as **guardrails**, which end users integrate into their agents. Lakera's
model is the reference ([`reference/lakera-guard.md`](../reference/lakera-guard.md)): a policy
lists guardrails with per-detector role toggles (user / assistant / tool / tool_call) and a
sensitivity level (L1–L4); each project has exactly one policy; templates are read-only. The
product owner approved two points (2026-10-17): guardrails **pin** the policy versions they use,
and the hierarchy is **company → project → guardrail**. ADR-0018/0019 already make tenants own
bundles and API keys.

Until now a bundle's L0 block was a flat `entities` + `on_detect` map, and the decision did not
say which rule produced it.

## Decision

1. **Terms.**
   - *Detector*: one entity type of the L0 catalog (or a custom recognizer).
   - *Policy*: a named, versioned set of detectors with one action (`BLOCK`,
     `STEP_UP_APPROVAL`, `ALLOW_WITH_MODIFICATIONS`), a severity, the **roles** it screens and a
     **sensitivity** level. Policies live in a library (control plane, F5).
   - *Guardrail*: what a project integrates. One guardrail version = one signed bundle
     (ADR-0012): its pinned policies, deterministic exceptions, allowed domains, custom
     detectors, phases, mode and failure behaviour. The bundle id is the guardrail id.
2. **Pinning.** Publishing a guardrail copies the current version of each policy into the
   bundle (`l0.policies[]` with `id` + `version`). Editing a policy in the library never
   changes a published guardrail; the console shows which guardrails use a policy and offers a
   new guardrail version. Activation is still the `active` pointer (ADR-0012/0013).
3. **Roles → phases.** `user` → `PRE_LLM`; `assistant` → `POST_LLM`; `tool_call` → `TOOL_INPUT`,
   `MCP_REQUEST`; `tool` → `TOOL_OUTPUT`, `MCP_RESPONSE`; `memory` → `MEMORY_WRITE`. A policy
   only screens its roles' phases; a type no policy screens in a phase is not detected there.
4. **Sensitivity → minimum score.** L1 0.80, L2 0.65, **L3 0.50 (default)**, L4 0.35. Shapes
   that are context-required stay context-required at every level. Measured on
   `l0-pii-tr` (dev): recall 0.61 / 0.79 / 0.99 / 0.99 at L1–L4, precision 1.000 throughout
   ([report](../../evals/reports/2026-10-17-l0-pii-tr-sensitivity-dev/summary.md)).
5. **Provenance.** Every decision lists what produced it (`decision.matched`, evaluation API
   0.5): policy id, version, name, action and entity types; exceptions and, later, L1a rules
   the same way. The reason names the deciding policy (`L0: TR_TCKN detected by policy Kimlik
   verileri v2`).
6. **One type, one policy** per guardrail (validation error otherwise), so a decision is never
   ambiguous about which policy a type belongs to.
7. **Hierarchy.** Company (tenant, ADR-0019) → projects → each project uses exactly one
   guardrail (Lakera's "one policy per project"); a guardrail can serve many projects of the
   same tenant. API keys belong to a tenant and are scoped to projects (ADR-0019); when a key is
   project-bound, the request may omit `bundle` and the project's guardrail is used (control
   plane, F5/F6). Templates are copied into a tenant, never referenced across tenants.
8. **Compatibility.** Bundles ≤ 0.5 (`entities` + `on_detect`) keep working; their provenance
   is `l0_on_detect` (`on_detect.default` / `on_detect.actions.<TYPE>`).

## Alternatives considered

| Option | Why not |
|---|---|
| Guardrails reference library policies by id at runtime (no pinning) | A policy edit would change live behaviour of every guardrail silently; bundles would no longer be self-contained signed artifacts |
| Keep the flat `entities` + `on_detect` map | No names, versions, roles or sensitivity; the console could not show Lakera-style policies, and decisions could not say which policy blocked |
| Sensitivity as raw `min_score` numbers only | Policy authors think in levels; levels also map to Lakera's model. Raw overrides stay possible (`min_score_overrides`) |
| A type in several policies, strictest wins | Ambiguous provenance and surprising edits; one owner per type is easier to reason about |
| Roles as our phase names | Lakera's roles are what users know; phases stay the engine's internal vocabulary, mapped one way |

## Consequences

- policy-bundle 0.6 (`l0.policies`, guardrail `name` / `description`), evaluation API 0.5
  (`decision.matched`), policy-tests 0.2 (`expect.matched`); demo guardrail v4 uses the policy
  form; `GET /v1/bundles/{id}/versions/{v}` serves a guardrail's content to the console.
- The console calls bundles "guardrails" and shows policies with role toggles and sensitivity.
- To build with the control plane (F5): the policy library, "used by" view, publish = pin +
  sign, project ↔ guardrail binding, project-scoped keys.
- L1 rules and L1b policies will join the same model (policies with roles) when L1 is built.

## Validation

Golden cases per guardrail assert `matched`, roles and sensitivity (demo v4: 52 cases); unit and
API tests cover rule compilation, phase filtering, validation and provenance. Revisit the level
→ score mapping with real-traffic false-positive data.
