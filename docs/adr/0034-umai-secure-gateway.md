# ADR-0034 — UMAI secure gateway: an OpenAI-compatible pass-through proxy in this repository, guardrails in and out

- **Status:** Accepted (product owner, 2026-10-19: a first customer, UMAI's own gateway, the
  technology below). Nothing implemented yet; build plan WP2–WP5 in the design. §6 amended by
  ADR-0038 (gateways instead of per-model routes, 2026-10-20)
- **Date:** 2026-10-19
- **Deciders:** product owner; engineering proposal
- **Builds on:** ADR-0004 (decision models), ADR-0009 (Python stack), ADR-0012 (signed bundles),
  ADR-0018 / ADR-0019 (editions, entitlements), ADR-0023 (API keys), ADR-0025 (tenant store),
  ADR-0027 (key custody), ADR-0028 (metering), ADR-0030 (events), ADR-0031 / ADR-0032 (Azure,
  single-VM pilots)
- **Companions:** ADR-0035 (raw-content zone, amends ADR-0005), ADR-0036 (checks after masking,
  amends ADR-0014)
- **Answers:** Q45
- **Design:** [`architecture/ai-gateway.md`](../architecture/ai-gateway.md) · **Contract:**
  [`contracts/gateway-api.md`](../contracts/gateway-api.md) · **Facts:**
  [`reference/ai-gateways.md`](../reference/ai-gateways.md) (verified 2026-10-19)

## Context

- A customer asks for a secure AI gateway that resists prompt injection, is tested in Turkish
  and supports KVKK compliance, in UMAI Cloud (EU processing accepted) and on-prem. Their upstreams
  are OpenAI, Azure OpenAI and models on their own servers; their traffic is streaming chat, API
  calls and agents with tool calls. No such product was found in Türkiye (design §1.1).
- Today the engine is a decision service: callers send content to `POST /v1/evaluate` and enforce
  the decision themselves. Phases (`PRE_LLM`, `POST_LLM`, `TOOL_*`, `MCP_*`), guardrail roles,
  masking with typed placeholders and per-tenant keys exist; L1 does not.
- The plain AI gateway is a commodity (LiteLLM MIT, agentgateway and Agent Router Apache-2.0,
  bundled in Azure API Management and Cloudflare) and is consolidating into security platforms
  (Palo Alto Networks bought Portkey). Differentiation is in the guardrail: Turkish and EU
  identifiers, policy-as-data, provenance, no prompt logging, self-hosting, reversible masking.
- LiteLLM, the obvious base to inherit, keeps per-key guardrails in its Enterprise licence, would
  hold a second copy of tenants and keys, can only carry a mask map across its pre- and post-call
  hooks through a shared store, and had a PyPI compromise (2026-03-24) and two serious CVEs in 2026.
- All of the customer's upstreams speak the OpenAI wire format (Chat Completions, Responses).

## Decision

1. **UMAI ships a secure AI gateway** as a second delivery form of the guardrail engine (design
   option B). Guardrail integrations into other gateways (option A) follow once the evaluation API
   has per-message masks. UMAI does not build a full LLMOps gateway (option C: load balancing,
   semantic caching, prompt management, cost optimisation).
2. **Technology.** UMAI's own code in this repository: package `gateway/`, entry point
   `umai_decision_engine.gateway.app:app` on its own port, in the existing image. Python 3.12,
   FastAPI / uvicorn, httpx `AsyncClient` with explicit connect, read, write and pool timeouts.
   The guardrail pipeline is called **in-process** (`Evaluator`), never over HTTP. Request and
   response bodies are handled as JSON passed through unchanged except for the fields the contract
   names; server-sent events are parsed by the gateway itself. No gateway product (LiteLLM, Kong,
   Envoy / Agent Router, agentgateway) is embedded. The first stage needs no new runtime
   dependency; the official `openai` Python SDK becomes a development dependency for compatibility
   tests.
3. **Surfaces, natively, no translation between provider formats.** Contract `gateway-api` 0.1:
   OpenAI Chat Completions (`POST /v1/chat/completions`, `GET /v1/models`). Next minor: OpenAI
   Responses. Anthropic Messages when a customer needs it, as its own surface.
4. **Upstreams:** `openai`, `azure_openai` (v1 API), `openai_compatible` (vLLM, Ollama and other
   self-hosted servers). Base URLs must match a per-deployment allow-list (HTTPS, no private
   addresses unless a self-hosted operator lists them).
