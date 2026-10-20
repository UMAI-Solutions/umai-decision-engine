# AI gateways: market, guardrail channels, integration hooks

**Last verified:** 2026-10-19 · **Used by:** [`architecture/ai-gateway.md`](../architecture/ai-gateway.md)
(proposal), Q45–Q52 · Labels: see [README](README.md). Gathered by web research on 2026-10-19;
the LiteLLM decision-model guardrail and the Portkey acquisition were re-checked on the primary
page the same day. Prices and deal values are vendor or press figures, not ours.

## 1. Summary

- **A plain AI gateway is a commodity.** Routing, fallback, caching, budgets and an
  OpenAI-compatible endpoint are free or bundled: LiteLLM (MIT outside `enterprise/`), Agent
  Router (formerly Envoy AI Gateway) and agentgateway (Apache-2.0), Cloudflare (core features
  free), Azure API Management (all tiers).
- **Gateways are being absorbed by security and payments platforms.** Palo Alto Networks closed
  the Portkey acquisition on 2026-05-29 and sells it as the Prisma AIRS AI Gateway; Stripe
  signed for OpenRouter (2026-08-19, ≈ $7.5 B, *secondary source*, closing unconfirmed); Helicone
  was bought by Mintlify and put into maintenance mode.
- **Guardrail-inside-a-gateway is an established channel.** LiteLLM lists ≈ 50 guardrail
  providers, Portkey 20 partners plus a bring-your-own webhook, Kong vendor plugins plus a
  generic HTTP guardrail plugin. Acquired security vendors kept these listings.
