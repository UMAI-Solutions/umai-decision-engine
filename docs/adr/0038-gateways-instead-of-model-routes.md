# ADR-0038 — Gateways instead of per-model routes: one provider connection and one guardrail per gateway, models pass through

- **Status:** Accepted (product owner, 2026-10-20: "redesign"; the guardrail is chosen when the
  gateway is created)
- **Date:** 2026-10-20
- **Deciders:** product owner; engineering proposal
- **Amends:** ADR-0034 §6 (routes as tenant configuration): the configuration unit is now the
  gateway; per-model routes survive only as optional aliases
- **Contracts:** [gateway-api](../contracts/gateway-api.md) 0.9,
  [credentials](../contracts/credentials.md) 0.5

## Context

Gateway-api 0.2 to 0.8 made the **route** the unit of configuration: one entry per model name a
caller may send, each carrying its upstream, credential mode, guardrail, streaming and refusal
settings. The first console design copied that shape: a "Models" list where an administrator
registers every model before an application can use it.

Running the pilot on umai-beta (2026-10-20) showed the cost of that shape:

- The pilot needed three nearly identical routes (`gpt-4.1-mini`, `gpt-5.4-mini`,
  `text-embedding-3-small`) that differ only in the model name. Every new provider model is
  another entry before any application can use it.
- The settings that really vary are per **provider connection** (base URL, credential mode) and
  per **application** (which guardrail protects it), not per model.
- The product owner's mental model is "deploy a gateway, choose its guardrail", which the
  per-model list did not match.

The security reasons for routes still hold: callers must never choose the upstream URL (SSRF;
the allow-list stays), and every call must have exactly one known guardrail.

## Decision

1. **The gateway is the unit.** A tenant has one or more gateways. A gateway has:
   - an id;
   - one **provider connection**: `kind`, `base_url` (defaults to `https://api.openai.com/v1` for
     `openai`), `credential` (`pass_through`, `secret` or `none`), `region`;
   - one **guardrail**;
   - the call settings that used to sit on each route: streaming mode, refusal style and text,
     unscanned parts, restoration, timeouts, destination class.

   Gateways live in one YAML file per tenant (`<UMAI_DE_GATEWAYS_DIR>/<tenant>.yaml`,
   `gateways: [...]`), hot-reloaded with last-known-good, like routes before.
2. **Models pass through.** The `model` a caller sends goes to the gateway's provider unchanged.
   - Optional `models` is an allow-list of names a caller may send (cost control).
   - Optional `aliases` map a name to a provider model (an Azure deployment name, a pinned
     version).
   - A name must match `^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$`, because it is logged; anything
     else is refused before it reaches a log.
3. **Keys bind to a gateway.** An API key may carry a `gateway_id`. The gateway is selected in
   this order:
   1. The key's gateway. A different `X-Umai-Gateway` header is refused.
   2. Otherwise (unbound key, operator token) the `X-Umai-Gateway` header.
   3. Otherwise the tenant's only enabled gateway.
   4. Otherwise the call is refused with `gateway_required`.

   A bound key whose gateway is gone or disabled is refused. It never moves to another gateway.
4. **Fallback** names another gateway of the tenant with the **same guardrail** and a credential
   other than `pass_through`. It resolves the requested model with its own aliases and allow-list.
   It is tried once on 429, 5xx, unreachable or timeout, as in gateway-api 0.6.
5. **Management.** The policy service manages gateways one at a time:
   - `GET /v1/gateways`;
   - `PUT /v1/gateways/{id}`;
   - `DELETE /v1/gateways/{id}`, refused while active keys are bound to the gateway or another
     gateway falls back to it.

   The actions are `gateway.read` and `gateway.write`, with the same roles as the route actions
   they replace. A key is created bound with `gateway_id`; its scopes then default to `gateway`.
6. **Internally** the gateway resolves (gateway, requested model) into the same per-call target
   the code already uses. Checks, masking, streaming and refusals are unchanged.

## Alternatives considered

| Option | Why not |
|---|---|
| Keep per-model routes, build the console list on them | Every new model is administration before use; the console shows the backend's shape instead of the user's task. Rejected by the product owner |
| Guardrail on the API key, no gateway object | Simple, but the provider connection and the call settings then need another home, and "deploy a gateway, choose its guardrail" has no object to point at |
| Provider prefix in the model name (`azure/<deployment>`) to pick a connection per call | Changes what applications send; one application then crosses guardrails by renaming a model. Possible later as an alias convention |
| Gateway id in the URL path (`/g/<id>/v1`) | Works with any key, but every SDK configuration carries the id and a leaked path selects a gateway; the key already identifies the caller |
| Forward any `model` without validation | The name is logged; an unchecked string could carry content into logs (ADR-0005) |

## Consequences

- **Easier.** Setup is "create a gateway (provider, guardrail), create a key, copy the snippet".
  A new provider model works the day it ships. The console needs no model list in its main flow.
- **Contract changes:** gateway-api 0.9 (gateway file, selection, errors `gateway_required`,
  `unknown_gateway`, `gateway_mismatch`, `model_not_allowed`, `gateway_misconfigured`; response
  header `X-Umai-Gateway`; `/v1/gateways` replaces `/v1/gateway/routes`) and credentials 0.5
  (`gateway_id` on key records and registry entries; snapshot payload 0.2).
- **Must be built:**
  - tenant-store migration 0003 (`api_keys.gateway_id`);
  - the renamed settings `UMAI_DE_GATEWAYS_DIR` and `UMAI_DE_GATEWAYS_POLL_INTERVAL_S`, with the
    Compose and `umai.sh` changes;
  - migrating umai-beta's route files to gateway files;
  - the console flow (`umai-control-center`).
- **Unchanged:**
  - the upstream allow-list;
  - the raw-content zone (ADR-0035);
  - masking, restoration, streaming and refusals;
  - the rule that a fallback never reuses the caller's provider key.
- **Harder:**
  - A pass-through gateway lets a caller use any model its provider key allows. Cost control is
    the optional `models` list, not a default.
  - One application that needs two providers uses two gateways and two keys.

## Validation

- Integration tests cover:
  - every selection path (bound key, header, sole gateway, `gateway_required`, `gateway_mismatch`,
    `unknown_gateway`);
  - pass-through, aliases and the allow-list;
  - gateway fallback;
  - `PUT` / `DELETE` validation and key binding;
  - the migration on SQLite and PostgreSQL.
- On umai-beta, the pilot's three routes per tenant become one gateway per tenant, and the
  end-to-end SDK check of 2026-10-20 passes unchanged.
- Revisit if customers need several providers behind one key (provider prefixes), or
  per-model guardrails.
