# Security standards used by the gap analysis: ASVS 5.0, SOC 2 TSC, OWASP API and LLM Top 10

**Last verified:** 2026-10-17 · **Used by:** [`security/gap-analysis.md`](../security/gap-analysis.md), launch plan ·
Labels: see [README](README.md). Requirement texts are paraphrased from the primary documents;
IDs are exact.

## OWASP ASVS 5.0

- Current stable release **5.0.0, May 2025** (GitHub tag `v5.0.0_release`, 2025-05-30); no
  5.0.x patch release exists — the `latest` tag is a rolling "bleeding edge" preview. 345
  requirements in 17 chapters: V1 Encoding and Sanitization, V2 Validation and Business Logic,
  V3 Web Frontend Security, V4 API and Web Service, V5 File Handling, V6 Authentication,
  V7 Session Management, V8 Authorization, V9 Self-contained Tokens, V10 OAuth and OIDC,
  V11 Cryptography, V12 Secure Communication, V13 Configuration, V14 Data Protection,
  V15 Secure Coding and Architecture, V16 Security Logging and Error Handling, V17 WebRTC.
- Levels: L1 ≈ 20 % of requirements ("the minimum requirements to consider"); L2 adds ≈ 50 %
  ("most applications should be striving to achieve this level"); L3 the final 30 %. Levels are
  chosen by risk, not prescribed. Compared with 4.0.3: 109 requirements removed or merged,
  chapters renumbered, "V1 Architecture" dissolved, OAuth/OIDC and self-contained tokens are
  new chapters.
- Items the gap analysis uses (level in brackets): tokens 7.2.1–7.2.4 [L1], 6.5.2 [L2], 11.5.1
  [L2], 13.2.1–13.2.3 [L2], 6.3.2 [L1], 6.1.1/6.3.1 [L1], 6.3.3 [L2], 9.1.1–9.1.3, 9.2.1
  [L1], 9.2.3 [L2]; sessions 7.1.1, 7.1.2, 7.3.1, 7.3.2, 7.4.3–7.4.5, 7.5.1, 7.5.2 [L2],
  7.4.1, 7.4.2 [L1]; browser 3.3.1, 3.4.1, 3.4.2, 3.5.1 [L1], 3.3.2–3.3.4, 3.4.6 [L2];
  authorization 8.1.1, 8.2.1, 8.2.2, 8.3.1 [L1], 8.1.2, 8.2.3, 8.4.1 [L2], 15.3.1 [L1],
  15.3.3, 15.3.4 [L2]; logging and errors 16.1.1, 16.2.1–16.2.5, 16.3.1–16.3.4, 16.4.1–16.4.3,
  16.5.1–16.5.3 [L2]; data protection 14.2.1 [L1], 14.1.1, 14.1.2, 14.2.2–14.2.4, 14.3.2,
  14.3.3 [L2], 11.3.2, 12.1.1 [L1], 11.1.1, 11.1.2, 11.3.3, 12.3.1–12.3.3 [L2];
  configuration 13.4.1, 15.2.1, 4.1.1 [L1], 13.3.1, 13.3.2, 13.4.2–13.4.5, 13.2.4, 13.2.5,
  15.1.2, 15.2.3, 4.1.3 [L2]; anti-automation 2.2.2 [L1], 2.1.3, 2.3.2, 2.4.1, 15.1.3, 15.2.2
  [L2]. Credential revocation (6.5.6), immediate effect of authorization changes (8.3.2) and
  secret rotation (13.1.4, 13.3.4) are **L3** in 5.0 — we treat them as L2 for this product.

## SOC 2 Trust Services Criteria (2017, points of focus revised 2022)

- The AICPA PDF is behind a free login; criterion wording below is from reproductions and is to
  be checked against the PDF before an audit. The 2022 revision changed points of focus only;
  AICPA states it does not alter the 2017 criteria.
