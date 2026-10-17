# European and US identifiers

**Last verified:** 2026-10-17 · **Used by:** L0 validators (`l0/validators.py`) and the
`l0-pii-tr` dataset generator · Labels: see [README](README.md)

Algorithms as implemented. Each was checked by code against a widely published canonical
example (listed below); the dataset generator re-implements the same checksums independently,
and a unit test checks that both agree. Sources are the public descriptions of each scheme
(linked). Verified on 2026-10-17: SSA (randomization FAQ), HMRC (NIM39110), Spanish Interior
Ministry (DNI/NIE letter). Others are secondary sources (Wikipedia), marked as such.

| Type | Format | Validation | Canonical example (synthetic / published) | Source |
|---|---|---|---|---|
| `US_SSN` | `AAA-GG-SSSS` (dashes, spaces or none) | area ≠ 000, 666, 900–999; group ≠ 00; serial ≠ 0000 (Lakera documents the same non-hits) | `234-56-7890` (patterned) | [SSA: randomization FAQ](https://www.ssa.gov/employer/randomizationfaqs.html) |
| `UK_NINO` | 2 letters + 6 digits + `A`–`D` | first letter not D F I Q U V; second not D F I O Q U V; prefixes BG GB KN NK NT TN ZZ not allocated | `JG103759A` | [HMRC NIM39110](https://www.gov.uk/hmrc-internal-manuals/national-insurance-manual/nim39110) (verified 2026-10-17) |
| `DE_TAX_ID` | 11 digits, first ≠ 0 | in the first ten digits exactly one digit occurs 2 or 3 times; ISO 7064 MOD 11,10 check digit | `86095742719` | [Wikipedia: Steuerliche Identifikationsnummer](https://de.wikipedia.org/wiki/Steuerliche_Identifikationsnummer) (*secondary source*) |
| `FR_NIR` | 13 characters + 2-digit key (spaces allowed) | key = 97 − (n mod 97); Corsica 2A/2B → 19/18 for the computation | `1 85 07 75 115 003 22` (generated) | [Wikipedia: Numéro de sécurité sociale en France](https://fr.wikipedia.org/wiki/Num%C3%A9ro_de_s%C3%A9curit%C3%A9_sociale_en_France) (*secondary source*) |
| `ES_NIF` (DNI) | 8 digits + letter | letter = `TRWAGMYFPDXBNJZSQVHLCKE`[n mod 23] | `12345678Z` | [Ministerio del Interior: cálculo del dígito de control](https://www.interior.gob.es/opencms/es/servicios-al-ciudadano/tramites-y-gestiones/dni/calculo-del-digito-de-control-del-nif-nie/) |
| `ES_NIE` | X/Y/Z + 7 digits + letter | X→0, Y→1, Z→2, then as DNI | `X1234567L` | same |
| `IT_FISCAL_CODE` | 16 characters | odd/even position tables, sum mod 26 → check letter; omocodia letters allowed in digit positions | `RSSMRA85T10A562S` | [Agenzia delle Entrate / Wikipedia: Codice fiscale](https://it.wikipedia.org/wiki/Codice_fiscale) (*secondary source*) |
| `NL_BSN` | 9 digits | 11-proof: Σ dᵢ·(9…2) − d₉ ≡ 0 (mod 11) | `111222333` | [Wikipedia: Burgerservicenummer](https://nl.wikipedia.org/wiki/Burgerservicenummer) (*secondary source*) |
| `PL_PESEL` | 11 digits | weights 1 3 7 9 1 3 7 9 1 3, check = (10 − Σ mod 10) mod 10; month field +20/40/60/80 encodes century | `44051401359` | [Wikipedia: PESEL](https://en.wikipedia.org/wiki/PESEL) (*secondary source*) |

Detection rules on top of the checksums (`l0/recognizers/regional.py`):

- Plain 9–11-digit shapes (`NL_BSN`, `PL_PESEL`, `DE_TAX_ID`, `TR_VKN`, compact `US_SSN`) are
  **context-required**: a valid checksum alone is too common (BSN ~9 %, VKN ~10 %).
- Distinctive shapes (`ES_NIF`, `ES_NIE`, `IT_FISCAL_CODE`, `FR_NIR`, dashed `US_SSN`, `UK_NINO`)
  pass `min_score` on format + checksum alone.

Synthetic data only: test values are the canonical examples above or values generated with the
same checksums by `evals/datasets/generate_l0_pii_tr.py`.
