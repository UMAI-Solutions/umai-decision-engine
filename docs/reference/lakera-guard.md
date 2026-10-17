# Lakera Guard (Check Point AI Guardrails) — PII and policy model

**Last verified:** 2026-10-17 · **Used by:** L0 coverage target (ADR-0017), guardrail model, editions (ADR-0018) ·
Labels: see [README](README.md). Everything here is a **vendor claim** from the vendor's docs
unless marked otherwise.

Lakera Guard's documentation is now published as **Check Point AI Guardrails** on
docs.lakera.ai (*secondary source* for the rename: third-party review, consistent with the
page titles). It is the market reference the product owner chose for L0's PII coverage.

## PII detectors ("Data Leakage → Managed")

Source: [Data Leakage Prevention](https://docs.lakera.ai/docs/data-leakage-prevention).

| Detector | What it flags | Not flagged / limits |
|---|---|---|
| Name | Full names from any cultural background, with middle initial or middle name; tolerates typos and punctuation errors. A 2024 changelog says famous names were added ([changelog](https://docs.lakera.ai/changelog/2024-06-04)) | Single names; "John Doe" / "Jane Doe" |
| Mailing address | **US** street addresses, optionally with city, state, ZIP | Non-US addresses; city or state alone |
| Phone number | **US** numbers, with or without `+1`; area code required | Non-US numbers |
| Email | Standard addresses, subdomains, `[AT]` / `[DOT]` obfuscation | Threshold not tunable |
| IP address | IPv4 and IPv6; public addresses at high confidence; private, CGNAT, unique-local and documentation ranges at lower confidence | Loopback, link-local, multicast, common DNS (e.g. `8.8.8.8`) |
| Credit card | 14-, 15-, 16-, 19-digit numbers, with or without separators; Luhn-validated | — |
| IBAN | Standard format with or without spaces (examples CH, DE, FR, IT, ES, GB) | Full country list not published; threshold not tunable |
| US Social Security number | Dashes, spaces or both | Invalid patterns such as `000-` / `666-` prefixes; threshold not tunable |

Also in the data-leakage category: **custom data leakage guardrails** by pattern or keyword
("custom regex matches" appear in the API payload), system-prompt leakage, custom entity
types (beta).

## Unknown links

Source: [Unknown links](https://docs.lakera.ai/docs/unknown-links). A URL is flagged when its
domain is **not among the top one million most popular domains** (the page links DomCop's
list without naming the edition). Policies can add **allowed domains** (domain + TLD, no
subdomain). Threshold not tunable. Intended mainly for LLM outputs (phishing links).

## Policy model

Sources: [Policies](https://docs.lakera.ai/docs/policies/saas-policies),
[Defenses](https://docs.lakera.ai/docs/defenses), [Guard API](https://docs.lakera.ai/docs/api/guard).

- A **policy** lists guardrails (defense categories: prompt defense, content moderation, data
  leakage prevention, malicious links, agent behaviour), a **flagging sensitivity**, and the
  **message roles** each guardrail screens: `user` (inputs), `assistant` (outputs), `tool`
  (tool responses), `tool_call` (tool-call arguments). The UI shows two toggles per PII
  detector (user / assistant).
- **Sensitivity L1–L4** = confidence threshold for a detection, not severity: L1 lenient (very
  few false positives), L2 balanced, L3 strict (very few false negatives), **L4 paranoid
  (default)**. Numeric thresholds are not published.
- **Projects:** each project has exactly one policy; a policy can serve many projects. Catalog
  policies (e.g. the default policy) are read-only templates; saved edits take effect within a
  couple of minutes; changes are audit-logged; a simulator runs a policy version on samples.
- **Modes:** Detect (never flags, logs only) and Enforce (flags on detection) — ≈ our
  `MONITOR` / `ENFORCE`.
- **API:** `POST https://api.lakera.ai/v2/guard` with OpenAI-style `messages`, optional
  `project_id`, `breakdown: true` (per-guardrail detected + confidence) and `payload: true`
  (location and type of PII / profanity / custom regex matches). The response has `flagged`.
  The guardrail can **block or mask** PII. Exact `detector_type` strings are in the API
  reference and were not captured here (**unverified**: `pii/...` prefix per third-party
  integration docs, e.g. [Portkey](https://portkey.ai/docs/integrations/guardrails/lakera.md)).

## Plans, deployment and limits

Checked 2026-10-17. Sources: [pricing](https://platform.lakera.ai/pricing) (rendered page;
`lakera.ai/pricing` redirects there), [Platform](https://docs.lakera.ai/docs/platform.md),
[Self-hosting](https://docs.lakera.ai/docs/selfhosting.md),
[Data regions](https://docs.lakera.ai/docs/data-regions.md),
[Guard API reference](https://docs.lakera.ai/api-reference/lakera-api/guard/screen-content.md).
This is the model for UMAI's editions ([ADR-0018](../adr/0018-saas-and-self-hosted-editions.md)).

| Topic | What the vendor documents | Not found / unverified |
|---|---|---|
| Plan table | Community $0: 10k requests / month, 8k tokens, SaaS, community support, EU residency. Enterprise by contract: flexible requests, configurable prompt size, SaaS or self-hosted, SSO, RBAC, SIEM, version pinning (self-hosted only), EU / US residency. Both: API, dashboards, reports, encryption, SOC 2 / GDPR. The platform docs add "up to 1 MB of context per request" and data-retention control for Enterprise | A "$99 Pro tier" appears only on a third-party site (**secondary source**) |
| Over the prompt-size limit | Long content is handled by chunking and parallel scanning; the API spec has no 413 / size error | **Unverified:** whether a Community request above 8k tokens is rejected, truncated or chunked; which tokenizer counts tokens |
| Quota / rate limits | The Guard API can return 429 "Too many requests"; error body `error`, `code`, `request_id` | **Unverified:** whether exceeding the monthly quota returns 429; per-second limits; overage pricing |
| Self-hosted delivery | Helm chart or Docker / OCI images from a registry (credentials from the vendor); guardrails service, API gateway, Triton inference server; NVIDIA GPUs supported; full offline deployment (export and load images) | Hardware sizing (customer-only portal) |
| Version pinning | Container channels: stable, nightly and version-pinned releases (e.g. container version `2.0.493`); model updates ship inside container releases (our reading) | Exact pinning semantics are not spelled out |
| Self-hosted licence | Enterprise licence key passed as `LAKERA_GUARD_LICENSE` | **Secondary source:** the container does not start with a missing or expired key. **Unverified:** phone-home or usage reporting |
| Self-hosted operation | API without API keys; policies as JSON files from S3 or a mounted directory, hot-reloaded within minutes; an invalid policy file crashes the container; probes `/startupz`, `/readyz`, `/livez`; **the dashboard is not available to self-hosted customers** | — |
| SIEM | Enterprise only: automatic log export to an S3-compatible bucket as `.jsonl` files under `YYYY/MM/DD/hh/` (one log per line: timestamp, request, response); admins are e-mailed when bucket credentials fail. Logs and analytics APIs for select organisations | No native Splunk / Datadog / syslog / webhook integration found (a community Splunk add-on exists — **secondary source**) |
| SSO / RBAC | SSO configurable per organisation. RBAC (Enterprise): roles **User** (view analytics, logs, keys, projects, policies, members), **Admin** (also manage keys, projects, policies, roles; toggle prompt logging), **No access** (offboarding) | SAML vs OIDC; supported identity providers |
| Data residency | Processing region chosen by hostname: `eu.api.lakera.ai` (eu-south-2), `us.api.lakera.ai` (us-east-1), an Asia-Pacific host, and `api.lakera.ai` routing to the nearest; storage region fixed when the organisation is created; `allowed_processing_regions` rejects requests sent to other regions with a 4xx | — |
| Dashboards / reports | Analytics: request counts, flagged requests, detection rate, latency distribution, per-project comparison, time and project filters; logs with lookup by request id, PII masked in stored logs as types (e.g. `<EMAIL>`); prompt logging on by default, admins can turn it off; analytics API over at most 180 days | What "Reports" contains as a feature |

## What this means for us

- Parity list for L0: name, mailing address, phone, e-mail (incl. obfuscation), IP (with the
  public / private / special-range nuance), card (14–19 digits), IBAN, US SSN, unknown links.
- Lakera is US-centric for address and phone; Türkiye and Europe are where we go further
  ([`architecture/l0-entity-catalog.md`](../architecture/l0-entity-catalog.md)).
- Their policy shape (per-detector role toggles, sensitivity levels, one policy per project,
  templates, simulator) matches our guardrail model and is a useful reference for the console.
- Editions ([`architecture/deployment-and-editions.md`](../architecture/deployment-and-editions.md)):
  we copy the plan table, but deliberately differ where Lakera is silent or weaker: oversized
  input gets an explicit 413 instead of undocumented behaviour; self-hosted Enterprise gets the
  console (Lakera's self-hosted has no dashboard); a lapsed licence should not stop evaluation
  (Q24). Their S3 JSONL export is a cheap first SIEM target (Q22); their per-region hostnames
  and User / Admin / No-access roles are a reasonable starting point for ours.
