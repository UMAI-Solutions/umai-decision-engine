# Reference

Dated summaries of external facts the engine depends on. Every document has a
**Last verified** date and cites a source for each claim. Labels:

- **unverified** — could not be confirmed from any source; treat as unknown.
- **secondary source** — only press or third-party coverage, not the vendor.
- **vendor claim** — stated by the vendor, not independently measured.

Re-verify anything older than 30 days before relying on it (skill `verify-vendor-facts`).
Measurements we make ourselves go to `evals/reports/`, not here.

| Document | Covers | Used by |
|---|---|---|
| [presidio.md](presidio.md) | Microsoft Presidio analyzer/anonymizer APIs, versions, NLP engines, performance | L0 |
| [turkish-identifiers.md](turkish-identifiers.md) | TCKN, VKN, TR IBAN, phone, plate, Luhn: algorithms and synthetic test vectors | L0 |
| [turkish-nlp.md](turkish-nlp.md) | Turkish NER options (spaCy, transformers, GLiNER) | L0 (NER) |
| [eu-identifiers.md](eu-identifiers.md) | US SSN and European national IDs: formats, checksums, canonical examples | L0 validators |
| [lakera-guard.md](lakera-guard.md) | Lakera Guard / Check Point AI Guardrails: PII detectors, unknown links, policy model, sensitivity levels, plans, self-hosting, residency | L0 coverage target (ADR-0017), guardrail model, editions (ADR-0018) |
| [onprem-decision-models.md](onprem-decision-models.md) | Open-weight decision models and DIY option scoring for on-prem L1b | L1b (default candidates) |
| [typesafe-jev.md](typesafe-jev.md) | TypeSafe Jev decision model (hosted) | L1b cloud option |
| [openai-decisions-api.md](openai-decisions-api.md) | OpenAI Decisions API (GPT-6 Luna) | L1b |
| [gpt-oss-safeguard.md](gpt-oss-safeguard.md) | gpt-oss-safeguard model, prompting, hosting, latency; alternative safety classifiers | L1b fallback, L2 |
| [identity-providers.md](identity-providers.md) | WorkOS, Auth0, Stytch, Keycloak, Zitadel: SSO, SCIM, audit logs, self-hosting, regions, prices; Auth0 and Keycloak sign-in pages, custom domains, languages, e-mail, organizations, invitations, Actions, plans, rate limits, configuration as code | ADR-0024 (console identity), ADR-0033 (organizations, onboarding) |
| [key-management-services.md](key-management-services.md) | AWS KMS, GCP KMS, Azure Key Vault, Vault Transit, CloudHSM: Ed25519 support, FIPS, prices, rotation | ADR-0027 (key custody) |
| [azure-platform.md](azure-platform.md) | Azure for UMAI Cloud: Key Vault / Managed HSM (no Ed25519, P-256, envelope, rotation, prices), PostgreSQL Flexible Server (RLS, PITR, geo-backup pairs, CMK), EU/US regions and EU Data Boundary, Managed Redis, AKS/Container Apps, ACR signing, Log Analytics/Sentinel, Blob WORM, Front Door vs Application Gateway, SOC 2; Communication Services Email (retiring 2028-09-30: limits, DNS, Auth0 provider) | launch plan P1/P2, ADR-0025–0028, ADR-0030, sign-in design (A13) |
| [usage-billing-providers.md](usage-billing-providers.md) | Stripe Billing Meters, Orb, Metronome, Lago: ingestion, aggregations, entitlements, residency, prices | ADR-0028 (metering, billing) |
| [iyzico-subscriptions.md](iyzico-subscriptions.md) | iyzico Subscription (Abonelik) API: IYZWSv2 auth, products, plans, customers, checkout-form start, upgrade, retry, card update, statuses, webhooks and `X-IYZ-SIGNATURE-V3`, sandbox and test cards, 3DS/currency/fee constraints, Python SDK | billing (planned) |
| [slack-incoming-webhooks.md](slack-incoming-webhooks.md) | Slack incoming webhooks: URL shape (`hooks.slack.com/services/`), `text` and `blocks`, mrkdwn links, responses and which mean a dead URL, 1 message/s, no signature (the URL is the secret), buttons vs links | outbound webhooks, kind `slack` (ADR-0048) |
| [saas-tenant-isolation.md](saas-tenant-isolation.md) | AWS SaaS Lens isolation models; PostgreSQL row-level security semantics and pitfalls | ADR-0025 (tenant isolation) |
| [compliance-and-assurance.md](compliance-and-assurance.md) | SIG and CAIQ questionnaires, SOC 2 evidence tooling, pentest providers, GDPR / SCC / KVKK anchors | launch plan, Q25 |
| [ai-gateways.md](ai-gateways.md) | AI gateway market (LiteLLM, Portkey / Prisma AIRS, Kong, APIM, agentgateway, Agent Router, Cloudflare, OpenRouter, …), security-vendor consolidation, external-guardrail hooks and their failure defaults, streaming and reversible masking patterns, provider API surfaces, MCP gateways, build vs embed, provider keys, Türkiye notes | AI gateway proposal (`architecture/ai-gateway.md`), Q45–Q52 |
| [security-standards.md](security-standards.md) | OWASP ASVS 5.0 structure and IDs, SOC 2 TSC CC1–CC9, OWASP API Top 10 (2023), LLM Top 10 (2026) | gap analysis |
