# Compliance and assurance: questionnaires, SOC 2 tooling, penetration testing, legal anchors

**Last verified:** 2026-10-17 · **Used by:** launch plan and verification plan (`docs/security/`), ADR-0026 (residency), open question Q25 ·
Labels: see [README](README.md). Vendor pages are **vendor claims**; law is cited from the
official texts.

## Buyer questionnaires

| | SIG (Shared Assessments) | CSA CAIQ / STAR |
|---|---|---|
| Current version | 2026 SIG Workbook, released 2025-09-19 (ISO 42001 mapping, NIST SP 800-171 depth, ISO 27001:2022 Annex A regrouping, Lite/Core/Detail scoping presets) | CCM + CAIQ v4.1 (CAIQ v4 released 2021-06-07 with CCM v4; 4.1 release date not stated) |
| Sizes | 2025: Lite 128 questions, Core 627, Detail 1,936; 21 domains in 4 control areas. 2026 counts *unverified* ("no new risk domains", *secondary source*) | CCM v4: 197 control objectives, 17 domains. CAIQ v4: 261 yes/no questions; CAIQ-Lite: 124, free |
| Access | Licensed: included for members, otherwise annual subscription; no public price; no editing without permission | CCM free for internal use (download behind registration); STAR Level 1 self-assessment is complimentary, renewed annually, not a certification; optional "Valid-AI-ted" check $595 |
| Audit tiers | — | STAR Level 2 = third-party audit: Attestation (SOC 2 based, 1-year listing) or Certification (ISO 27001 based, 3-year) |

Practical reading: SIG Lite (~128 questions) is the floor an enterprise buyer sends first;
CAIQ-Lite is free and public, which makes it the better self-assessment to publish before a
SOC 2 report exists.

## SOC 2 evidence automation

No vendor publishes list prices (all **quote only**); the claims below are marketing claims.

| | Vanta | Drata | Secureframe |
|---|---|---|---|
| Integrations | 400+ (AWS, GCP, Azure, Okta, GitHub named); 1,200+ hourly automated tests | "hundreds" (cloud, IdP, HR, code, ticketing) | 300+; monitors 150+ cloud services |
| Mechanism | Automated evidence collection, continuous control monitoring, policy templates, auditor network (100+) | Automated evidence, continuous monitoring, alerts | Automated evidence, continuous monitoring, policy templates |
| Timeline claim | "Audit-ready in weeks, not months"; Type II "depends on scope and pace" | "Ready in weeks" | "SOC 2 in weeks, not months"; Type II observation **at least 3 months, 12 recommended** |

"Weeks" in every case means *audit-readiness*. A Type I report attests control **design** at a
point in time; a Type II report needs an **observation window** (3–12 months) before it exists.

## Penetration testing providers

| Provider | Model | Public pricing |
|---|---|---|
| Cobalt (pentest as a service) | Prepaid credits, 1 credit = 8 h, annual packages, unlimited retesting in term | "Autonomous Pentest" $3,500 per test (promotion through 2026-12-31); manual tiers quote only |
| NCC Group | CREST member / CHECK; manual, semi-automated, continuous testing; red/purple teams | *unverified* (no public price) |
| Cure53 (Berlin) | Black/white-box pentests and code audits; publishes many reports | *unverified* (enquiry by e-mail) |

## Legal anchors (official texts)

GDPR — Regulation (EU) 2016/679, OJ L 119, 4.5.2016:

| Article | What it requires of us as a processor |
|---|---|
| 28 | Controllers may use only processors giving "sufficient guarantees"; a binding contract with the mandatory terms of 28(3); prior written authorisation for sub-processors; the processor stays liable for them |
| 30 | Records of processing; 30(2) the processor keeps records per controller; 30(5) exemption below 250 employees does **not** apply when processing is non-occasional or involves special-category data (ours is non-occasional) |
| 32 | Risk-appropriate technical and organisational measures: pseudonymisation and encryption, confidentiality–integrity–availability–resilience, timely restore, regular testing |
| 44–49 | Transfers only under Chapter V: adequacy decisions (45), appropriate safeguards incl. SCCs and BCRs (46–47), third-country orders only via international agreement (48), narrow derogations (49) |

Standard Contractual Clauses: Commission Implementing Decision (EU) 2021/914 of 4 June 2021
(OJ L 199, 7.6.2021), four modules (controller→controller, controller→processor,
processor→processor, processor→controller).

Türkiye — KVKK, Law No. 6698 (Official Gazette 7.4.2016, No. 29677; official English
translation on kvkk.gov.tr, Turkish text prevails): Art. 3 defines controller and processor;
Art. 8 domestic transfer conditions; **Art. 9 (amended by Law 7499, in force 2024-06-01)**
transfer abroad on a Board adequacy decision or safeguards (Board-approved BCRs, the Board's
standard contract notified to the Authority within 5 business days, approved undertakings);
Art. 12 the controller takes all measures and is "jointly responsible" with anyone processing
on its behalf, confidentiality survives the engagement, breach notification; Art. 16 VERBİS
registration. The 2024 implementing regulation on transfers abroad (OG 2024-07-10, No. 32598)
is available in English only from *secondary sources* (law-firm notes, courtesy translations).

## Sources (accessed 2026-10-17)

Shared Assessments: sharedassessments.org/about-sig/ · /sig-faq/ · /blog/2026-sig-workbook-updates/ ·
/blog/2025-sig/ · *secondary:* mitratech.com/resource-hub/blog/sig-2026-key-updates-and-considerations/.
CSA: cloudsecurityalliance.org/star/levels · /research/cloud-controls-matrix ·
/artifacts/consensus-assessments-initiative-questionnaire-caiq-v4-0 ·
/artifacts/star-level-1-security-questionnaire-caiq-v4 · /articles/what-is-caiq.
Vendors: vanta.com/pricing · vanta.com/products/soc-2 · drata.com/pricing · drata.com/product/soc-2 ·
secureframe.com/pricing · secureframe.com/products/soc-2 · cobalt.io/pricing ·
nccgroup.com/penetration-testing-services/ · cure53.de.
Law: eur-lex.europa.eu/eli/reg/2016/679/oj/eng · eur-lex.europa.eu/eli/dec_impl/2021/914/oj/eng ·
kvkk.gov.tr/Icerik/6649/Personal-Data-Protection-Law · *secondary:* prighter.com (transfer regulation),
iapp.org/news/a/the-long-awaited-amendments-in-turkish-data-protection-law.
