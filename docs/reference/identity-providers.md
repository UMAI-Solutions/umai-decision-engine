# Identity providers and auth brokers for the console (SSO, SCIM, audit, sign-in pages, organizations)

**Last verified:** 2026-10-17 (comparison, Entra) and **2026-10-18** (section "Sign-in, onboarding
and branding": Auth0 and Keycloak) · **Used by:** ADR-0024 (human identity plane), ADR-0026
(residency of account metadata), ADR-0033 (organizations and onboarding),
[`architecture/sign-in-and-onboarding.md`](../architecture/sign-in-and-onboarding.md) ·
Labels: see [README](README.md). Everything is a **vendor claim** from the vendor's own pages
unless marked otherwise; prices are list prices on the day and change.

Constraint that shaped the comparison: the console must run as UMAI Cloud (EU and US
regions, ADR-0018) **and** inside a customer's network (self-hosted), from one codebase.

## Comparison

| | WorkOS | Auth0 (Okta CIC) | Stytch | Keycloak | Zitadel |
|---|---|---|---|---|---|
| SAML 2.0 / OIDC enterprise SSO | Yes / yes; connection per organization | Yes / yes (Okta WF, Entra ID, Google WS, ADFS, LDAP, PingFederate) | Yes / yes; per organization | Yes / yes; brokers to external SAML/OIDC IdPs | Yes / yes; external OIDC/SAML/LDAP IdPs |
| Customer self-service SSO setup | Admin Portal (domain verify, SSO, directory sync, log streams) | Self-Service SSO profiles + ticket URL, incl. SCIM | Admin Portal React components | None in core; admin console / REST API (third-party Phase Two adds one) | Console; delegated per-organization IdP management |
| SCIM | Directory Sync, custom SCIM 2.0; Okta, Entra ID, Google, JumpCloud, OneLogin, Rippling, PingFederate, CyberArk, SailPoint | Inbound SCIM 2.0 on SAML/OIDC/Okta/Entra connections | SCIM 2.0 per organization; Okta and Entra guides; groups → roles | SCIM API **supported since 26.8.0 (2026-10-01)**; realm-scoped, users and groups | SCIM 2.0 server, **users only** (no groups); GA status *unverified* |
| Customer-facing audit logs / SIEM | Per-organization Audit Logs product; CSV export; log streams to Datadog, Splunk, S3, GCS, Sentinel, Snowflake, HTTPS | Tenant logs (5–30-day retention); streams to EventBridge, Event Grid, Datadog, Splunk, Sumo, Elastic, webhook; no per-organization product | Project event logs (30 days); Datadog / Loki streaming; per-org audit logs on the public roadmap | User and admin events with expiration; Listener SPI for export; no SIEM connector | Event-sourced trail, unlimited retention, Events API; actions → webhook; no SIEM connector |
| Self-hosting | **No** (SaaS only; "on-prem" doc means your app calls WorkOS) | **No** customer-hosted option (Private Cloud is Auth0-operated) | **No** | **Yes**, Apache 2.0, CNCF incubating; Red Hat build with paid support | **Yes** (Docker, Helm, PostgreSQL); core **AGPL-3.0 since v3 (2025-03-31)**, commercial licence offered |
| EU region | **No**: DPA names the USA as the only storage/access country | Yes: US, EU, AU, JP, UK, CA | **No**: "operates exclusively out of U.S. servers" | Your infrastructure | Cloud: US, EU, Switzerland, Australia; EU available on Free |
| Price points | SSO and directory sync $125/connection/month (1–15), down to $65 (51–100); 1M MAU free then $2,500/month per extra 1M; log streams $125/month per SIEM connection | Free: 25k MAU, 1 enterprise connection; Essentials $150–3,800/month; Professional $800–2,400/month; extra enterprise connection $100/month (max 30); inbound SCIM included | Free: 10k MAU, 5 SSO-or-SCIM connections; extra connection $125; overage *unverified* | Free; Red Hat subscription price *unverified* | Free: 100 DAU, EU region; Pro $100/month for 25k DAU; per-connection price *unverified* |
| Python | Official `workos` | Official `auth0-python` | Official `stytch` | No official SDK; standard OIDC (JWKS); community `python-keycloak` (MIT) | Official management client (incubating); user auth via standard OIDC |
| Tenant model | Organizations; org-scoped roles | Organizations; roles per member; connections per org | Organizations, members, RBAC | Organizations inside a realm (since 26.0): per-org IdPs, domain routing, `organization` claim; **no org-scoped roles in core** | Organizations are tenants; project roles; delegated role grants |
| Compliance claims | SOC 2 Type 2; HIPAA BAA (enterprise); ISO 27001 *unverified* | SOC 2 Type 2; ISO 27001/27017/27018; CSA STAR; PCI DSS | SOC 2, ISO 27001 (Twilio trust center) | None (software; your deployment is audited) | ISO/IEC 27001:2022; SOC 2 Type II (announced 2026-01-21); DPF |

One line each: **Clerk** — SaaS only, US-hosted, SAML/OIDC $75/connection/month (2–15).
**Ory** — Kratos and Polis Apache 2.0 and self-hostable (Polis = SAML/OIDC SSO + SCIM);
Ory Network SAML/SCIM are Enterprise-only.

## What this means for us

- **The self-hosted edition rules out WorkOS, Auth0 and Stytch as dependencies.** Only
  Keycloak and Zitadel can run inside a customer's network.
- **An EU SaaS region rules out WorkOS and Stytch** (US-only processing) even for the cloud
  edition; Auth0 and Zitadel Cloud have EU regions; self-operated Keycloak is wherever we run it.
- **One codebase pattern:** the console and the services speak only standard OIDC
  (authorization code + PKCE, JWT validation against the issuer's JWKS, an organization claim
  for tenant selection). Broker-specific operations (create organization, mint an SSO/SCIM
  setup link, read directory events) sit behind an `IdentityBroker` interface with one
  implementation per backend. Roles live in our own database (AGENTS rule 3), which
  neutralises Keycloak's lack of organization-scoped roles.
- **Customer-facing audit logs and SIEM export are our own feature** (ADR-0030), fed from our
  event streams; only WorkOS sells them per organization and it is excluded by hosting.
- Keycloak's SCIM support is three weeks old at verification time; Zitadel's SCIM has no
  groups. Enterprise SCIM provisioning should be verified against Okta and Entra ID test
  tenants before it is sold (verification plan §2).
- **Decision (ADR-0024 §4, product owner, 2026-10-18):** Auth0 for UMAI Cloud, Keycloak for
  self-hosted. Public cloud EU or Private Cloud on Azure is Q35.

## Microsoft Entra External ID (Azure)

Added because UMAI Cloud must run on Azure (product-owner constraint). A separate section
because the comparison table is already five columns wide. Rows match the table above; the
last column covers the alternative "our own **workforce** tenant + B2B collaboration".
Microsoft vendor claims from learn.microsoft.com unless marked; *inference* = our reading,
not a vendor statement.

**Product status.** External ID in an *external tenant* is Microsoft's CIAM product (tenant
type "external", sign-in host `<tenant>.ciamlogin.com`). **Azure AD B2C is no longer sold to
new customers since 2025-05-01**; it is not an option for us.

