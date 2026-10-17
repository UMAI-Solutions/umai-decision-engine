# L0 entity catalog (target)

What L0 must detect: **parity with Lakera's managed PII detectors**, extended to **Türkiye** and
**Europe** ([ADR-0017](../adr/0017-l0-coverage-lakera-parity-tr-eu.md);
vendor facts: [`reference/lakera-guard.md`](../reference/lakera-guard.md)). The `l0-pii-tr`
eval labels every type below (dataset v2), so each row has a measured recall.

Status: **done** (recognizer + tests + eval), **partial**, **missing**.

## Lakera parity

| Lakera detector | Our entity type(s) | Beyond Lakera | Method | Status |
|---|---|---|---|---|
| Name | `PERSON` | Turkish and European names, Turkish suffixes (`Ayşe'nin`) | given-name gazetteer (TR / US-UK / EU, ~480 names) + capitalised surname; names from every culture need multilingual NER — **parked by the product owner** (ADR-0020) | partial (parked) |
| Mailing address | `ADDRESS` | Turkish (`Mah.`, `Cad.`, `Sok.`, `No:`, `D:`, `İlçe/İl`) and European street formats, not only US | street-type + house-number patterns per region (TR, US, UK, DE, FR, ES/IT/PT, NL); NER optional later (ADR-0020) | done |
| Phone number (US only) | `TR_PHONE`, `PHONE_NUMBER` | Every country via libphonenumber: Türkiye, US, EU | patterns + libphonenumber validity; national formats need phone context and separators | done |
| Email (+ `[AT]`/`[DOT]`) | `EMAIL_ADDRESS` | Turkish obfuscation (`at`, `nokta`) | pattern; obfuscated form needs a known TLD | done |
| IP address | `IP_ADDRESS` | — | pattern + `ipaddress` classification (public high, private/documentation lower, loopback/link-local/multicast/reserved/well-known DNS not flagged) | done |
| Credit card | `CREDIT_CARD` | TROY (9792) | pattern + Luhn + issuer prefixes (incl. Diners 14, Maestro 19) | done |
| IBAN | `TR_IBAN`, `IBAN_CODE` | — | pattern + mod 97 | done |
| US SSN | `US_SSN` | — | pattern + area/group/serial rules (no `000`, `666`, `9xx`, `00`, `0000`); spaced/compact forms need context | done |
| Unknown links | `URL` + domain check → `UNKNOWN_LINK` | Guardrail allowlist; on-prem domain list snapshot | URL pattern + registrable domain vs curated popular list (+ optional `UMAI_DE_POPULAR_DOMAINS_FILE`) ∪ `l0.allowed_domains` | done |

## Türkiye

| Type | Method | Status |
|---|---|---|
| `TR_TCKN` | pattern + checksum (valid in any written form); unvalidated only with ID context | done |
| `TR_VKN` | pattern + checksum, **context required** (10 % random pass rate); wins over a phone-shaped reading next to "vergi" | done |
| `TR_PHONE` | done | done |
| `TR_IBAN` | done | done |
| `TR_PLATE` | case-sensitive pattern from `turkish-identifiers.md`, **context required** | done |
| `ADDRESS` (TR) | see parity table | done |

## Europe (first set: checksum-bearing national identifiers)

| Type | Country | Validation | Status |
|---|---|---|---|
| `UK_NINO` | United Kingdom | format (2 letters, 6 digits, A–D), disallowed prefixes | done |
| `DE_TAX_ID` | Germany (Steuer-IdNr) | ISO 7064 MOD 11,10 + digit-repetition rule | done |
| `FR_NIR` | France (INSEE / sécurité sociale) | 13 digits + key `97 − n mod 97` | done |
| `ES_NIF` / `ES_NIE` | Spain (DNI / NIE) | check letter `n mod 23` | done |
| `IT_FISCAL_CODE` | Italy (codice fiscale) | check character | done |
| `NL_BSN` | Netherlands | 11-proof, **context required** (9 digits) | done |
| `PL_PESEL` | Poland | weighted checksum + date | done |
| `PHONE_NUMBER` (EU) | all | libphonenumber | done |
| `IBAN_CODE` (EU) | all | mod 97 | done |

Later candidates, on evidence: EU VAT numbers, passports, Belgian / Swedish / Austrian IDs.

## Beyond PII

| Type | Method | Status |
|---|---|---|
| `API_KEY` | provider prefixes (AWS, GitHub, GitLab, Slack, Stripe, OpenAI, Anthropic, Google, SendGrid); generic 40-character AWS secret only with "secret" context | done |
| `PRIVATE_KEY` | PEM armour | done |
| `JWT` | three base64url parts, header decodes to JSON with `alg` | done |
| `CONNECTION_STRING` | URI with `user:password@` or `Server=…;Password=…` | done |
| `PASSWORD` | value after `password=` / `şifre:` / "şifresi X olarak" | done |
| custom types | `l0.custom_recognizers` (policy-bundle 0.5) | done |

## Measured (2026-10-17, dataset v2)

See the latest `evals/reports/*-l0-pii-tr-*` summary. Residual gaps: names outside the
gazetteer (NER), foreign national-format numbers without a phone word, a number split across
lines.

## Notes for policy authors

- Phone numbers are two types (`TR_PHONE`, `PHONE_NUMBER`) because Turkish policies often treat
  them differently. Type groups (e.g. "all phone numbers") belong to the guardrail model
  (step 3), not to L0.
- `UNKNOWN_LINK` is the only type whose decision depends on guardrail data (allowed domains).
