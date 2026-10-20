# ADR-0037 — Per-message L0 scanning with a detection cache; a gateway input limit of its own

- **Status:** Accepted (product owner approved the N14 plan, 2026-10-20)
- **Date:** 2026-10-20
- **Deciders:** product owner, engine team

## Context

- The gateway checks the whole conversation on every call (target `FULL_HISTORY`, ADR-0034 §7):
  clients resend the full history each turn, and earlier turns carry the caller's real values
  (restored by the gateway), which must be masked again before the next upstream call.
- The scanned text was capped at `UMAI_DE_MAX_INPUT_CHARS` (32 000 characters, about 8 000
  tokens; evaluation API 0.9, gap T2). A chat with 15–20 long turns reached it and got
  413 `input_too_large` (note N14 in [`ai-gateway.md`](../architecture/ai-gateway.md)).
- Measured on the pilot VM (umai-beta, Azure `Standard_B2s`, 2026-10-20). One PRE_LLM check over
  a synthetic conversation, guardrail `gr-demo-data-protection`, in the gateway container:

  | Scanned characters | 1 235 | 8 049 | 32 064 | 64 041 | 128 056 | 256 060 |
  |---|---|---|---|---|---|---|
  | p50 per check | 4.9 ms | 22.4 ms | 91.8 ms | 185.8 ms | 371.9 ms | 773.2 ms |

  The cost is linear, about 2.9 ms of CPU per 1 000 characters, and single-threaded.
  - Raising the limit alone would make every turn of a long chat pay for the whole history:
    0.4 s at 128 k characters.
  - Over a conversation this grows quadratically.
  - A single new message costs about 5 ms.

## Decision

1. **Per-segment scanning.** When L0's target is made of several parts (messages, attachment
   parts; `FULL_HISTORY`, `ATTACHMENTS`, `FULL_CONTEXT`), L0 scans each part on its own and
   reports spans in `"\n\n".join(parts)`.
   - Nothing changes for callers: offsets, placeholders numbered across the whole text,
     redacted text and summary are as before.
   - A match and a context word no longer reach across a message boundary.
   - `LAST_MESSAGE` is one part and is unchanged.
2. **Detection cache.** Each process keeps an LRU map from a scanned part to what L0 found in it:
   - **Holds:** entity type, span, score, recognizer, `validated` and value hash. Never the text
     or a value.
   - **Key:** an HMAC, under a secret that lives only in that process, over:
     - the tenant's hashing key;
     - the phase's L0 configuration (JSON);
     - the language;
     - the part's text.
   - **Size:** `UMAI_DE_L0_CACHE_ENTRIES` entries (default 20 000; `0` disables it).
   - **Lifetime:** memory only, lost at restart.
   - **What goes in:** input checks only (`PRE_LLM`, `TOOL_OUTPUT`), the texts a conversation
     resends. Answer and tool-call checks are one-off (a streamed answer is checked every 32
     characters) and would only push conversation entries out (review fix, 2026-10-20).

   A resent history is therefore not scanned again; a turn costs its new parts.
3. **The gateway has its own input limit.**
   - `UMAI_DE_GATEWAY_MAX_INPUT_CHARS` defaults to 256 000 characters (about 64 000 tokens).
   - The evaluation API keeps `UMAI_DE_MAX_INPUT_CHARS` (32 000).
   - On the single VM, Caddy's body cap is 4 MB for the gateway paths and stays 512 KB elsewhere.

## Alternatives considered

| Option | Why not |
|---|---|
| Raise the limit only | Every turn rescans everything: 0.4–0.8 s of CPU per call at 128–256 k characters on the pilot VM, quadratic per conversation, and one busy chat holds a core |
| Scan only the new messages and trust the history | The gateway is stateless and cannot tell which messages it has checked; the history carries real values restored for the caller, which would reach the upstream unmasked (ADR-0035) |
| One evaluation per message, strictest decision wins | Changes decision semantics: count-based rules and, later, L1 need the conversation as a whole. Caching detection keeps the decision on the full set of entities |
| Session state in the gateway (masked history per session) | Needs a store for derived raw-content data (ADR-0035 keeps the zone in memory) and a session id that clients do not reliably send |
| Shared cache (Redis) | Only pays off with several replicas; puts derived data in a store. Revisit when the gateway scales out |

## Consequences

- A turn of a long chat costs about its new messages: the new user message and the previous
  answer, whose restored form the gateway has not scanned yet. The first call of an existing
  long history (or a large pasted document) still pays the full scan, up to about 0.8 s at the
  limit on the pilot VM, inside the default 1.5 s evaluation budget.
- The cache is not a policy cache (rule 8): its key includes the L0 configuration, so a new
  bundle version misses instead of serving an old result.
- Memory: about 20 000 small entries, a few MB per process. Each process (engine API, gateway)
  owns its cache; replicas do not share one, which only lowers the hit rate.
- Context words in the previous message no longer raise a score. The `l0-pii-tr` dev split (one
  text per case) is unchanged: identical to the catalog report of 2026-10-17
  ([report](../../evals/reports/2026-10-20-l0-pii-tr-n14-segments/summary.md)). There is no
  multi-message L0 eval set yet; it is needed before tuning context across turns.
- The engine API scans `FULL_HISTORY` targets per message too, so the API and the gateway decide
  the same content the same way.
- Denial of service: a cold 256 000-character call costs about 0.8 s of CPU on the pilot VM.
  Per-key rate limits (ADR-0023) bound it. Edition limits come with entitlements (ADR-0019).
- To build later:
  - cache hit/miss metrics;
  - a multi-message L0 eval set;
  - a per-entitlement gateway limit.

## Validation

- **Tests:** unit tests in `tests/unit/test_detection_cache.py` cover:
  - one part equals one text;
  - spans refer to the joined text;
  - a resent history hits the cache;
  - no reuse across tenants or configurations;
  - no text in the cache;
  - the cache is bounded.
- **Gateway test:** a 3 500-character conversation passes with an engine-API limit of 1 000.
- **Pilot benchmark** (pilot VM, 2026-10-20): a conversation grown turn by turn, each turn
  adding a user message and the previous answer before the gateway checks the whole history:

  | History | 8.6 k | 33 k | 65 k | 130 k | 256 k |
  |---|---|---|---|---|---|
  | Check per turn, with the cache | 19 ms | 36 ms | 11 ms | 20 ms | 23 ms |
  | Full scan, before (Context) | 25 ms | 92 ms | 186 ms | 372 ms | 773 ms |

- **End to end** (public hostname, OpenAI SDK):
  - a 100 k-character chat took 331 ms in the pipeline on the first call and 5 ms on the
    next turn;
  - a 250 k-character call passes;
  - a 300 k-character call gets 413 `input_too_large` from the gateway;
  - a 700 KB body reaches the gateway, while Caddy's 512 KB cap stays on the engine paths.
- **Revisit when:**
  - the hit rate in production is low;
  - the gateway runs several replicas;
  - a multi-message eval shows recall lost at message boundaries.
