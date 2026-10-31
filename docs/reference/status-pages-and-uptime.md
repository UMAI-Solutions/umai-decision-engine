# Status pages and external uptime monitoring

**Last verified:** 2026-10-31 · **Used by:** launch plan S0-11 (monitoring, status page), the private
beta on the single VM (ADR-0032), ADR-0031 §5 (Front Door only for content-free surfaces) ·
Labels: see [README](README.md). **Vendor claims** from official pricing pages, docs and terms unless
marked otherwise. Prices are list prices per month, excl. VAT; "annual" = per month, billed yearly.

The beta runs on one Azure VM in West Europe. Two needs: **(a)** checks of 2–4 HTTPS URLs
(`/healthz`, TLS certificate, the console's sign-in redirect) from **outside Azure's network**, with
e-mail alerts; GitHub-hosted runners do not qualify ("GitHub hosts Windows runners and Linux runners
… on virtual machines in Azure"); **(b)** a public page on `status.<company domain>` in English and
Turkish, with incident posts and e-mail subscriptions. Use is commercial, so a free tier counts only
if its terms allow that.

**Probe hosting, label *RDAP*:** where a vendor publishes its probe IP list we looked up the owner of
every distinct /16 prefix with RDAP (rdap.org, ARIN) on 2026-10-31. That shows who owns the address
block, not which building the probe sits in.

## 1. Monitoring side (a)

| Vendor | Free tier: commercial use? what it covers | Cheapest paid plan for (a)+(b) | Interval | Probe hosting (Azure?) | Entity, data location, DPA |
|---|---|---|---|---|---|
| **Better Stack** | Pricing page heads the free tier "Free for personal projects"; the ToS has **no** commercial-use clause → **ambiguous**. Free: 10 monitors + 10 heartbeats, 3-min checks, 1 status page, Slack and e-mail alerts | 1 **Responder** seat $29 annual / $34 monthly; +50 monitors $25; extra status page $15; 1,000 subscribers included, +1,000 $40. Which features need the seat: **unverified** | Free 3 min; "up to 30 seconds" | ≥ 4 locations per check, incident when ≥ 3 fail; regions EU, US, AS, AU. *RDAP*: Hetzner (DE, US, SG), Linode/Akamai, HODL s.r.o. (CZ), a Better Stack block. **No Azure** | Better Stack, Inc. (Delaware; New York law). "All data is stored in the EU regions" by default (Enterprise: custom location); DPA; SOC 2 Type 2 |
| **UptimeRobot** | **Yes**: ToS §3 "available for any use, including commercial and business use". Free: 50 monitors, 5 min, 1 page; **no** own domain, subscribers, translations, SSL monitoring or multi-location | **Team**: USD page $30 (with $35 struck through), EUR page €35 annual / €41 monthly. Solo (USD $9, $10 struck through; €9 annual / €10 monthly) adds own domain, translations, SSL monitoring, 60 s, but **no subscribers** | Free 5 min · Solo 60 s · Team 30 s | 4 regions (NA, EU, Asia, AU). DPA names AWS, DigitalOcean, Hetzner, Limestone; *RDAP* of the 103-address list agrees. **No Azure** | UptimeRobot s. r. o., Bratislava (**EU entity**, Slovak law). DPA v2.2 (2026-08-28): primary hosting EU, secondary US; SCCs; e-mail via SendGrid (US) |
| **Instatus** | ToS has no personal-only clause (use "for your own internal business purposes"). Free Starter: 15 monitors, 2 min, page without custom domain, 200 subscribers | **Pro "from $20/mo"** (page title; amounts not rendered): 50 monitors, 30 s, custom domain, 5,000 subscribers, e-mail + SMS alerts | 2 min · Pro 30 s | "United States, Canada, Europe, Asia"; providers not published (**unverified**) | Instatus, Inc. (Delaware law); data "primarily in the United States"; DPA |
| **OpenStatus** | ToS sets no plan limits; Hobby = 1 monitor, 10 min, page without custom domain → does not cover (a) | **Starter $30 / $25 annual**: 20 monitors, 1 min, 6 regions per monitor, custom domain, subscribers, locale switcher | Hobby 10 min · Starter 1 min · Pro 30 s | 27 locations: Fly.io (17), Railway (4), Koyeb (6). **No Azure** | Terms under French law; legal entity not named; **no DPA found** (**unverified**). Sub-processors: Turso (global), Tinybird (EU), Resend e-mail (EU), Vercel/Fly/Koyeb/Railway. AGPL-3.0, self-hostable |
| **Hyperping** | ToS has no personal-only clause; Free: 20 monitors, 5 min, page on Hyperping domain only, no subscribers | **Essentials $24 annual / $29**: 50 monitors, 30 s, custom HTTPS domain, 100 subscribers; languages need **Pro $74 / $89** | 5 min · paid 30 s | 18 regions (London, Paris, Frankfurt, Amsterdam in EU); probes on DigitalOcean, AWS, Scaleway. **No Azure** | Hyperping SAS, Paris (French law); data DigitalOcean FRA1; DPA for all customers; no own SOC 2 / ISO |
| **Checkly** | ToS grants use for "internal business purposes", no free-plan restriction; Hobby marketed "for personal projects and learning": 10 uptime monitors, 2 min, max 3 of 6 locations, status page without custom domain | **Starter $24** (annual; monthly price not shown): 50 monitors, 1 min, custom domain (comparison icons, inferred). **Incident automation** from checks only on **Team $64** | 2 min · 1 min · Team 30 s | 22 locations; all on AWS except four "Checkly hosted" (N. Virginia, N. California, Frankfurt, London); provider "may change over time". **No Azure** | Checkly Inc., Boston (New York law); data AWS eu-west-1, check results may stay in the run region; SOC 2 Type 2; DPA not on the security page (**unverified**) |
| **StatusCake** | **No**: ToS 4.5 "The Free Account is for your own personal use" | Superior $20.41 annual / $24.49 (100 monitors, 1 min); status-page features per plan not stated (**unverified**) | Free 5 min · 1 min · Business 30 s | *RDAP*: Google Cloud, DigitalOcean, AWS, Vultr. **No Azure**. Region choice paid only | TrafficCake Ltd (England; English law) |
| **Atlassian Statuspage** | Status page only, no monitoring on the plan list → needs a separate monitor for (a) | see §2 | — | — | Statuspage is not among Atlassian's data-residency products |

## 2. Status-page side (b)

| Vendor | Custom domain | E-mail subscribers | Languages, Turkish | Page driven by own monitors |
|---|---|---|---|---|
| **Better Stack** | CNAME; "Free status page on your subdomain … Completely free" | Yes; whole page or chosen components, double opt-in; sent for new reports, updates, maintenance | Ready-made **EN, CS, DE, ES, FR, NL, SV, TR**; visitors switch on one page; "Modern look" design required; pricing lists i18n as "beta". Language of subscriber e-mails: **unverified** | Monitors linked to page resources ("Fully automate your workflow or control everything manually"); whether automatic changes e-mail subscribers: **unverified** |
| **UptimeRobot** | Solo and above | **Team and above** | 11 incl. **Turkish** (added 2025-03-12). Language is set per page; "separate status pages for each language" on paid plans; no visitor switcher documented → EN and TR = two pages, two subscriber lists | Page lists the chosen monitors (product design, not re-checked) |
| **Instatus** | Pro ("Custom domains need the Pro plan"); certificate generated automatically | Yes (200 free, 5,000 Pro) | Visitor switcher; built-in list not published; own translation file can be uploaded → Turkish possible by file (**unverified**); plan **unverified** | Yes: a monitor can create an incident, publish it, notify subscribers and resolve automatically |
| **OpenStatus** | Paid plans | Yes, e-mail on each status report create/update, double opt-in; paid | Paid plans: en, fr, de, **tr**, hi, ko, ja (i18n launched 2026-03-22) | Monitors feed page components; automatic reports **unverified** |
| **Hyperping** | Essentials and above | Yes (e-mail, Slack, Teams, SMS via own Twilio) | EN, FR, DE, NL, PL, RU, SV: **no Turkish** | Marketing: outage creates an incident and notifies subscribers (docs silent) |
| **Checkly** | Starter and above (inferred) | 250 → 2,000 by plan | None documented | Tag rules open/resolve incidents: Team and Enterprise only |
| **Atlassian Statuspage** | Hobby **$29** and above (250 subscribers; Startup $99, Business $399, Enterprise $1,499; free: 100 subscribers, no domain) | Yes on all plans | Only via the third-party **Localize** app (own Localize account, paid page with custom domain); notifications go out as written (community answer, **secondary source**) | No own monitors |

**EU-based options.** UptimeRobot is itself an EU company (above). Others checked:

| Vendor | Fit | Facts |
|---|---|---|
| **Oh Dear** (data hosted in Belgium) | (a)+(b) | Solo **$17** (2 sites), every plan has every feature, no free tier (10-day trial). Check locations Frankfurt, Paris, London, Stockholm + 12 elsewhere; providers not published. Custom domain with automatic TLS; **e-mail subscribers since 2026-07** (500 per team); subscribers notified on manual updates, not on automatic monitor changes by default; "multilingual" claimed, Turkish **unverified** |
| **Statuspal** (E.M. StatusPal UG, Berlin) | (a)+(b), pricier | Hobby **$46** (1 language, 500 subscribers, 10 monitored services); 2 languages from Startup **$99**. Built-in checks once a minute from US or EU. Page hosted on US or EU servers at the customer's choice; DPA. "10+ languages", Turkish not listed (custom language on request) |
| **ilert** ("made & hosted in Germany", ISO 27001) | (b) only | Free public page without subscribers; subscribers need Pro ($19/user, min. 3 users) + status-page add-on (amount not shown). **HTTP uptime monitoring discontinued in 2024**; ilert recommends Checkly |
| **Phare** (EU-hosted, € pricing) | (a), weak (b) | Scale €5 + usage; custom domains on all plans; **no e-mail subscriptions** (RSS, Atom, Slack feeds) |

## 3. Self-built on Azure

| | Static Web Apps Free | Static Web Apps Standard | Storage account static website |
|---|---|---|---|
| Price | $0; plan labelled "For personal projects"; no SLA | **$9 per app/month** (Retail Prices API, West Europe); optional managed Front Door add-on $0.024/h (≈ $17.5/month) | Feature free; blob storage and operations only |
| Bandwidth, size | 100 GB/month per subscription, no overage ("Sites over quota are not served"); 250 MB per environment | 100 GB included, then $0.20/GB; 500 MB per environment, 2 GB total | Blob egress pricing |
| Custom domain + TLS | 2 per app; free auto-renewing certificates | 5 (plans page) / 6 (quotas page); free certificates; SLA | **HTTP only.** "Azure Storage doesn't yet natively support HTTPS with custom domains": needs Front Door (preferred) or Azure CDN. Azure CDN (classic) takes no new resources or managed certificates since 2025-08-15 and retires 2027-09-30 → **Front Door Standard, $35/month base** + traffic |
| Other | Dedicated plan retired 2025-10-31 | Private endpoint, IP restrictions | No header configuration, no auth, no CORS |

**Application Insights availability tests** (feed for such a page): Standard tests check TLS validity
and a proactive certificate-lifetime window, support verbs and headers; up to 100 tests per resource;
default 5 min; at least 5 locations recommended, up to 16. Price **$0.000645 per execution** (Retail
Prices API, West Europe): one URL from 5 locations every 5 min ≈ 43,200 runs ≈ **$28/month per URL**.
Free URL ping tests are deprecated and retire **2028-09-30** (moved from 2026-09-30). Test locations
are **Azure regions and edges** (e.g. `emea-nl-ams-azr` = West Europe), so they fail requirement (a).

**Failure domain.** An Azure-hosted page, its Front Door and its availability tests share Azure's
failure domain: an Azure-wide or global-edge incident can take the product, the alert and the page
down together. ADR-0031 §5 allows Front Door for a content-free status page; it does not make it
independent of Azure.

## 4. Consequences for the design

1. **Every SaaS candidate probes from outside Azure** (Hetzner, Akamai/Linode, DigitalOcean, AWS,
   GCP, Vultr, Fly.io, Scaleway); Application Insights does not. AWS-hosted probes are still
   independent of an Azure outage.
2. **Turkish narrows the field.** Built-in Turkish is confirmed only at Better Stack (one page, visitor
   switch), UptimeRobot (one language per page) and OpenStatus (switcher, paid). Hyperping has no
   Turkish; Statuspage needs Localize; Instatus, Statuspal and Oh Dear are unverified.
3. **Subscriber e-mail addresses are personal data**: the vendor becomes a sub-processor and needs a
   DPA. Better Stack (DPA, SOC 2 Type 2, EU storage by default), UptimeRobot (DPA with SCCs, EU
   entity, EU primary hosting) and Hyperping qualify on paper; OpenStatus has no DPA we could find;
   Instatus keeps data in the US.
4. **Cheapest combined (a)+(b), commercial, Turkish confirmed:** OpenStatus Starter $25–30, Better
   Stack one Responder $29–34, UptimeRobot Team ≈ $30–35. **Monitoring only, free and commercial:**
   UptimeRobot Free (5 min, no TLS check; Solo $9–10 adds it), Checkly Hobby, Instatus Starter.
5. **Self-building costs more and is less independent:** Static Web Apps $0–9 plus availability tests
   ≈ $28 per URL, or storage + Front Door ≥ $35, all inside Azure.
6. **Suggested for the ADR:** Better Stack with one paid Responder seat for (a)+(b) (one bilingual
   page, monitor-linked status, EU storage, DPA, probes off Azure); confirm in writing which features
   the seat unlocks and the language of subscriber e-mails. Runner-up: UptimeRobot Team (EU
   entity, but EN and TR as two pages). OpenStatus is the open-source exit. Re-verify prices at
   purchase.

## Sources (accessed 2026-10-31)

Better Stack: betterstack.com/pricing · /uptime · /status-page · /security · /terms ·
/docs/uptime/status-pages/translations · /docs/uptime/subscribing-to-status-updates ·
/docs/uptime/locations-and-regions · /docs/uptime/frequently-asked-questions ·
/docs/uptime/ssl-certificate-monitor · incidents.betterstack.com/ips-by-cluster.json.
UptimeRobot: uptimerobot.com/pricing · /pricing.md · /terms · /terms-fair-use · /dpa ·
/blog/update-status-pages-now-support-11-languages · /inc/files/ips/IPv4.txt.
Instatus: instatus.com/pricing · /help/status-page/custom-domain · /help/status-page/multi-language ·
/help/monitoring/website · /policies/terms · /policies/privacy.
OpenStatus: openstatus.dev/pricing · /docs/reference/location · /docs/guides/how-to-translate-status-page ·
/changelog/status-page-i18n · /legal/terms · /legal/privacy · /subprocessors ·
docs.openstatus.dev/status-page/subscribers · github.com/openstatusHQ/openstatus (licence).
Hyperping: hyperping.com/pricing.md · /docs/status-page/languages · /docs/monitoring/datacenter-regions ·
/docs/status-page/incidents · /terms · /security.
Checkly: checklyhq.com/pricing · /docs/status-pages · /docs/monitoring/global-locations · /security · /terms.
Statuspage: atlassian.com/software/statuspage/pricing · support.atlassian.com/statuspage/docs/translate-pages-with-localize ·
support.atlassian.com/security-and-access-policies/docs/understand-data-residency ·
community.atlassian.com (Statuspage translation threads, secondary).
StatusCake: statuscake.com/pricing · /terms-and-conditions · /kb/?p=8221 · app.statuscake.com/Workfloor/Locations.php.
EU options: ohdear.app/pricing · /feature/status-pages · /docs/status-pages/subscriptions ·
/docs/faq/can-i-choose-where-oh-dear-checks-my-site-from · statuspal.io/pricing · /privacy ·
/features/multi-language · docs.statuspal.io/platform/multi-language · /monitoring-and-automation ·
ilert.com/pricing · docs.ilert.com/developer-docs/api-version-history/discontinuation-of-uptime-monitoring ·
phare.io/pricing · /products/uptime · docs.phare.io/uptime/status-pages.
Azure: learn.microsoft.com/azure/static-web-apps/plans · /static-web-apps/quotas ·
/storage/blobs/storage-blob-static-website · /storage/blobs/storage-custom-domain-name ·
/azure-monitor/app/availability · /cdn/classic-cdn-retirement-faq · /cdn/cdn-features ·
prices.azure.com (Static Web Apps, Front Door Standard base fee, Standard Web Test Execution).
GitHub: docs.github.com/actions/concepts/runners/github-hosted-runners.
Probe ownership: rdap.org / rdap.arin.net lookups of the published IP lists above.