5. **Credentials.** The caller authenticates with a per-tenant UMAI key carrying the `gateway`
   scope. Provider credentials are passed through per call (`X-Umai-Provider-Key`) and never
   stored or logged, or not needed (`none`). Stored provider keys (envelope-encrypted, ADR-0027)
   are a later decision (Q47).
6. **Routes are tenant configuration, not policy.** A route names the upstream, model, credential
   mode, guardrail bundle, stream mode, refusal style and timeouts; it lives in the tenant store and
   is hot-reloaded. The guardrail itself stays in the signed bundle (AGENTS rule 8). Every decision
   event records the route id.
7. **Guardrails on every hop of a call:** `PRE_LLM` over every message the gateway forwards
   (independent of the bundle's `l0.target`, which can only widen), `TOOL_OUTPUT` for tool results,
   `POST_LLM` for answers, `TOOL_INPUT` for tool-call arguments. One placeholder namespace per call;
   restoration of input placeholders in answers by exact match only. Streaming modes `buffer`,
   `window`, `after`.
8. **Failures.** A guardrail failure is a decision under the bundle's failure policy. Upstream
   errors pass through in the provider's shape, are recorded, and are not guardrail decisions. No
   retries inside the guardrail budget.
9. **No content at rest.** Prompts, answers, provider keys and mask maps are never logged, stored
   or put in events; usage (provider token counts) and decisions are recorded content-free.
10. **Escape hatch.** If benchmarks show the Python data plane is the bottleneck, agentgateway runs
    in front and calls UMAI through external processing (one stream per request keeps the mask map
    in one process). That change gets its own ADR.

## Alternatives considered

| Option | Why not |
|---|---|
| Inherit LiteLLM's proxy (UMAI as its guardrail) | Per-key guardrails are Enterprise-licensed; tenants and keys live in two places; reversible masking needs raw values in a shared store between hooks (against ADR-0005); generic-guardrail streaming undocumented; 2026 supply-chain compromise and CVEs are a hard sell to a bank's security review |
| LiteLLM as a library (provider translation only) | Same package as the compromised releases, heavy dependency tree, translation drops provider features; the customer's upstreams need no translation |
| Fork LiteLLM | A fast-moving code base to maintain and patch for features the customer is not buying |
| Envoy-based data plane now (Agent Router, agentgateway + external processing) | Second runtime and language, Kubernetes weight for single-server on-prem installs; no measured need yet. Kept as the escape hatch (§10) |
| Kong or Azure API Management as the data plane | Kong's custom guardrail is Enterprise and allow / block only; APIM's built-in safety policy calls only Azure AI Content Safety and is not available on-prem |
| Full AI gateway (option C) | Commoditised, competes with the platform-first priority; prompt logs and caching collide with AGENTS rule 1 |
| Integrations only (option A) | Leaves customers without a gateway (banks, on-prem) unserved and puts masking limits in other vendors' hooks |
| Normalise every provider to one internal format | Loses provider features; Anthropic calls its own OpenAI layer not production-ready |

## Consequences

- To build: WP2 (non-streaming Chat Completions, routes, pass-through keys, input and answer
  checks, masking, refusals, `MONITOR`), WP3 (streaming, restoration), WP4 (Turkish attack set,
  injection detector, L1), WP5 (tool checks, Responses), WP7 console pages, WP8 pilot deployment.
- Contract changes that land with their code in WP2: evaluation API per-message masks and
  widening scan, credentials `gateway` scope, tagged-action `route_id` and `source: gateway`.
- UMAI is in the customer's availability path: SaaS gateway customers need the regional stack
  (ADR-0031); the single-VM starter serves pilots only (ADR-0032), starting in `MONITOR` mode.
- New threat surface: SSRF through upstream URLs, cost abuse with a stolen UMAI key, stream
  exhaustion, provider keys in transit; the gateway inherits the supply-chain posture of the engine.
- Open product questions: Q47 (stored keys), Q48 (refusal default), Q49 (metering unit), Q50
  (stream default), Q51 (KVKK claim), Q52 (injection before launch).

## Validation

- Compatibility: the official `openai` SDK, pointed at the gateway with `base_url`, passes the
  contract tests unchanged, streaming included.
- Benchmarks (AGENTS rule 6): added p95 latency per call against a fake upstream, time-to-first-
  token overhead per stream mode, L0 cost of window scans.
- Leak suite over gateway logs, events, error bodies; cross-tenant tests for routes.
- `masking-survival` eval per upstream kind before restoration is offered; `pi-tr` rates before
  injection resistance is claimed.
- Revisit if the Python data plane is the measured bottleneck, if customers need upstreams beyond
  the OpenAI wire format, or if Q48–Q50 change the defaults.