| | Entra External ID (external tenant) | Workforce tenant + B2B collaboration (alternative) |
|---|---|---|
| SAML 2.0 / OIDC enterprise SSO | SAML/WS-Fed federation: yes (WS-Fed tested only with AD FS, Shibboleth); custom OIDC federation: yes (`code` flow only; `client_secret_post`/`client_secret_jwt`, no `private_key_jwt`); Entra ID tenants via OIDC. Domain-based routing: an email matching a federated domain is redirected to that IdP; `domain_hint` routing for invitations. One "domainless" SAML IdP per tenant. Max number of federations: *unverified* (a 1,000 figure appears only in secondary/stale copies) | SAML/WS-Fed federation per partner; Entra-to-Entra uses native B2B (direct SAML between two Entra tenants unsupported); configurable redemption order (not available in external tenants) |
| Tenant model | **No organization object.** Options: one external tenant for all customers (customer org = groups / app roles / custom attribute, max 100 custom attributes per user) or one external tenant per customer (**max 20 external tenants per Azure subscription**; 300,000 objects per tenant, raisable via support). App registrations are single-tenant only | Guests are user objects in our directory; no per-customer org object either; cross-tenant access settings are per partner tenant |
| Customer self-service SSO setup | **None.** IdP setup needs the *External Identity Provider Administrator* role in our tenant (admin center or Graph `samlOrWsFedExternalDomainFederation` / identity-provider APIs). A self-service portal would be ours to build on Graph (*inference*) | None (same roles/APIs) |
| SCIM | **No inbound SCIM into external tenants.** Entra's provisioning service is a SCIM *client* (outbound to apps); "Provisioning logs: not available" in external tenants; cross-tenant sync not available in external tenants. The new Entra **SCIM Provisioning API** (`graph.microsoft.com/rp/scim`, paid per call, needs Entra ID P1, app-only token with tenant-wide `User.ReadWrite.All`) is documented for workforce tenants only; external-tenant support *unverified*, and it is tenant-wide, not per customer org | Cross-tenant synchronization: push from a customer's **Entra** tenant only, P1 per synced user on the customer side; Microsoft says it is "intended for use within an organization". Non-Entra IdPs (Okta, Google): no inbound SCIM except the paid SCIM API above |
| MFA | Email OTP, SMS (paid add-on), passkey/FIDO2 (needs custom URL domain; not for federated or email-OTP-primary users). **No TOTP / authenticator app listed.** Conditional Access: limited (all users incl./excl.; conditions = device platform, location; grant = block / MFA / password reset; sign-in frequency, persistent session). ID Protection (risk) not available | Full Entra MFA methods and Conditional Access for guests; MFA trust of the guest's home tenant via cross-tenant access settings and its licensing: *unverified* (not checked in this pass) |
| Customer-facing audit logs / SIEM | Audit and sign-in logs; **7-day retention** in external tenants; export via Azure Monitor diagnostic settings (Log Analytics, Event Hubs, Storage) set up through an Azure Lighthouse wizard from a workforce tenant; Sentinel only from the workforce-tenant workspace (feature table still labels this preview). No per-customer audit product | Standard Entra audit/sign-in logs and diagnostic settings |
| Self-hosting | No (SaaS) | No |
| EU / US region | Country/Region chosen at tenant creation, **cannot be changed**; maps to geo-locations incl. **EMEA** and **North America**. So one EU tenant and one US tenant is possible. Go-Local (in-country) only Australia, Japan. Entra EU Data Boundary commitments exist with documented exceptions (MFA data "North America and/or in geo"; fraud IPs/phones published globally); whether every EUDB statement applies identically to external tenants: *unverified* | Same Entra geo model |
| Price points (USD list, Azure retail price API) | Free first **50,000 MAU** (all users in an external tenant count, admins included); "Basic" **$0.03 per MAU** above 50k. Add-ons (no free tier): SMS $0.03 / $0.07 / $0.15 / $0.35 per attempt by country band (Türkiye and US low; DE, NL mid-high); M2M (client credentials) $0.001 per token; Go-Local $0.02/MAU. MAU is aggregated across all tenants linked to a subscription. A separate "Core MAU" meter ($0.01625 above 50k) exists in the price API; what it maps to is *unverified*. Custom URL domain needs Azure Front Door (billed separately) | Guest MAU billing (same meters); internal staff need Entra licences; ID Governance for guests $0.75/MAU |
| Python | Standard OIDC; MSAL Python; Microsoft Graph SDK | Same |
| Token / branding | Claims mapping, claims transformation, custom claims provider (`TokenIssuanceStart` REST extension, **2,000 ms timeout, 1 retry, 50 RPS per tenant**) → an `org_id` claim from our DB is feasible; app roles and group claims (object ID only). Company branding, language customisation; custom URL domain via reverse proxy (Front Door); blocking the default domain needs a support ticket | Claims mapping / custom claims provider; branding applies to Microsoft sign-in pages |
| Limits | 200 requests/s per tenant, 20/s per IP (trial: 20/s); sign-in ≈ 4 requests, sign-up ≈ 6 | Workforce limits (not checked) |
| Compliance / DPA | Azure ISO/IEC 27001 and SOC 2 Type 2 programmes cover "Azure"; the named in-scope list is in the Service Trust Portal appendix, so External ID being named in scope is *unverified*. Same vendor and Products and Services DPA as Azure, so likely no new sub-processor *(inference; DPA coverage of External ID unverified)* | Same |