- **L1b-style checks are already a gateway feature.** LiteLLM ships a *Decision Model
  Guardrail* that asks yes/no questions of any `/v1/decisions` model (TypeSafe Jev, OpenAI,
  Microsoft Foundry, Databricks, Perplexity) on chat completions, messages and responses, with
  `pre_call` / `during_call` / `post_call`, actions `block` (HTTP 400) or `log`, thresholds,
  `fail_closed` / `fail_open`, and streaming sampling. It cannot redact
  ([docs](https://docs.litellm.ai/docs/proxy/guardrails/decision_model), checked 2026-10-19).
- **Presidio PII masking is standard gateway equipment** (LiteLLM, Traefik; agentgateway has its
  own). Turkish identifiers with checksums and context, policy-as-data and decision provenance
  are not.
- **No Turkish AI gateway or LLM firewall product was found** (searches in English and Turkish;
  absence of evidence).

## 2. Gateways

| Gateway | Owner, licence | Deployment | Guardrails | MCP / agents | Price model |
|---|---|---|---|---|---|
| LiteLLM | BerriAI; MIT except `enterprise/` ([LICENSE](https://github.com/BerriAI/litellm/blob/main/LICENSE)) | Self-hosted proxy (Python) | ≈ 50 providers incl. Lakera, Lasso, Pangea / CrowdStrike AIDR, Prisma AIRS, Zscaler, Noma, HiddenLayer, Purview, Model Armor, Bedrock, Presidio, decision model ([index](https://docs.litellm.ai/docs/guardrail_providers)); per-key and model-level guardrails are Enterprise ([docs](https://docs.litellm.ai/docs/proxy/guardrails/quick_start)) | MCP, tool permissions | Enterprise price **unverified** (competitor estimate only) |
| Portkey → Prisma AIRS AI Gateway | Palo Alto Networks, closed 2026-05-29 ([press](https://www.paloaltonetworks.com/company/press/2026/palo-alto-networks-completes-acquisition-of-portkey-to-secure-ai-agents)); ≈ $117 M per PANW 10-K ([SEC](https://www.sec.gov/Archives/edgar/data/0001327567/000132756726000023/panw-20260731.htm)); GA 2026-07-16 ([blog](https://paloaltonetworks.com/blog/2026/07/announcing-general-availability-of-prisma-airs-ai-gateway)) | SaaS | 20 partner guardrails (paid plans) and a bring-your-own webhook ([list](https://portkey.ai/docs/aigw/product/guardrails/list-of-guardrail-checks), [docs](https://portkey.ai/docs/product/guardrails)) | LLM, MCP, A2A, agent identity | Not public. Open-source gateway licence after the deal **unverified** |
| Kong AI Gateway | Kong; gateway Apache-2.0 ([GitHub](https://github.com/Kong/kong)) | Self-hosted or Konnect | AWS / Azure / GCP guardrails, Lakera, NeMo, PII sanitizer, regex and semantic prompt guards; **AI Custom Guardrail** (3.14+, any HTTP guardrail) and the Lakera plugin are Enterprise ([catalog](https://developer.konghq.com/ai-gateway/policies.md), [custom](https://developer.konghq.com/plugins/ai-custom-guardrail/)) | MCP, A2A | Commercial |
| Cloudflare AI Gateway | Cloudflare | Edge SaaS only | Llama Guard on Workers AI; DLP; no third-party guardrails ([guardrails](https://developers.cloudflare.com/ai-gateway/features/guardrails/)) | — | Core free; fee on unified-billing credits ([pricing](https://developers.cloudflare.com/ai-gateway/reference/pricing/)) |
| Azure API Management | Microsoft | Azure | `llm-content-safety` calls **only Azure AI Content Safety**; token limits, semantic cache, load balancing, circuit breaker ([Learn](https://learn.microsoft.com/azure/api-management/genai-gateway-capabilities)) | MCP servers, A2A | Included in APIM tiers |
| Agent Router (formerly Envoy AI Gateway) | Apache-2.0; moved to the Agentic AI Foundation 2026-09-09 ([AAIF](https://aaif.io/blog/agent-router-joins-aaif)) | Kubernetes | None listed ([docs](https://theagentrouter.ai/docs/capabilities/)) | MCP; A2A preview | Free |
| agentgateway (Solo.io) | Apache-2.0, Rust; AAIF since 2026-06-04 ([blog](https://agentgateway.dev/blog/2026-06-04-agentgateway-joins-aaif/)) | Self-hosted | Built-in PII masking and regex; webhook guards can reject or rewrite; streamed output can be rejected, not masked ([docs](https://agentgateway.dev/docs/standalone/main/integrations/llm/guardrails/)) | LLM, MCP, A2A | Enterprise sales-led |
| OpenRouter | Stripe deal signed 2026-08-19 (*secondary source*, [The Stack](https://www.thestack.technology/stripe-acquires-openrouter-setting-up-a-battle-for-ai-routing-business/)) | SaaS | Own checks only: budgets, allow-lists, ZDR, EU region (Business+), PII redact / block, regex injection check ([docs](https://openrouter.ai/docs/guides/features/guardrails)) | — | Fee on credits ([pricing](https://openrouter.ai/pricing)) |
| TrueFoundry | Commercial | SaaS, VPC, on-prem | PII, injection, OPA, CrowdStrike AIDR ([docs](https://truefoundry.com/docs/ai-gateway)) | MCP gateway, registry | $0 / $499 / $2,999 per month, Enterprise ([pricing](https://truefoundry.com/pricing)) |
| Databricks Unity AI Gateway | Databricks (2026-06-16) | Databricks | PII, injection, jailbreak; third-party security "upcoming" ([blog](https://databricks.com/blog/ai-governance-data-ai-summit-2026-whats-new-unity-ai-gateway)) | MCP governance | Platform |
| Apigee | Google | GCP / hybrid | Model Armor policies ([InfoQ](https://www.infoq.com/news/2025/07/google-apigee-llm-model-armor)) | MCP ([docs](https://docs.cloud.google.com/apigee/docs/api-platform/get-started/ai-capabilities)) | Platform |
| F5 AI Gateway | F5 | Self-hosted containers | F5 AI Guardrails (from CalypsoAI) ([product](https://www.f5.com/es_es/products/ai-gateway)) | MCP gateway | Commercial |
| Traefik Hub | Traefik Labs | Self-hosted, offline | Parallel guards: Presidio, NVIDIA safety NIMs, Granite Guardian, regex (*secondary source*, [Help Net](https://helpnetsecurity.com/2026/03/17/traefik-labs-triple-gate-new-capabilities/)) | MCP gateway | Commercial |
| Helicone | Mintlify, maintenance mode ([Mintlify](https://mintlify.com/blog/mintlify-acquires-helicone)) | — | — | — | — |

Also: Netskope One AI Gateway (on-prem or VPC; its guardrail API is offered to LiteLLM, Kong,
Apigee, [datasheet](https://www.netskope.com/wp-content/uploads/2026/04/Data-Sheet-Netskope-One-AI-Gateway-TC.pdf));
Zscaler AI Guard (proxy mode and detection API, [brief](https://www.zscaler.com/mx/resources/solution-briefs/zscaler-ai-guard.pdf));
Kosmoy (Milan, self-hosted, EU AI Act checks, [site](https://kosmoy.com/platform/ai-gateway));
Vercel AI Gateway ([pricing](https://vercel.com/docs/ai-gateway/usage-and-pricing)); Bifrost
(Apache-2.0, Go, *secondary source*).

## 3. Security vendors and consolidation

| Target → acquirer | Closed | Deployment patterns |
|---|---|---|
| Robust Intelligence → Cisco | 2024-09 ([Cisco](https://blogs.cisco.com/news/fortifying-the-future-of-security-for-ai-cisco-announces-intent-to-acquire-robust-intelligence)) | Cloud gateway by URL redirect (no self-hosted model endpoints), egress gateway with TLS interception, inspection API ([docs](https://securitydocs.cisco.com/docs/ai-def/user/105487.dita)) |
| Protect AI → Palo Alto Networks | 2025-07-22 ([PANW](https://www.paloaltonetworks.com/company/press/2025/palo-alto-networks-completes-acquisition-of-protect-ai)) | Scan API, network intercept, now the AI Gateway |
| Aim Security → Cato Networks | announced 2025-09-03 ([CyberScoop](https://cyberscoop.com/cato-networks-acquires-ai-security-startup-aim-security/)) | SASE inline; still listed in LiteLLM |
| Prompt Security → SentinelOne | 2025-09 ([10-Q](https://www.sec.gov/Archives/edgar/data/1583708/000158370825000159/s-20251031.htm)) | Browser extension, desktop agent |
| Pangea → CrowdStrike (Falcon AIDR) | 2025-09 ([10-Q](https://www.sec.gov/Archives/edgar/data/0001535527/000153552726000031/crwd-20260731.htm)) | API; LiteLLM, Portkey, TrueFoundry integrations |
| CalypsoAI → F5 | 2025 ([F5](https://www.f5.com/company/blog/what-are-ai-guardrails)); price **unverified** | Inside the F5 platform |
| Lakera → Check Point | 2025-10 ([Check Point](https://www.checkpoint.com/press-releases/check-point-software-reports-2025-third-quarter-financial-results/)) | Guard API; Kong, LiteLLM, Portkey plugins ([our reference](lakera-guard.md)) |
| SPLX → Zscaler | 2025-11 (*secondary source*) | Red teaming, runtime guardrails |
| LayerX → Akamai | 2026-07-02 ([Akamai](https://www.akamai.com/newsroom/press-release/akamai-completes-acquisition-of-secure-enterprise-browser-provider-layerx)) | Browser extension |
| Portkey → Palo Alto Networks | 2026-05-29 | Gateway (§2) |

Still independent as far as found: Lasso (raised 2026-09; CPU guardrail, vendor claim < 5 ms;
MCP gateway, *secondary source*), Straiker; Noma, HiddenLayer, WitnessAI **unverified**.

Five deployment patterns across vendors: (1) detection API (how gateways plug them in), (2)
reverse proxy / gateway, (3) network or SASE inline with TLS interception, (4) browser
extension or desktop agent, (5) edge WAF. UMAI today is (1) plus (4) through the extension.

## 4. Market framing

- Gartner, *Market Guide for AI Gateways* (2025-10-13, [Gartner](https://www.gartner.com/en/documents/7051698)):
  three vendor types (API management adding AI, AI platforms with a gateway, purpose-built
  open-source / open-core gateways); advises buyers to check their existing API-management
  platform first (*secondary source* for the content, [Gravitee](https://www.gravitee.io/blog/gravitee-in-gartner-market-guide-for-ai-gateways)).
- Differentiation has moved to MCP and agent (A2A) governance, agent identity, hard budgets,
  residency and compliance evidence.
- A gateway is a high-value target: LiteLLM PyPI releases 1.82.7 and 1.82.8 were compromised on
  2026-03-24 to steal credentials (*secondary source*, [Arthur](https://www.arthur.ai/column/litellm-supply-chain-attack-pypi-compromise-2026)).
- EU AI Act Digital Omnibus in force 2026-07-27; Annex III high-risk obligations move to
  2027-12-02 (*secondary source*, [NatLawReview](https://natlawreview.com/article/eu-digital-omnibus-ai-enters-force)).

## 5. Türkiye

- KVKK Art. 9 (Law 7499, in force 2024-06-01): transfers abroad under standard contracts are
  notified to the authority within 5 business days ([KVKK](https://www.kvkk.gov.tr/Icerik/8043/Standart-Sozlesme-Bildirim-Modulu-Hakkinda-Kamuoyu-Duyurusu)).
  Sending prompts with personal data to a provider abroad is such a transfer; whether
  pseudonymised prompts are is a legal question (Q51).
- KVKK guide on generative AI and personal data, November 2025 (*secondary source*,
  [Erdem & Erdem](https://www.erdem-erdem.av.tr/bilgi-bankasi/uretken-yapay-zeka-ve-kisisel-verilerin-korunmasina-iliskin-rehber-yayimlandi)).
- Banks (BDDK): primary and secondary systems in Türkiye (*secondary source*, KPMG), so a
  self-hosted gateway is the only form for them.
- No comprehensive AI law; AI Action Plan 2026–2030 circular, Official Gazette 33344,
  2026-08-18 (*secondary source*).

## 6. Integration hooks and technical facts

### 6.1 External-guardrail hooks

| Gateway | Mechanism | Can it rewrite (mask)? | Streaming output | Default on failure |
|---|---|---|---|---|
| LiteLLM *Generic Guardrail API* ([docs](https://docs.litellm.ai/docs/adding_provider/generic_guardrail_api), checked 2026-10-19) | The guardrail service implements `POST /beta/litellm_basic_guardrail_api` | **Yes**: `action` `GUARDRAIL_INTERVENED` with `texts` (1:1 with the sent `texts`) or `structured_messages` (row per row); `BLOCKED` needs `blocked_reason`; `NONE` passes | Not documented on that page | `unreachable_fallback: fail_closed`, `fail_on_error: true`; `num_retries` default 0 (max 5); worst case `timeout × (num_retries + 1)` |
| Portkey webhook ([docs](https://portkey.ai/docs/product/guardrails/list-of-guardrail-checks/bring-your-own-guardrails), checked 2026-10-19) | `POST` with `request{json,text,isStreamingRequest}`, `response{json,text,statusCode}`, `provider`, `requestType` (`chatComplete`, `complete`, `embed`), `metadata`, `eventType` (`beforeRequestHook`, `afterRequestHook`) | **Yes**: `verdict` (bool) and optional `transformedData.request` / `.response`, which **replaces the whole body** | Output checked after the stream ends; results as a chunk after `[DONE]`, no deny ([guide](https://portkey.ai/docs/guides/use-cases/streaming-output-guardrails)) | **Timeout → `verdict: true` (pass)** unless "Fail on Error" is enabled; default timeout 3 000 ms |
| Kong *AI Custom Guardrail* ([docs](https://developer.konghq.com/plugins/ai-custom-guardrail/)) | Templated HTTP call; a Lua expression decides `block`; Enterprise, Gateway 3.14 / AI Gateway 2.0, needs AI Proxy | Documented as allow / block only. Kong's separate *AI PII Sanitizer* (Enterprise, own container) masks and restores ([docs](https://developer.konghq.com/plugins/ai-sanitizer/)) | **unverified** | **unverified** |
| agentgateway webhook ([overview](https://agentgateway.dev/docs/kubernetes/latest/llm/guardrails/overview/)) | `POST /request`, `POST /response`; scopes `SystemPrompt`, `Messages`, `ToolInput`, `ToolOutput` | Yes per the webhook OpenAPI spec (`MaskAction`); the overview lists only Reject / Audit (docs conflict) | `promptGuard.streaming` off by default; when on, Mask is not applied to streams and Reject sends `guardrail_blocked` | `FailClosed` |
| Envoy ext_proc / agentgateway ext_proc ([Envoy](https://www.envoyproxy.io/docs/envoy/latest/api-v3/extensions/filters/http/ext_proc/v3/processing_mode.proto), [agentgateway](https://agentgateway.dev/docs/standalone/latest/configuration/traffic-management/extproc/)) | gRPC external processing | Yes (headers and body) | Body modes up to `FULL_DUPLEX_STREAMED` | Configurable (agentgateway: fail-closed, 10 s) |
| Azure API Management | `llm-content-safety` ([docs](https://learn.microsoft.com/en-us/azure/api-management/llm-content-safety-policy)) calls only Azure AI Content Safety; `send-request` ([docs](https://learn.microsoft.com/en-us/azure/api-management/send-request-policy)) calls any service, then `choose` / `return-response` / `set-body` | Built-in: block only; `send-request` + `set-body`: yes | Built-in: sliding window (`window-size` 1 000 chars for responses, overlap configurable), stops forwarding events; custom: build it yourself; SSE needs `buffer-response="false"` ([docs](https://learn.microsoft.com/en-us/azure/api-management/how-to-server-sent-events)) | `send-request` `ignore-error` default false |
| Cloudflare AI Gateway | Built-in Llama Guard and DLP only ([docs](https://developers.cloudflare.com/ai-gateway/guardrails/)) | No (flag / block) | — | — |
| TrueFoundry ([docs](https://truefoundry.com/docs/ai-gateway/custom-guardrails)) | Custom guardrail server, validate or mutate mode | Yes (mutate) | — | Per enforcing strategy |
| Apigee | `SanitizeUserPrompt` / `SanitizeModelResponse` call Model Armor only; `ServiceCallout` calls any service ([docs](https://docs.cloud.google.com/apigee/docs/api-platform/reference/policies/service-callout-policy)) | Via callout | — | — |

LiteLLM's guardrail modes: `pre_call`, `during_call` (parallel with the LLM call; the response
waits for the check), `post_call`, `logging_only`; `default_on` applies a guardrail to every
request; per-key and per-team control is Enterprise ([docs](https://docs.litellm.ai/docs/proxy/guardrails/quick_start)).
Its hook request carries `request_data.user_api_key_*` (hash, alias, user, team, org, end user),
which can map to a UMAI subject. Its *Decision Model Guardrail* is §1.

### 6.2 Streaming and reversible placeholders

- Patterns in use: buffer everything (LiteLLM Bedrock default, [docs](https://docs.litellm.ai/docs/proxy/guardrails/bedrock.md));
  sampled scans with an explicit "detect-and-log, not prevention" warning for end-of-stream
  only (same page); overlapping windows (APIM; NeMo Guardrails `chunk_size` 200 tokens,
  `context_size` 50, `stream_first` default true, [docs](https://docs.nvidia.com/nemo/guardrails/latest/configure-guardrails/yaml-schema/streaming/output-rail-streaming));
  audit-only after delivery (Portkey). On a violation, streams are cut with an error event
  (APIM, agentgateway, NeMo).
- Sent bytes cannot be recalled: masking is the only verdict applied live; a block takes effect
  from the point it is found. A hold-back longer than the longest entity or placeholder delays
  only the tail. Re-scan the raw accumulated buffer, not masked text, or split secrets slip
  through (*secondary source*, [nr-llm ADR](https://docs.typo3.org/p/netresearch/nr-llm/0.30/en-us/Adr/Adr088StreamingRedaction.html)).
- Tool-call arguments stream as partial JSON (Anthropic `input_json_delta`, OpenAI
  `response.function_call_arguments.delta` / `.done`): buffer per call until complete.
- Reversible masking in the market: LiteLLM's Presidio integration with `output_parse_pii`
  (depends on the model reproducing tokens, [docs](https://docs.litellm.ai/docs/proxy/guardrails/pii_masking_v2));
  LLM Guard `Vault` + `Deanonymize` with `exact`, `case_insensitive`, `fuzzy` (≤ 3 edits) and
  combined strategies ([source](https://protectai.github.io/llm-guard/output_scanners/deanonymize/));
  Presidio's only built-in reversible operator is `decrypt` via `DeanonymizeEngine`; Kong AI PII
  Sanitizer restores originals.

### 6.3 Provider API surfaces

- OpenAI: Chat Completions remains supported; Responses is recommended for new projects (typed
  input / output items, `previous_response_id`, storage on by default, typed SSE events such as
  `response.output_text.delta`, hosted tools incl. remote MCP)
  ([migration guide](https://developers.openai.com/api/docs/guides/migrate-to-responses)).
  Assistants API removed 2026-08-26 ([deprecations](https://developers.openai.com/api/docs/deprecations)).
  *Open Responses* (2026-01) generalises Responses across providers (Hugging Face, OpenRouter,
  vLLM, Ollama; *secondary source*, [InfoQ](https://infoq.com/news/2026/02/openai-open-responses/)).
- Anthropic: its OpenAI-compatibility layer is "not considered a long-term or production-ready
  solution" ([docs](https://platform.claude.com/docs/en/api/openai-sdk)); native `/v1/messages`
  streaming uses `content_block_delta`, `text_delta`, `input_json_delta`, `message_stop`.
- Gemini: OpenAI compatibility still beta ([docs](https://ai.google.dev/gemini-api/docs/openai)).
- Azure OpenAI / Foundry v1: `https://<resource>.openai.azure.com/openai/v1/` without
  `api-version`; `api-key` header or Entra bearer ([docs](https://learn.microsoft.com/en-us/azure/foundry/openai/api-version-lifecycle)).
- Gateways differ: Portkey normalises to OpenAI; LiteLLM translates `/v1/messages` and drops
  `cache_control` unless native pass-through is enabled ([docs](https://docs.litellm.ai/docs/anthropic_unified/native_passthrough));
  APIM accepts several native schemas.

### 6.4 MCP and agent gateways

LiteLLM MCP guardrail modes `pre_mcp_call`, `during_mcp_call`, `post_mcp_call`, and scans of tool
descriptions at `tools/list` ([docs](https://docs.litellm.ai/docs/mcp_guardrail)); Portkey MCP
guardrails with `run_on` input / output, block = JSON-RPC error `-32446`
([docs](https://portkey.ai/docs/aigw/product/mcp-gateway/guardrails.md)); agentgateway CEL
authorization over `list_tools` / `call_tools` and MCP OAuth
([docs](https://agentgateway.dev/docs/standalone/latest/mcp/mcp-authz/)); Kong AI MCP Proxy
(Enterprise, per-tool ACLs); APIM exposes or passes through MCP servers (tools only)
([docs](https://learn.microsoft.com/en-us/azure/api-management/mcp-server-overview)); Docker MCP
Gateway interceptors and secret blocking ([docs](https://docs.docker.com/reference/cli/docker/mcp/gateway/gateway_run/)).

### 6.5 Build or embed

| Option | Licence, language | Weight | Multi-tenancy | Published overhead |
|---|---|---|---|---|
| Thin FastAPI / httpx proxy | Ours, Python | Low (same image) | Native (UMAI tenants, keys, limits) | None published; LiteLLM's Python proxy as a rough guide: p50 2–12 ms, p99 13–43 ms with 2–4 instances (*vendor claim*, [benchmarks](https://docs.litellm.ai/docs/benchmarks)) |
| Embed LiteLLM | MIT except `enterprise/`; Python, Rust path in beta | Heavy: Postgres, fast releases; PyPI compromise 2026-03-24 ([Datadog](https://securitylabs.datadoghq.com/articles/litellm-compromised-pypi-teampcp-supply-chain-campaign/)); MCP auth bypass and guardrail RCE CVEs in 2026 (*secondary source*, [Wiz](https://www.wiz.io/blog/off-guard-breaking-litellm-from-authentication-bypass-to-cloud-compromise)) | Teams, orgs, virtual keys; per-key guardrails Enterprise; stored keys encrypted with `LITELLM_SALT_KEY` ([FAQ](https://docs.litellm.ai/docs/proxy/security_encryption_faq)) | As above |
| agentgateway / Agent Router with webhook or ext_proc | Apache-2.0; Rust / Envoy + Go | Binary or Kubernetes | Tenant isolation **unverified**; UMAI maps tenants from headers | agentgateway p99 ≈ 2 ms (*competitor-run*, [blog](https://agentgateway.dev/blog/2026-06-26-benchmarking-agentgateway-vs-litellm/)) |
| Plugins for customers' gateways | — | Lowest for us | Tenant from plugin configuration | One extra HTTP call per check |

Other vendor overhead claims: Kong vs Portkey / LiteLLM ([Kong](https://konghq.com/blog/engineering/ai-gateway-benchmark-kong-ai-gateway-portkey-litellm)),
Portkey < 1 ms ([README](https://github.com/Portkey-AI/gateway)), TrueFoundry ≈ 3–4 ms. None
measured by us.

### 6.6 Provider keys and data

- Pass-through: forward the client's `Authorization`; Cloudflare substitutes a stored key only
  when that header is absent ([docs](https://developers.cloudflare.com/ai-gateway/configuration/bring-your-own-keys/)).
- Stored: LiteLLM encrypts with a salt key that cannot change after models are added, spend and
  audit logs unencrypted; Portkey vaults with optional KMS envelope encryption
  ([docs](https://portkey.ai/docs/product/enterprise-offering/kms)); on Azure, per-tenant data keys
  wrapped by a Key Vault / Managed HSM key, or managed identity for Azure OpenAI backends.
- OpenAI keeps abuse-monitoring logs up to 30 days by default; zero data retention needs approval
  ([docs](https://developers.openai.com/api/docs/guides/your-data)). Masking before the provider
  call reduces what is retained abroad.

## 7. Notes for other reference documents

- Presidio's documentation moved: `microsoft.github.io/presidio` redirects to
  `presidio.dataprivacystack.org` (reported 2026-10-19). Re-verify [`presidio.md`](presidio.md)
  links (skill `verify-vendor-facts`).
- The LiteLLM decision-model guardrail consumes `/v1/decisions` models (Jev, OpenAI, Foundry):
  relevant to [`typesafe-jev.md`](typesafe-jev.md) and [`openai-decisions-api.md`](openai-decisions-api.md)
  as a competing consumer of the same providers.

## 8. Console object models (checked 2026-10-20)

How administrators configure each gateway, from the vendors' own documentation; used by the
[console information architecture](../architecture/console-information-architecture.md) and ADR-0039. "Unverified" means
the documentation did not show it.

**Portkey** (docs now carry the "Prisma AIRS AI Gateway" banner; some pages are outdated):

- **Objects.** Organization → workspaces. Provider credentials live in an **Integration** at
  organization level and are provisioned as **AI Providers** with a slug
  ([model catalog](https://portkey.ai/docs/product/model-catalog)). Virtual keys were migrated to
  AI Providers ([upgrade](https://portkey.ai/docs/support/upgrade-to-model-catalog)).
- **Selecting a provider.** Requests name `@provider-slug/model`.
- **Keys and policy.** API keys are per workspace. A key can enforce a **default config**; with
  overrides off, any other config is refused
  ([default config](https://portkey.ai/docs/product/administration/enforce-default-config)).
- **Guardrails** attach at the organization
  ([org](https://portkey.ai/docs/product/administration/enforce-orgnization-level-guardrails)),
  the workspace and the config ([guardrails](https://portkey.ai/docs/product/guardrails)). The
  order between levels is unverified.
- **Logs** have filters by model, provider, config, API key, status and trace
  ([filters](https://portkey.ai/docs/product/observability/filters)). PII redaction uses
  placeholders, but the log shows the original request next to the redacted one
  ([use case](https://portkey.ai/docs/guides/use-cases/guardrail-pii-use-case)).
- **Retention:** 3 days (Developer) or 30 days (Production) ([pricing](https://portkey.ai/pricing)).

**LiteLLM proxy:**

- **Objects.** Organization → team → user → virtual key; organizations and team admins are
  Enterprise ([access control](https://docs.litellm.ai/docs/proxy/access_control)).
- **Selecting a provider.** Public `model_name` over `provider/deployment` targets
  ([configs](https://docs.litellm.ai/docs/proxy/configs)).
- **Guardrails** are defined once, then attached globally, per request, or through guardrail
  policies on teams, keys, models or tags (team and key are Enterprise)
  ([policies](https://docs.litellm.ai/docs/proxy/guardrails/guardrail_policies)).
- **Errors and logs.** A blocked call gets 400 and `x-litellm-applied-guardrails`. Logs have a
  guardrail panel ([logs](https://docs.litellm.ai/docs/proxy/ui_logs)).
- **Retention:** none by default ([deletion](https://docs.litellm.ai/docs/proxy/spend_logs_deletion)).

**Kong AI Gateway 2.x** (private beta, 2026-07-16):

- **Objects.** AI Model Provider (credentials), AI Model (a virtual model over provider targets),
  AI Consumer and consumer group, AI Policy
  ([concepts](https://developer.konghq.com/ai-gateway/ai-gateway-v2-concepts/)).
- **Policies** attach to a consumer, a group, a model or globally; the most specific wins
  ([policy](https://developer.konghq.com/ai-gateway/entities/ai-policy/)).
- **Audit events** keep the original values in `sanitized_items`
  ([audit](https://developer.konghq.com/ai-gateway/ai-audit-log-reference/)).

**Cloudflare AI Gateway:**

- **Objects.** Account → gateways. Guardrails, DLP and provider keys are configured per gateway, so
  a different policy needs another gateway
  ([DLP](https://developers.cloudflare.com/ai-gateway/features/dlp/),
  [BYOK](https://developers.cloudflare.com/ai-gateway/configuration/bring-your-own-keys/)).
- **Selecting a provider.** The model field is `provider/model` or a dynamic route
  ([chat completion](https://developers.cloudflare.com/ai-gateway/usage/chat-completion/)).
- **Retention and residency.** Logs are kept 7 days by default for gateways created since
  2026-09-24 ([datasets](https://developers.cloudflare.com/observability/logs/datasets/)). Requests
  can't be kept in one region
  ([localization](https://developers.cloudflare.com/data-localization/compatibility/)).

**Azure API Management:**

- **Objects.** APIs, backends, products with subscription keys, and XML policies at global,
  workspace, product, API or operation scope
  ([policies](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-policies)).
- **Content safety** attaches at those scopes; a block is 403
  ([content safety](https://learn.microsoft.com/en-us/azure/api-management/llm-content-safety-policy)).
- **Unified model API** (preview) has model aliases and `/models`
  ([unified](https://learn.microsoft.com/en-us/azure/api-management/unified-model-api)).

## 9. AI runtime security consoles (checked 2026-10-20)

How runtime security products organize their consoles, from their own documentation; used by
the [console information architecture](../architecture/console-information-architecture.md)
and ADR-0039.

**Palo Alto Networks Prisma AIRS** (Strata Cloud Manager, AI Security menu):

- **Four peer pillars:** AI runtime security (API and network intercept), supply chain, red
  teaming, and the AI gateway
  ([architecture](https://docs.paloaltonetworks.com/prisma-airs/getting-started/prisma-airs-platform-architecture)).
- **API intercept:**
  - An **Application** is bound to a deployment profile, holds one API key and is linked to an
    AI security profile
    ([onboard](https://docs.paloaltonetworks.com/prisma-airs/ai-runtime-security/airs-api/onboard-api-runtime-security-api-intercept-in-scm),
    [manage](https://docs.paloaltonetworks.com/prisma-airs/ai-runtime-security/airs-api/airs-apirs-manage-api-keys-profile-apps)).
  - *API Violations* shows blocked transactions by content type, detector and severity, with a
    per-request panel; DLP snippets can be masked
    ([violations](https://docs.paloaltonetworks.com/ai-runtime-security/administration/using-violations-view)).
- **AI gateway** (formerly Portkey, GA 2026-07-16):
  - It has its own workspaces, keys, policies and logs
    ([overview](https://docs.paloaltonetworks.com/prisma-airs/ai-gateway/ai-gateway-overview)).
  - LLM provider connections live only on its Integrations page
    ([integrations](https://docs.paloaltonetworks.com/prisma-airs/ai-gateway/ai-gateway-integrations)).
  - The documentation says it is available in the Americas only
    ([activate](https://docs.paloaltonetworks.com/prisma-airs/ai-gateway/activate-ai-gateway)).

**Lakera Guard** (now Check Point AI Guardrails):

- **Sidebar:** Analytics, Logs, Policies, Projects, Playground, API Access, Settings
  ([platform](https://docs.lakera.ai/docs/platform)).
- **Project:** one policy, mode Enforce or Detect, tags; the request carries `project_id`
  ([projects](https://docs.lakera.ai/docs/projects)).
- **Policy impact simulator:** replays 30 days of traffic
  ([policies](https://docs.lakera.ai/docs/policies/saas-policies)).
- **Integration:** no proxy of its own; existing gateways call the Guard API
  ([integration](https://docs.lakera.ai/docs/integration)).

**CrowdStrike Falcon AIDR** (formerly Pangea AI Guard; end of support 2026-11-30, continuing in
Falcon Guardian):

- **Collector:** one policy, a token, an install snippet and a playground
  ([collectors](https://pangea.cloud/docs/aidr/collectors/application)).
- **One Collectors page by type:** application (SDK/API), gateway (Apigee, Azure APIM, Kong,
  LiteLLM, Portkey), agentic (MCP proxy), browser, endpoint
  ([types](https://aidr-docs.crowdstrike.com/docs/aidr/collectors)).
- **Entry page:** *Visibility*, a user → application → provider → model flow that drills down into
  *Findings*; a finding can be marked benign
  ([visibility](https://aidr-docs.crowdstrike.com/docs/aidr/visibility/data-flows-and-dashboards)).

**HiddenLayer AISec Platform:**

- **Project** ("an AI use case"): exactly one policy and a project id header
  ([projects](https://docs.hiddenlayer.ai/docs/products/runtime/console/runtime_security_projects.md)).
- **Detections** by threat level and category
  ([detections](https://docs.hiddenlayer.ai/docs/products/runtime/console/runtime_security_detections.md)).
- **Providers:** no provider setup in the console; the client passes its provider key through the
  proxy.

**Prompt Security** (SentinelOne since 2025-09-05):

- "Homegrown apps" with policies inside the Singularity console; the details are not public
  ([product](https://www.sentinelone.com/platform/prompt-security-ai-application-security/)).

**Noma, Lasso:** documentation behind a login; not verified.