- Common criteria: CC1 Control Environment, CC2 Communication and Information, CC3 Risk
  Assessment, CC4 Monitoring Activities, CC5 Control Activities, **CC6 Logical and Physical
  Access**, **CC7 System Operations**, **CC8 Change Management**, CC9 Risk Mitigation.
- CC6.1 logical access security over protected information assets · CC6.2 register and
  authorize users before issuing credentials, remove when no longer valid · CC6.3 access by
  role, least privilege, segregation of duties · CC6.4 physical access · CC6.5 disposal of
  assets · CC6.6 protection against threats from outside the system boundary · CC6.7 restrict
  transmission and removal of information, protect in transit · CC6.8 prevent or detect
  unauthorized or malicious software.
- CC7.1 detect configuration changes and new vulnerabilities · CC7.2 monitor for anomalies and
  analyse security events · CC7.3 evaluate events, act on incidents · CC7.4 incident-response
  program · CC7.5 recovery.
- CC8.1 authorize, design, test, approve and implement changes to infrastructure, data,
  software and procedures.
- **Type I** reports on the design of controls as of a date; **Type II** on design and operating
  effectiveness over a period. No AICPA minimum window is published; practitioners cite 3
  months as the practical floor, 6 months common for a first report, 12 the norm afterwards.
- Categories: Security is required; add **Availability** when contracts carry uptime
  commitments and **Confidentiality** when committing to protect and delete customer data
  (typical for processors); Privacy fits controllers; Processing Integrity is rare. Our
  expected scope: Security + Availability + Confidentiality.

## OWASP API Security Top 10 (2023, released 2023-06-05; no newer edition)

API1 Broken Object Level Authorization · API2 Broken Authentication · API3 Broken Object
Property Level Authorization · API4 Unrestricted Resource Consumption · API5 Broken Function
Level Authorization · API6 Unrestricted Access to Sensitive Business Flows · API7 Server Side
Request Forgery · API8 Security Misconfiguration · API9 Improper Inventory Management ·
API10 Unsafe Consumption of APIs.

## OWASP Top 10 for LLM Applications (version 2026)

Published August–September 2026 (the resource page says 2026-08-03, the press release
2026-09-01; the PDF cover carries a placeholder — exact day weakly sourced). Agentic risks moved
to the separate Top 10 for Agentic Applications. LLM01 Prompt Injection · LLM02 Sensitive
Information Disclosure · LLM03 Excessive Agency · LLM04 Supply Chain · LLM05 Data and Model
Poisoning · LLM06 Unbounded Consumption · LLM07 Misinformation · LLM08 Hidden Context Exposure
(replaces System Prompt Leakage) · LLM09 Vector and Embedding Weaknesses · LLM10 Improper
Output Handling. Previous edition: 2025 (2024-11-18).

## Sources (accessed 2026-10-17)

ASVS: raw.githubusercontent.com/OWASP/ASVS/v5.0.0_release/5.0/en/ (0x01 Frontispiece, 0x03
What is the ASVS, 0x05 For users of 4.0, chapters 0x11–0x25) · github.com/OWASP/ASVS/releases ·
owasp.org/www-project-application-security-verification-standard/.
SOC 2: aicpa-cima.com/resources/download/2017-trust-services-criteria-with-revised-points-of-focus-2022
(login) · aicpa-cima.com/professional-insights/video/maintaining-high-standards-for-soc-engagements ·
*secondary:* vanta.com/resources/soc-2s-trust-service-criteria · documentation.wazuh.com/current/compliance/tsc/ ·
opensecurityarchitecture.org/frameworks/soc2-tsc · linfordco.com/blog/period-covered-type-ii-soc-examination/ ·
sans.org/blog/soc-2-trust-services-categories.
API Top 10: owasp.github.io/www-project-api-security/ · api-security.owasp.org/editions/2023/en/0x11-t10/.
LLM Top 10: genai.owasp.org/resource/owasp-genai-llm-top-10-2026/ · genai.owasp.org/download/56857/ (PDF) ·
genai.owasp.org/2026/09/01/… (press release) · genai.owasp.org/llm-top-10/ (2025 list).
