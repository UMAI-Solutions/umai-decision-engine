# ADR-0035 — The raw-content zone is L0 and the gateway forwarder

- **Status:** Accepted (product owner approved WP1 of the gateway plan, 2026-10-19)
- **Date:** 2026-10-19
- **Deciders:** product owner; engineering proposal
- **Amends:** ADR-0005 (raw PII never leaves L0)
- **Builds on:** ADR-0034 (UMAI secure gateway)

## Context

ADR-0005 lets raw content exist only in L0: every other layer, log, metric, trace, event, eval
report and provider call receives redacted text, entity types, spans and salted hashes. The API
boundary already receives raw content for `POST /v1/evaluate` and hands it straight to L0.

A gateway (ADR-0034) handles raw content by definition. It receives the caller's prompt, forwards
what the guardrail allowed to the upstream model the tenant chose, receives the answer, and puts
the caller's own values back in place of the placeholders it sent upstream. Reversible masking
needs the placeholder → value map for the life of one call, including a stream. None of this can
be done with redacted text only, and moving it into `l0/` would make L0 an HTTP proxy.

## Decision

1. **The raw-content zone is `l0/` and `gateway/`.** In the gateway it covers request and response
   bodies, stream buffers and the per-call mask map. Every other module keeps ADR-0005 unchanged:
   L1a, L1b, L2, session history, events, logs, metrics, traces, errors, eval reports and tests
   receive redacted forms only.
2. **Raw content leaves the zone only**
   - back to the calling application (answers, restored placeholders), and
   - to the upstream configured for the call's route, and only content the guardrail allowed for
     that hop: unchanged for `ALLOW` / `ALLOW_WITH_WARNINGS`, masked for
     `ALLOW_WITH_MODIFICATIONS`, nothing for `BLOCK`. In `MONITOR` mode content is forwarded
     unchanged by definition and the would-have decision is recorded.
3. **Memory only, one call long.** No persistence, cache, temporary file, log line, metric label,
   exception message or event carries raw content or a mask map. The map is created when the call's
   input is masked and dropped when the call ends; it is never shared between calls, replicas or
   processes.
4. **Provider credentials** passed through by the caller follow the same rule as raw content: used
   for the upstream request, never logged, stored or echoed.
5. **Module boundary.** `gateway` may import `pipeline`, `contracts`, `tenancy`, `settings`; it must
   not import `l1` or `l2` internals; nothing imports `gateway`. It logs only allow-listed fields
   (ids, route, entity types, decisions, latencies, status codes, token counts; ADR-0030), never a
   body, header value or exception message from an upstream.
6. **Processing region of the upstream** is the tenant's choice, recorded per route; a regional
   stack forwards only to upstreams whose declared region the tenant's configuration allows.

## Alternatives considered

| Option | Why not |
|---|---|
| Keep ADR-0005 literal; the gateway calls the engine over HTTP | Raw content still sits in the gateway process and crosses one more hop; nothing gained |
| Keep the mask map in Redis so any replica can restore | Raw personal data at rest in a shared store, against the whole point of ADR-0005 |
| Encrypt values into the placeholders and decrypt on the way back | Ciphertext reaches the provider and its logs; key management for no benefit when the map never leaves one call |
| Put the forwarder inside `l0/` | Turns entity detection into an HTTP proxy and couples its latency budget to upstream calls |

## Consequences

- AGENTS rule 1, `architecture/code-layout.md` (boundaries) and the threat model (new trust
  boundary gateway ↔ upstream, provider keys as an asset) are updated in the same change as this ADR.
- The leak suite must cover gateway logs, events, error bodies and admission errors before the
  gateway serves a customer (WP2 exit).
- Placeholder restoration must be designed so a stream holds back incomplete placeholders, not
  raw values, beyond what the stream mode already holds (WP3).

## Validation

Leak suite over every gateway output except the response body to the caller and the upstream
request body; a test that gateway logs for a call with synthetic identifiers contain none of them;
an import-boundary check for `gateway`.