Also verified, one line each:
**Keycloak on Azure** — no managed offering: Red Hat states "there is no Red Hat build of
Keycloak as a managed-service offering"; it is entitled and supported (not operated) on Azure
Red Hat OpenShift; Red Hat support on AKS *unverified* (knowledge-base article behind login).
**Auth0 Private Cloud on Azure** — a dedicated single-tenant Auth0 instance operated by Okta
on Azure, deployable in Australia, Canada, Germany, Switzerland, UK, USA; 99.99% SLA; price
not published.
**Auth0 public cloud hosting** (checked 2026-10-18) — Auth0 describes its public cloud as
delivered on AWS, with regions US, UK, EU, Japan, Australia and Canada (one language version of
the page omits Canada) and a 99.99% SLA
([auth0.com/platform/cloud-deployment/aws](https://auth0.com/platform/cloud-deployment/aws));
Private Cloud is deployable on Azure or AWS. EU location Frankfurt with failover to Dublin comes
only from a community-forum post (*secondary*). That every public-cloud EU tenant runs on AWS is
*unverified* (no sub-processor page checked).

### What this means for us (Azure constraint)

- **Entra External ID cannot replace Keycloak/Auth0 for per-customer enterprise SSO + SCIM.**
  Federation (SAML/OIDC, domain routing) works, but there is no organization model, no
  customer self-service SSO setup, and **no inbound SCIM** from a customer's Okta / Entra /
  Google into an external tenant. Building orgs, a setup portal and a SCIM server ourselves on
  Graph is most of what we would buy a broker for.
- **Tenant-per-customer does not scale** (20 external tenants per subscription, no
  cross-tenant management); one shared tenant means the customer org lives in our database
  and claims come from the `TokenIssuanceStart` extension (2 s budget, on the sign-in path).
- **Advantages:** native Azure (same vendor, same DPA, no new processor), EMEA and North
  America tenants, cheap (50k MAU free, then $0.03/MAU), passkeys, Azure Monitor/Sentinel export.
- **Weak spots for a security product:** 7-day log retention unless exported, no TOTP,
  limited Conditional Access, no risk-based protection.
- **Workforce tenant + B2B** fits Entra-based customers (cross-tenant sync, MFA trust) but is
  not sold for third-party SaaS customers ("intended for use within an organization") and
  still has no SCIM for Okta/Google customers.
- **Azure-compatible paths that keep ADR-0024:** (a) self-operated Keycloak on AKS or Azure Red
  Hat OpenShift, one deployment per region (EU, US), same as self-hosted; (b) Auth0 Private
  Cloud on Azure (Germany / USA) if a managed broker is required, at contract pricing. External
  ID could at most be an extra upstream IdP behind the broker, not the broker. A change of
  direction needs an ADR (amend ADR-0024).

## Sign-in, onboarding and branding (Auth0 and Keycloak, verified 2026-10-18)

Facts behind ADR-0033 and the sign-in and onboarding design: hosted login only, pages and
e-mails branded and in Turkish and English, one identity organization per UMAI tenant,
environments managed as code. Vendor claims from Auth0's and Keycloak's own pages fetched on
2026-10-18 unless marked; link labels in brackets resolve to the source list at the end of this
section. *Unverified* means no vendor page fetched that day states it: do not design on it,
test it in a development tenant.

### Auth0: what each customisation needs, and which plan has it

| Feature | What it is | Needs | Plan (B2B line) | Source |
|---|---|---|---|---|
| Theme editor and Themes API | Colours (buttons, links, text, widget, inputs, errors), **one custom font as a WOFF file by URL**, borders and radii, widget (logo URL, position, height), page background and widget alignment; `POST /api/v2/branding/themes`, scope `update:branding` | Universal Login (not Classic); no custom domain stated | "Configurable Login Experience": all plans incl. Free | [themes], [themes-api], [pricing] |
| Page template (Liquid) | HTML around the login widget, `PUT /api/v2/branding/templates/universal-login`; must contain `auth0:head` and `auth0:widget`; **100 KB** max; variables: application, branding, tenant, organization, custom domain, locale, prompt and screen names, texts. Widget internals cannot be styled (class names change every build); only `--prompt-width`, `--form-max-width`, `--font-family` and the logo are supported | **A custom domain**: served only on `<custom domain>/authorize` | Not named; reachable on Free through its one custom domain; a plan entitlement is *unverified* | [templates] |
| Prompt partials ("Customize Signup and Login") | Extra fields and content at fixed insertion points on login and sign-up screens (and passkey enrolment); 10,000 characters per partial; field names prefixed `ulp-` | Custom domain **and** page template | **Not on Free**; Essentials and up | [partials], [pricing] |
| Advanced Customizations (ACUL) | Our own JS/CSS for any screen, served from our CDN, Auth0 serves a host page with Subresource Integrity; SDKs `@auth0/auth0-acul-js`, `@auth0/auth0-acul-react`; ~76 screens incl. `login-id`, `login-password`, `signup`, `reset-password-*`, `mfa-*`, `organization-selection`, `organization-picker`, `accept-invitation`, `email-verification-result`, `logout-complete`; configured per screen (dashboard, `PATCH /v2/prompts/{prompt}/screen/{screen}/rendering`, CLI `auth0 acul`, Terraform `auth0_prompt_screen_renderer`) | Custom domain, first-party application, identifier-first, our CDN and CI/CD | **GA since 2025-12-12**. Early Access was "all paid customers"; current GA entitlement *unverified* (a 2025 staff forum reply says all paid plans, *secondary*) | [acul-ga], [acul-ea], [acul-start], [acul-screens] |
| Custom domain, Auth0-managed certificate | One CNAME, kept permanently; certificates renewed every 3 months; CNAME flattening and proxying unsupported; 4 h wait after a failed verification | DNS | **Free: 1, with credit-card verification** (not charged); Essentials and up included; one domain per tenant | [cd-managed], [cd], [pricing] |
| Custom domain, self-managed certificate | Our reverse proxy, TXT ownership record, `cname-api-key` header | — | **Enterprise only** | [cd-self] |
| Multiple custom domains | Up to 20 per tenant, GA 2026-04 | — | Enterprise only | [cd-multi] |
| Languages | **Turkish (`tr`) is supported**; `enabled_locales` in tenant settings, first entry = default, only English enabled by default; chosen by `ui_locales` on `/authorize`, otherwise `Accept-Language`, otherwise the default (no precedence documented beyond that); OAuth flows only | — | No plan stated | [i18n] |
| Custom text | `PUT /api/v2/prompts/{prompt}/custom-text/{language}` replaces all texts of a prompt and language | — | Plan and custom-domain requirement *unverified* (none stated) | [text] |

Features that depend on a custom domain, besides the three above: customising the organization
invitation prompt and e-mail; links in e-mails use it only when "Use Custom Domain in Emails" is
on; **passkeys are bound to the domain** (relying-party id), so the login domain should be final
before passkeys are enabled; Auth0's developer keys for social connections stop working
[invite], [cd-features], [cd].

### Auth0: e-mail

- The built-in provider is for testing only: no custom templates, sender fixed to
  `no-reply@auth0user.net`, 10 e-mails per minute [smtp].
- Providers: Amazon SES, **Azure Communication Services** (`azure_cs`), Mandrill, Microsoft 365,
  Resend, SendGrid, SparkPost, Mailgun, generic SMTP, or anything through one
  `custom-email-provider` Action [smtp], [smtp-action], [email-api].
- Plan: the pricing row "Email Workflow" is **not on Free**, included from Essentials; a
  2026-05-06 changelog entry says the Resend integration is on all plans. Whether Free can use a
  custom provider with custom templates is *unverified* [pricing], [changelog-full].
- Templates: verification link, verification code, welcome, change password (link and code),
  blocked account, password breach alert, MFA enrolment, MFA verification code, passwordless OTP,
  user invitation. **No "password changed" confirmation template** [email-templates].
- Customising any template needs an external provider; Liquid in From, Subject, Redirect To and
  body; a From address at `@auth0.com` disables customisation [email-templates-2].
- **One template per e-mail type**: languages are branches inside it (`{% if %}` on
  `request_language`, or on `user.user_metadata.lang` set by us). How `request_language` is
  derived is *unverified* [email-customize], [email-liquid].
- Variables include `user.*`, `organization.*` (when the flow runs through an organization),
  `custom_domain.*`; the invitation e-mail adds `inviter.name` and `roles.*` [email-liquid],
  [email-liquid-2].
- **Custom "Redirect To" is unavailable on non-Enterprise tenants created on or after
  2026-05-05** (403). Link lifetime defaults to 432,000 s (5 days) [email-customize].

### Auth0: organizations

| Topic | Fact | Source |
|---|---|---|
| Caps | Free **5 organizations**; Essentials and Professional "unlimited" subject to system limits; system limits: 100,000 organizations per tenant, 100,000 members per organization, 10 connections per organization, 100 discovery domains per organization. Organizations per user: *unverified* | [pricing], [entity-limits] |
| Fields | `name`: lowercase letters, digits, `_`, `-`, 1–50 characters; `display_name` 1–255; `metadata` ≤ 25 string properties of ≤ 255 characters; branding (`logo_url` HTTPS, `colors.primary`, `colors.page_background`) overrides the tenant's | [org-create], [org-api], [org-first] |
| Restrictions | Universal Login only; no password grant, device flow or WS-Fed; no custom domain per organization | [org-overview] |
| Application setting | `organization_usage`: `deny` (Individuals, default), `require` (Business Users), `allow` (Both: an organization member or a personal account). `organization_require_behavior`: `no_prompt` (default; a request without a valid organization is rejected), `pre_login_prompt` (only with `require`), `post_login_prompt` (credentials, then choose). The docs contradict themselves on whether `allow` admits a sign-in without `organization`: **test before relying on it** | [org-behavior] |
| Requesting an organization | `organization=<org id>` on `/authorize`; the `name` too once the tenant-wide setting "Allow Organization Names in Authentication API" is on, which also adds `org_name` to tokens. Auth0 advises validating `org_id` and not renaming organizations | [org-name] |
| Token | `org_id` (e.g. `org_9ybsU1dN2dKfDkBi`) in ID and access tokens | [org-tokens] |
| Several organizations | Without `organization`, a member of several is sent to Auth0's picker, which shows the first 20. The chosen organization is not bound to the SSO session; switching = a new redirect with the other organization. Silent switching with `prompt=none`: *unverified* | [org-flows], [org-auth], [org-switch] |
| Discovery by domain | "Organization Discovery by Domain" and "Self-Service Domain Verification" GA 2026-04-28; `POST /organizations/{id}/discovery-domains` returns a TXT record to publish; only verified domains route users; discovery does not restrict who may sign up. Plan: *unverified* | [changelog], [org-domains], [org-flows] |
| Invitations | `POST /api/v2/organizations/{id}/invitations`: `inviter.name`, `invitee.email`, `client_id` required; optional `connection_id`, `roles`, metadata, **`ttl_sec` (default 7 days, maximum 30 days)**, `send_invitation_email` (default true; false returns the URL for us to deliver). Response: `invitation_url`, `ticket_id`, `expires_at`. URL: `<application login URI>?invitation=<ticket>&organization=<org id>&organization_name=<name>`; our login route forwards `invitation` and `organization` to `/authorize` | [inv-api], [inv-send], [invite] |
| Application login URI | Required for invitations; **must be HTTPS and cannot point to `localhost`**; the tenant's default login route is the fallback | [login-routes] |
| Accepting | New users sign up, existing users sign in, both with the invited address; **acceptance marks the e-mail verified**; an expired invitation is marked expired and later deleted | [invite] |
| Membership | `POST`/`DELETE /organizations/{id}/members` (users must exist); auto-membership per connection (`assign_membership_on_login`); organization roles exist (organization-scoped roles are Early Access); effect of removal on sessions not documented | [members-add], [members-del], [org-roles], [org-conn] |
| Delegated admin | My Organization API and embeddable components: Early Access, manage an **existing** organization (members, invitations, domains, IdPs), no create operation. Self-Service SSO is now "Self-Service Enterprise Configuration" (tickets, ≤ 20 profiles per tenant), on B2B Free, Essentials, Professional | [my-org], [self-service], [pricing] |

### Auth0: Actions, MFA, plans, limits, configuration as code

- **Actions:** 20 s per trigger, 100 kB per Action, 100 per tenant, 20 per trigger, 30 secrets
  per Action. Post-login: deny, custom claims (namespaced; `org_id`, `roles`, `permissions`
  restricted; collisions dropped silently), redirect with `onContinuePostLogin` (not with
  `prompt=none`), `api.multifactor.enable`, `api.authentication.challengeWith` / `enrollWith`,
  forms. `event.organization` only in organization transactions. Pre-registration is blocking,
  post-registration asynchronous [act-limits], [entity-limits], [post-login-api],
  [post-login-event], [act-redirect], [claims], [forms], [pre-reg], [post-reg].
- **MFA:** factors OTP, WebAuthn (security key, device biometrics), e-mail, SMS, voice, push,
  recovery codes, Duo; passkeys are a first factor, not MFA. **Free has neither "Pro" nor
  "Enterprise" MFA factors**; Essentials has Pro (Enterprise factors as an add-on); which factor
  is in which tier is *unverified*. The ID token carries `amr: ["mfa"]` after a challenge and
  **not after silent authentication or a refresh** [mfa-factors], [pricing], [mfa],
  [mfa-actions], [step-up].
- **Plans (B2B list prices, 2026-10-18):** Free $0 up to 25,000 MAU; Essentials from $150/month
  and Professional from $800/month (at 500 MAU); Enterprise by quote. Tenants included: Free 1,
  **Essentials 3**, Professional 12. Actions + Forms: 5 / 10 / 15. Log retention 1 / 5 / 10 / 30
  days. Enterprise connections 1 / 3 / 5 (+$100/month each up to 30). Passkeys on all plans
  [pricing].
- **Rate limits:** Free Management API **2 requests/s** (burst 2), Authentication API 300/min.
  Essentials/Professional: Management API organization writes **25/min** (burst 5), user writes
  and member writes 200/min, everything else 150/min; Authentication API 25/s. Load tests only
  on Enterprise with approval. Production limits apply only to tenants tagged Production
  [rl-free], [rl-ess], [rl], [load-test], [environments].
- **Environments:** Auth0 recommends one tenant per environment, tagged Development, Staging or
  Production (documented effect: rate limits) [environments].
- **Configuration as code:** Terraform provider `auth0/auth0` **v1.59.0 (2026-10-07)** has
  resources for tenant, client, connection, organization (not used by us), action and trigger
  bindings, branding, branding theme, custom domain and its verification, e-mail provider and
  templates, prompt, prompt custom text, prompt partials, screen renderer (ACUL), resource
  server, forms, flows, self-service profiles. Deploy CLI **9.1.0** covers the same areas with
  keyword replacement (`@@KEY@@`, `##KEY##`), `AUTH0_EXCLUDED: ["organizations"]`,
  `AUTH0_ALLOW_DELETE` off by default, dry run GA 2026-04-22 [tf-releases], [tf-resources],
  [dc-releases], [dc], [dc-keywords], [dc-exclude], [changelog].

### Keycloak: themes, languages, e-mail, organizations

- **Version:** 26.8.0 released 2026-10-01; **26.8.1 on 2026-10-13** (security fixes). Breaking in
  26.8.1: with "Enable StartTLS" set, STARTTLS is mandatory and no longer falls back to plain
  text [kc-2680], [kc-2681], [kc-upgrading].
- **Theme types:** login, account, admin, email, welcome; chosen per realm, a client can override
  the login theme. `theme.properties`: `parent`, `import`, `styles`, `scripts`, `locales`,
  `kcXxxClass` properties, `darkMode`, `${env.VAR:default}`. Deployed as a directory in `themes/`
  or a JAR in `providers/` (rebuild needed when running `--optimized`; for `themes/` directories
  *unverified*). Development: `--spi-theme--cache-themes=false --spi-theme--cache-templates=false
  --spi-theme--static-max-age=-1` [kc-themes], [kc-providers].
- **Login theme:** `keycloak.v2` (PatternFly 5) is the default since 26.0. It overrides most base
  templates but **inherits `select-organization.ftl`, `info.ftl` and `error.ftl` from `base`**, so
  a custom theme must style those itself. The docs recommend CSS and `theme.properties` over
  template overrides; `footer.ftl` adds a footer. 26.8.0 redesigned identity-provider buttons
  (custom overrides may break). "Quick Theme" (logos and colours from the admin console, exported
  as a JAR) is **preview** [kc-2600], [kc-v2-props], [kc-themes], [kc-upgrading],
  [kc-quick-theme].
- **Keycloakify** v11.16.3 (2026-10-15, MIT) builds themes from React; its login theme ports the
  **v1** templates, v2 support is unreleased; e-mail themes through FreeMarker or the Vite-only
  `keycloakify-emails` plugin [kcfy], [kcfy-v2], [kcfy-email].
- **Languages:** **Turkish is bundled**: login messages complete (525/525 keys), e-mail messages
  68 of 73 keys. Enabled per realm (`internationalizationEnabled`, `supportedLocales`,
  `defaultLocale`). Locale order: the person's choice on the page → user attribute `locale` →
  the client's request (`ui_locales`; parameter `kc_locale`) → cookie `KEYCLOAK_LOCALE` →
  `Accept-Language` → realm default → English. Themes override with
  `messages/messages_tr.properties`; realm overrides exist but are not recommended [kc-tr-login],
  [kc-tr-email], [kc-admin], [kc-l10n].
- **E-mail theme:** FreeMarker in `html/` and `text/`: `email-verification`, `password-reset`,
  `executeActions`, `email-update-confirmation`, **`org-invite`**, `identity-provider-link`, event
  notifications; subjects as message keys (`orgInviteSubject`, …). The recipient's `locale`
  attribute decides the language (an invitee without an account falls back to cookie, header,
  realm default: our reading of the source). SMTP per realm (host, port, SSL or StartTLS,
  password from a vault, or XOAUTH2 since 26.2) [kc-email], [kc-email-msgs], [kc-email-src],
  [kc-admin], [kc-2620].
- **Organizations** (since 26.0; enabled per realm): invite by e-mail (`POST
  /admin/realms/{realm}/organizations/{id}/members/invite-user`) or an existing user
  (`invite-existing-user`); a new user must register with the invited address; template
  `org-invite.ftl`. **Invitation lifetime is the realm-wide "admin-initiated action lifespan",
  default 12 h** (no per-invitation setting). Invitation list, resend and delete since 26.5.
  Identity-first login with domain routing (per domain since 26.8). Tokens: scope `organization`
  (several memberships → `select-organization.ftl`), `organization:<alias>` or `organization:*`;
  claim **`"organization": {"<alias>": {…}}`** (ids and attributes only if the mapper is set to
  include them) [kc-admin], [kc-org-src], [kc-2650], [kc-2680], [kc-rest].
- **Configuration as code:** `kc.sh start --import-realm` imports realm JSON (themes, SMTP,
  locales, `localizationTexts`, `actionTokenGeneratedByAdminLifespan`, organizations) but **skips
  existing realms**; the operator's `KeycloakRealmImport` is create-only too. Updates need
  keycloak-config-cli (adorsys, Apache-2.0, v6.5.1 of 2026-05-22, organizations since 6.5.0;
  support for Keycloak 26.7/26.8 *unverified*) or the admin API [kc-import], [kc-operator],
  [kc-config-cli].

### What this means for us (sign-in and onboarding)

- **A custom login domain is the gate** to page templates, partials, ACUL, branded invitation
  links and stable passkeys. Free allows one (credit card); each environment's tenant gets one.
- **Free is not enough for the brief:** 5 organizations, no partials, "Email Workflow" not
  listed, no Pro MFA factors, 2 Management API requests/s. **B2B Essentials** (from $150/month,
  3 tenants included) covers development and beta tenants; production waits for Q35.
- **Brand reach on hosted pages is the theme plus the page frame**: widget internals cannot be
  styled without ACUL. ACUL is GA but its plan is *unverified*; adopt it only if the brand owner
  rejects the theme-plus-template result.
- **E-mails are one template per type** with Liquid language branches; we keep the person's
  locale in `user_metadata` so Auth0's e-mails follow it. A "password changed" e-mail and
  security notices are ours to send (no Auth0 template).
- **Invitations:** Auth0's 7-day default fits ADR-0033; sending the e-mail ourselves
  (`send_invitation_email: false`) keeps the template pipeline and language in our hands and lets
  the link point at our landing page. Keycloak's lifetime is realm-wide (default 12 h), so the
  self-hosted realm sets it to 7 days.
- **The organization in the token differs by vendor** (`org_id` string vs `organization` map;
  `organization` parameter vs `organization:<alias>` scope): the console needs a two-value
  per-issuer setting, not vendor code.
- **Test before designing on:** `organization_usage: allow` admitting a sign-in without an
  organization; silent organization switch with `prompt=none`; where the verification and reset
  links land without "Redirect To" on a tenant created after 2026-05-05; how `request_language`
  is derived.
- **End-to-end tests must be frugal:** 25 organization writes per minute (Essentials), 5
  organizations on Free; runs clean up and are serialised per tenant.

Sources (accessed 2026-10-18; every link checked to resolve that day): the bracketed labels
above are links; their URLs are listed in this file's Markdown source below this line.

[pricing]: https://auth0.com/pricing
[themes]: https://auth0.com/docs/customize/login-pages/universal-login/customize-themes
[themes-api]: https://auth0.com/docs/api/management/v2/branding/post-branding-theme
[templates]: https://auth0.com/docs/customize/login-pages/universal-login/customize-templates
[partials]: https://auth0.com/docs/customize/login-pages/universal-login/customize-signup-and-login-prompts
[acul-ga]: https://auth0.com/changelog/v202549.md
[acul-ea]: https://auth0.com/changelog/v202505.md
[acul-start]: https://auth0.com/docs/customize/login-pages/advanced-customizations/getting-started
[acul-screens]: https://auth0.com/docs/libraries/acul/js-sdk/Screens.md
[cd]: https://auth0.com/docs/customize/custom-domains
[cd-managed]: https://auth0.com/docs/customize/custom-domains/auth0-managed-certificates
[cd-self]: https://auth0.com/docs/customize/custom-domains/self-managed-certificates
[cd-multi]: https://auth0.com/docs/customize/custom-domains/multiple-custom-domains
[cd-features]: https://auth0.com/docs/customize/custom-domains/configure-features-to-use-custom-domains
[i18n]: https://auth0.com/docs/customize/internationalization-and-localization/universal-login-internationalization
[text]: https://auth0.com/docs/customize/login-pages/universal-login/customize-text-elements
[smtp]: https://auth0.com/docs/customize/email/smtp-email-providers
[smtp-action]: https://auth0.com/docs/customize/email/smtp-email-providers/custom/configure-action
[email-api]: https://auth0.com/docs/api/management/v2/emails/post-provider
[changelog-full]: https://auth0.com/changelog/llms-full.txt
[changelog]: https://auth0.com/changelog
[email-templates]: https://auth0.com/docs/customize/email/email-templates/email-template-descriptions
[email-templates-2]: https://auth0.com/docs/customize/email/email-templates
[email-customize]: https://auth0.com/docs/customize/email/email-templates/customize-email-templates
[email-liquid]: https://auth0.com/docs/customize/email/email-templates/use-liquid-syntax-in-email-templates
[email-liquid-2]: https://auth0.com/docs/customize/email/email-templates/supported-liquid-syntax
[entity-limits]: https://auth0.com/docs/troubleshoot/customer-support/operational-policies/entity-limit-policy
[org-create]: https://auth0.com/docs/manage-users/organizations/configure-organizations/create-organizations
[org-api]: https://auth0.com/docs/api/management/v2/organizations/post-organizations
[org-first]: https://auth0.com/docs/manage-users/organizations/create-first-organization
[org-overview]: https://auth0.com/docs/manage-users/organizations/organizations-overview
[org-behavior]: https://auth0.com/docs/manage-users/organizations/configure-organizations/define-organization-behavior
[org-name]: https://auth0.com/docs/manage-users/organizations/configure-organizations/use-org-name-authentication-api
[org-tokens]: https://auth0.com/docs/manage-users/organizations/using-tokens
[org-flows]: https://auth0.com/docs/manage-users/organizations/login-flows-for-organizations
[org-auth]: https://auth0.com/docs/get-started/architecture-scenarios/multiple-organization-architecture/single-identity-provider-organizations/authentication
[org-switch]: https://support.auth0.com/center/s/article/switch-logged-in-user-organization-using-an-organization-picker
[org-domains]: https://auth0.com/docs/api/management/v2/organizations/post-discovery-domains
[inv-api]: https://auth0.com/docs/api/management/v2/organizations/post-invitations
[inv-send]: https://auth0.com/docs/manage-users/organizations/configure-organizations/send-membership-invitations
[invite]: https://auth0.com/docs/manage-users/organizations/configure-organizations/invite-members
[login-routes]: https://auth0.com/docs/authenticate/login/auth0-universal-login/configure-default-login-routes
[members-add]: https://auth0.com/docs/api/management/v2/organizations/post-members
[members-del]: https://auth0.com/docs/api/management/v2/organizations/delete-members
[org-roles]: https://auth0.com/docs/manage-users/organizations/configure-organizations/add-member-roles
[org-conn]: https://auth0.com/docs/manage-users/organizations/configure-organizations/enable-connections
[my-org]: https://auth0.com/docs/manage-users/my-organization-api
[self-service]: https://auth0.com/docs/authenticate/enterprise-connections/self-service-enterprise-configuration
[act-limits]: https://auth0.com/docs/customize/actions/limitations
[post-login-api]: https://auth0.com/docs/customize/actions/explore-triggers/signup-and-login-triggers/login-trigger/post-login-api-object
[post-login-event]: https://auth0.com/docs/customize/actions/explore-triggers/signup-and-login-triggers/login-trigger/post-login-event-object
[act-redirect]: https://auth0.com/docs/customize/actions/explore-triggers/signup-and-login-triggers/login-trigger/redirect-with-actions
[claims]: https://auth0.com/docs/secure/tokens/json-web-tokens/create-custom-claims
[forms]: https://auth0.com/docs/customize/forms/render
[pre-reg]: https://auth0.com/docs/customize/actions/explore-triggers/signup-and-login-triggers/pre-user-registration-trigger
[post-reg]: https://auth0.com/docs/customize/actions/explore-triggers/signup-and-login-triggers/post-user-registration-trigger
[mfa]: https://auth0.com/docs/secure/multi-factor-authentication
[mfa-factors]: https://auth0.com/docs/secure/multi-factor-authentication/multi-factor-authentication-factors
[mfa-actions]: https://auth0.com/docs/secure/multi-factor-authentication/customize-mfa/customize-mfa-selection-universal-login
[step-up]: https://auth0.com/docs/secure/multi-factor-authentication/step-up-authentication/configure-step-up-authentication-for-web-apps
[rl]: https://auth0.com/docs/troubleshoot/customer-support/operational-policies/rate-limit-policy
[rl-free]: https://auth0.com/docs/troubleshoot/customer-support/operational-policies/rate-limit-policy/rate-limit-configurations/free-public
[rl-ess]: https://auth0.com/docs/troubleshoot/customer-support/operational-policies/rate-limit-policy/rate-limit-configurations/essentials-professional-b2b
[load-test]: https://auth0.com/docs/troubleshoot/customer-support/operational-policies/load-testing-policy
[environments]: https://auth0.com/docs/get-started/auth0-overview/create-tenants/set-up-multiple-environments
[tf-releases]: https://github.com/auth0/terraform-provider-auth0/releases
[tf-resources]: https://github.com/auth0/terraform-provider-auth0/tree/main/docs/resources
[dc-releases]: https://github.com/auth0/auth0-deploy-cli/releases
[dc]: https://github.com/auth0/auth0-deploy-cli
[dc-keywords]: https://github.com/auth0/auth0-deploy-cli/blob/master/docs/keyword-replacement.md
[dc-exclude]: https://github.com/auth0/auth0-deploy-cli/blob/master/docs/excluding-from-management.md
[kc-2600]: https://www.keycloak.org/2024/10/keycloak-2600-released
[kc-2680]: https://www.keycloak.org/2026/10/keycloak-2680-released
[kc-2681]: https://www.keycloak.org/2026/10/keycloak-2681-released
[kc-upgrading]: https://www.keycloak.org/docs/latest/upgrading/index.html
[kc-themes]: https://www.keycloak.org/ui-customization/themes
[kc-providers]: https://www.keycloak.org/server/configuration-provider
[kc-v2-props]: https://github.com/keycloak/keycloak/blob/26.8.1/themes/src/main/resources/theme/keycloak.v2/login/theme.properties
[kc-quick-theme]: https://www.keycloak.org/ui-customization/quick-theme
[kcfy]: https://github.com/keycloakify/keycloakify
[kcfy-v2]: https://github.com/keycloakify/keycloakify/discussions/729
[kcfy-email]: https://docs.keycloakify.dev/theme-types/email-theme
[kc-tr-login]: https://github.com/keycloak/keycloak/tree/26.8.1/themes/src/main/resources-community/theme/base/login/messages
[kc-tr-email]: https://github.com/keycloak/keycloak/tree/26.8.1/themes/src/main/resources-community/theme/base/email/messages
[kc-admin]: https://www.keycloak.org/docs/latest/server_admin/index.html
[kc-l10n]: https://www.keycloak.org/ui-customization/localization
[kc-email]: https://github.com/keycloak/keycloak/tree/26.8.1/themes/src/main/resources/theme/base/email
[kc-email-msgs]: https://github.com/keycloak/keycloak/blob/26.8.1/themes/src/main/resources/theme/base/email/messages/messages_en.properties
[kc-email-src]: https://github.com/keycloak/keycloak/tree/26.8.1/services/src/main/java/org/keycloak/email/freemarker
[kc-2620]: https://www.keycloak.org/docs/latest/release_notes/index.html#keycloak-26-2-0
[kc-2650]: https://www.keycloak.org/docs/latest/release_notes/index.html#keycloak-26-5-0
[kc-org-src]: https://github.com/keycloak/keycloak/tree/26.8.1/services/src/main/java/org/keycloak/organization/admin/resource
[kc-rest]: https://www.keycloak.org/docs-api/latest/rest-api/index.html
[kc-import]: https://www.keycloak.org/server/importExport
[kc-operator]: https://www.keycloak.org/operator/realm-import
[kc-config-cli]: https://github.com/adorsys/keycloak-config-cli

## Sources (accessed 2026-10-17)

WorkOS: workos.com/pricing · /docs/sso · /docs/admin-portal · /docs/directory-sync ·
/docs/audit-logs · /docs/on-prem-deployment · /legal/data-processing-addendum.
Auth0: auth0.com/pricing · /docs/authenticate/enterprise-connections/self-service-SSO ·
/docs/authenticate/protocols/scim/configure-inbound-scim · /docs/customize/log-streams ·
/docs/deploy-monitor/deployment-options · /docs/troubleshoot/customer-support/operational-policies/public-cloud-service-endpoints ·
/docs/manage-users/organizations · /docs/secure/data-privacy-and-compliance.
Stytch: stytch.com/pricing · /docs/b2b/guides/sso/overview · /docs/b2b/guides/scim/overview ·
/docs/resources/workspace-management/event-logs · /docs/resources/policies/security-and-trust/compliance/data-compliance.
Keycloak: keycloak.org/2026/10/keycloak-2680-released · /docs/26.8.0/release_notes ·
/docs/latest/server_admin · github.com/keycloak/keycloak/blob/main/LICENSE.txt ·
docs.redhat.com (Red Hat build of Keycloak 26.2: organizations, auditing).
Zitadel: zitadel.com/pricing · /docs/concepts/structure/organizations · /docs/guides/manage/user/scim2 ·
/docs/concepts/features/audit-trail · /docs/self-hosting/deploy/overview ·
github.com/zitadel/zitadel/blob/main/LICENSE · zitadel.com/blog/apache-to-agpl ·
help.zitadel.com/security-compliance/certifications-and-compliance.
Clerk: clerk.com/pricing. Ory: ory.com/pricing · github.com/ory/polis.
Microsoft Entra External ID (learn.microsoft.com/en-us/entra/…): external-id/customers/concept-supported-features-customers ·
/external-id/customers/overview-customers-ciam · /external-id/external-identities-pricing ·
/external-id/direct-federation · /external-id/direct-federation-overview ·
/external-id/customers/how-to-custom-oidc-federation-customers ·
/external-id/customers/concept-multifactor-authentication-customers ·
/external-id/customers/reference-service-limits · /external-id/customers/reference-group-app-roles-support ·
/external-id/customers/how-to-create-external-tenant-portal · /external-id/customers/how-to-azure-monitor ·
/external-id/customers/concept-custom-url-domain · /fundamentals/data-residency · /fundamentals/data-storage-eu ·
/identity/app-provisioning/known-issues · /identity/app-provisioning/entra-id-scim-api-reference ·
/identity/app-provisioning/enable-scim-api · /identity/multi-tenant-organizations/cross-tenant-synchronization-overview.
Azure AD B2C end of sale: learn.microsoft.com/en-us/azure/active-directory-b2c/overview.
Prices: prices.azure.com/api/retail/prices (filter productName contains "External ID"; USD, Global);
azure.microsoft.com/pricing/details/microsoft-entra-external-id (free 50k MAU; amounts rendered client-side).
Compliance: learn.microsoft.com/en-us/azure/compliance/offerings/offering-iso-27001 · /offering-soc-2 ·
/cloud-services-in-audit-scope.
Keycloak on Azure: access.redhat.com/articles/7044244. Auth0 on Azure:
auth0.com/docs/deploy-monitor/deploy-private-cloud/private-cloud-on-azure.
