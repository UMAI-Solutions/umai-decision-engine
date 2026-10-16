# `umai-engine` today

Background only: a snapshot, taken 2026-10-15, of `umai-engine` (FastAPI, Python 3.12),
the engine this project may eventually replace. There is no connection to that codebase;
this file exists so design choices do not make a future replacement impossible.

## Where it sits

```text
browser extension / Agent SDK / Copilot Studio bridge
        │
        ▼
umai-service  (public API: tenant, environment, project, guardrail resolution, audit)
        │  POST /internal/ai-engine/v1/evaluate   (InternalRequest → InternalResponse)
        ▼
umai-engine   (internal only)
        │  snapshot by (tenant, env, project, guardrail_id, version) from Redis (or dummy store)
        │  HMAC-SHA256 snapshot signature verified when SNAPSHOT_SIGNING_KEY is set
        ▼
pipeline → decision
```

## Pipeline (`app/core/pipeline.py`)

1. Load `GuardrailSnapshot`. Missing → `BLOCK`, error `CONFIG_MISSING`.
2. **Preflight** (`HeuristicPolicy("preflight")`): regex/exact rules on the preflight
   target. `BLOCK` → return immediately.
3. **AGT** (`agt_adapter.evaluate_agt_policy`): deterministic rules for agent-action phases
   `TOOL_INPUT`, `MCP_REQUEST`, `MEMORY_WRITE`. Requires metadata per phase
   (`agent_id`, `action`, `tool_name` / `server_name`, `method` / `memory_scope`). Knows
   high-impact actions (`write`, `delete`, `export`, `send`, `share`, `publish`,
   `permission-change`, `rotate-credentials`, …) and dangerous MCP methods. `BLOCK` →
   skip the rest.
4. **Policies in parallel** (`asyncio` tasks) for enabled policies whose `phases` include
   the request phase:
   - `HEURISTIC`: rules with `block_on_match`, `step_up_on_match`, `redact_on_match`
     (→ `ALLOW_WITH_MODIFICATIONS` with `modified_text`), otherwise `FLAG`;
     `max_length` exceeded → `BLOCK`.
   - `CONTEXT_AWARE`: LLM classifier over an OpenAI-compatible chat endpoint
     (`instructions` + `definitions_and_category_map` + `examples` as system prompt,
     `Content: <text>\nAnswer:` as user prompt, temperature 0). Parses JSON
     `{violation, policy_category, confidence: low|medium|high, rationale}`.
     `violation=0` → `ALLOW`; category in `step_up_categories` → `STEP_UP_APPROVAL`;
     confidence ≥ `min_confidence_for_block` → `BLOCK`; else `FLAG`. Errors →
     `fail_closed_on_error` (default `true` → `BLOCK`). Skipped with `LLM_DISABLED` when
     `flags.allow_llm_calls` is false.
   - First `BLOCK` cancels the remaining tasks.
5. **Merge** (`_build_decision`), in this order: any `BLOCK` → `BLOCK`; any `ERROR` →
   `BLOCK` (or `ALLOW_WITH_WARNINGS` in `MONITOR`); `STEP_UP_APPROVAL`;
   `ALLOW_WITH_MODIFICATIONS`; `FLAG` → `ALLOW_WITH_WARNINGS`; else `ALLOW`.
   Ties pick the most severe.

## LLM client

`app/core/llm_client.py`: httpx, OpenAI-compatible `/chat/completions`, retries 429/502/
503/529 up to 3 attempts with backoff (≤ 2 s, honours `Retry-After`). Auth via
`llm_config.auth` (`bearer` / `header` / `none`, secret from an env var named in the
snapshot) or legacy env lookup by host (Groq, OpenRouter, OpenAI, Hugging Face).
The deployed context-aware model is `openai/gpt-oss-safeguard-20b`.

Note for this project: retries inside a runtime budget are dangerous; L1b adapters do not
retry (the pipeline decides on fallback). The L2 worker retries with backoff because nothing
waits on it.

## Mapping to the new layers

| `umai-engine` | `umai-decision-engine` |
|---|---|
| Preflight heuristic | L1a `legacy_preflight` (compat) |
| Regex PII rules in guardrail library | L0 recognizers (Presidio) |
| AGT rules | L1a rules (fact paths `action.*`, `mcp.*`, `memory.*`) |
| `HEURISTIC` policies | L1a rules (incl. `redact` effects) or L0 custom recognizers |
| `CONTEXT_AWARE` policies | L1b decision policies at runtime; the LLM judge moves to asynchronous L2 review |
| Parallel policies, first BLOCK wins | Ordered layers, merge with floor (see merge doc) |
| `fail_closed_on_error` per policy | `on_evaluation_failure` per bundle (L2 failures never affect decisions) |

## Background: `umai-engine` SLO and failure policy

Summary of the SLO and failure-policy rules `umai-engine` was built against (v1,
2026-08-31). This engine adopts the failure-policy rules; the latency figures are not
commitments here.

- **Measured span:** from `umai-service` accepting the request to the decision being
  written. Client network excluded.
- **Deterministic path** (no LLM): p95 ≤ 400 ms, p99 ≤ 800 ms. The only latency figure
  that may be given to customers, and only after measurement.
- **LLM path:** p95 ≤ 2 500 ms is an observation target, not a commitment; hard timeout
  1 500 ms per model call (`timeout_ms or 1500` today).
- **No blended percentile** across LLM-on and LLM-off guardrails is published.
- **Failure behaviour belongs to the guardrail**, field `on_evaluation_failure`:
  `fail_closed` (default, `BLOCK`, reason `EVALUATION_UNAVAILABLE`), `fail_open` (`ALLOW`,
  audited as `degraded_allow`, separate metric), `step_up`.
- `step_up` only where the client has a UI; elsewhere it behaves as `fail_closed`.
- `MONITOR` mode always returns `ALLOW` by definition.
- Metrics expected: decision latency by route × LLM on/off × p50/p95/p99; per-layer
  latency L0/L1/L2; L2 timeout rate; failure decisions by policy value; degraded rate;
  step-up outcomes. Tenant labels on counters only.
