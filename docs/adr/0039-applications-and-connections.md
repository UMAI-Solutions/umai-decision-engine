# ADR-0039 — Applications as the protected unit; the AI gateway as one way to connect

- **Status:** Accepted (product owner, 2026-10-20: "start coding and implement the planned
  console design"); implemented, see [Implementation](#implementation)
- **Date:** 2026-10-20
- **Deciders:** product owner; engineering proposal
- **Amends:** ADR-0038 (the gateway object becomes the application, providers move to the AI
  gateway's own list); the evaluation API (an application's guardrail binds its API calls)
- **Analysis:** [console information architecture](../architecture/console-information-architecture.md)

## Context

UMAI is an AI runtime security product: guardrails decide inline whether an AI interaction may
proceed and review it afterwards. The gateway analysis (A17, [ai-gateway.md](../architecture/ai-gateway.md)
§1) positioned UMAI as *the guardrail and decision layer for regulated Turkish and EU AI traffic*,
with the gateway as one way to deliver it. ADR-0038 then made the **gateway** the object an
administrator configures: one provider, one guardrail, keys bound to it. A console built on that
object puts routing in front and the guardrail behind. The product owner rejected that
(2026-10-20): the gateway is a secondary product; providers belong under it.

The console analysis found:

- The questions of the administrator and the compliance officer are asked per protected
  application ("which application sent personal data, where"), whatever path its requests take.
- One application may reach UMAI through the gateway, the evaluation API, or both. Today the API
  caller names the guardrail in each request, so one application can be protected by different
  guardrails on different paths.
- Comparable products keep provider credentials apart from the object that carries policy
  (Portkey, Kong, LiteLLM, Azure API Management). AI runtime security products protect one
  object with one policy (CrowdStrike AIDR collector, HiddenLayer and Lakera project, Prisma AIRS
  application) and list a gateway as one connection type among SDK, MCP and browser (CrowdStrike).
  Prisma AIRS gives its gateway its own workspaces, keys and policies, which splits one
  application across two places; its provider connections live only inside the gateway.

## Decision

1. **The application is the protected unit.** It has a guardrail, keys and optional gateway
   options (allowed providers, model names, aliases). It is connected through the AI gateway, the
   evaluation API, or both. Detections are attributed to it.
2. **One guardrail per application on every path.**
   - On the gateway, the application's guardrail applies (as for ADR-0038's gateway).
   - On the evaluation API, a key bound to an application uses the application's guardrail. A
     request naming another guardrail is refused (`guardrail_mismatch`).
   - An unbound key or the shared token names the guardrail as today.
3. **The AI gateway is a connection method with its own settings.**
   - It has the organization's provider list: name, which is also the `@name/` model prefix;
     type; address on the allow-list; provider-key handling; region; one default.
   - It has defaults for refusal style, streaming, unscanned content and timeouts, which an
     application may override.
   - A bare model name goes to the default provider; `@<provider>/<model>` goes to that provider.
4. **Keys bind to an application**, with ADR-0038's selection rules renamed:
   1. the key's application;
   2. otherwise `X-Umai-Application`;
   3. otherwise the organization's only application;
   4. otherwise `application_required`.

   One key is valid on all connections of its application.
5. **"Gateway" names the product area and its address only.**
6. **The console is organized as Protect** (guardrails, applications, playground), **Investigate**
   (detections, reports), **Connect** (AI gateway, API and SDKs, integrations) **and
   Organization**. Overview is the security posture.

## Alternatives considered

| Option | Why not |
|---|---|
| Keep ADR-0038's gateways as the configured object | Puts routing before protection; one application on two providers needs two gateways; contradicts A17 |
| Two products side by side ("Guard" and "Gateway"), each with applications and keys | Duplicates applications and keys for an application on both paths; makes the guardrail one of two equals |
| Providers at the organization's top level | Every customer meets providers, including those who never use the gateway |
| Let API calls keep naming any guardrail | The same application could be protected differently per path; evidence per application becomes unreliable |
| Provider prefix without `@` | Ambiguous with model names that contain a slash |

## Consequences

- **Engine changes, within the 0.x contracts:**
  - gateway-api 0.10: `applications:` and `providers:` in the tenant file, `/v1/applications`,
    `/v1/providers`, `X-Umai-Application`, prefix resolution, gateway defaults, new errors;
  - credentials 0.6: `application_id` on keys, migration 0004;
  - evaluation-api 0.10: the guardrail comes from a bound key's application;
  - gateway logs for refusals before the guardrail.

  Masking, restoration, streaming and refusal behaviour do not change.
- **WP6 detection records** must carry the application, key, connection, provider, model and entity
  types, or the console's Investigate section cannot be built.
- **umai-beta** moves to one OpenAI provider and one application per tenant; the bound key keeps
  working.
- **Console:** Protect and Connect can be built now. Overview's posture, Detections and Reports
  need WP6 and stay hidden until then; the setup steps and "Explain an error" work today.

## Validation

- **Usability:** the four flows of the analysis, tested with the pilot customer's
  administrator, a developer and the compliance contact. Target: the first protected request
  within 10 minutes without help, and the compliance contact finding a given detection.
- **Engine:** integration tests for application selection, `guardrail_mismatch` on the API,
  prefix resolution and key binding.
- **Revisit** if a customer needs different guardrails for one application on different paths,
  or per-model guardrails.

## Implementation

2026-10-20, engine branch `feat/applications` and console branch `feat/applications-console`:

- **Engine:** contracts gateway-api 0.10, credentials 0.6 (snapshot 0.3; 0.2 snapshots with
  `gateway_id` still verify), evaluation-api 0.10; one file per tenant,
  `<UMAI_DE_APPLICATIONS_DIR>/<tenant>.yaml`, read by the engine and the gateway
  (`applications.py`) and written by the policy service (`/v1/applications`, `/v1/providers`,
  `/v1/gateway/settings`); migration 0004 renames `api_keys.gateway_id` to `application_id`;
  the gateway logs refusals before the guardrail as `gateway.refused` (code and request id).
- **Console:** the structure of Decision 6 without Reports and Integrations (WP6, Q58); Overview, Applications
  (add, guardrail, connection snippet with the first key, gateway options, keys), AI Gateway
  (Overview, Providers, Settings), API & SDKs, Detections (explain an error).
- **Answered with the design:** Q53 (this ADR), Q55 (an application's guardrail binds its API
  calls), Q58 (WP6 sections hidden). **Built as proposed, still open:** Q56 (a built-in OpenAI
  provider, pass-through, is the default until another provider is marked default).
  **Not built:** Q54 (developer role; administrators create keys), Q57 (per-application Monitor
  only).
- **Not in the console yet:** aliases, fallback and per-application overrides are edited in the
  file or through the API; the "Stored in UMAI" provider key (Q47).
