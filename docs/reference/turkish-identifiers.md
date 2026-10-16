# Turkish identifiers and payment cards

**Last verified:** 2026-10-15 · **Used by:** L0 validators and tests · Labels: see [README](README.md)

All vectors below were computed by script and re-checked independently on 2026-10-15
(cross-checked against python-stdnum formulas, 0 mismatches over 100k random samples).
Use only these or freshly generated synthetic vectors in tests. **Write our own checksum
code** — python-stdnum is LGPL.

## TCKN — T.C. Kimlik Numarası (11 digits)

Rules:

- 11 digits, first digit ≠ 0.
- `d10 = (7·(d1+d3+d5+d7+d9) − (d2+d4+d6+d8)) mod 10`
- `d11 = (d1+…+d10) mod 10` — always even.

Sources: [python-stdnum tckimlik](https://github.com/arthurdejong/python-stdnum/blob/master/stdnum/tr/tckimlik.py),
[tr.wikipedia](https://tr.wikipedia.org/wiki/T.C._Kimlik_Numarası), Presidio `TrNationalIdRecognizer`.

| Valid | Invalid |
|---|---|
| `17291716060` (stdnum doctest) | `17291716050` (checksum) |
| `10000000146` | `07291716092` (leading 0) |
| `12345678950` | `1729171606` (length) |
| `43770009392` | `12345678901` |
| `96696370398` | `10000000147` |
| `92041534996` | `43770009393` |
| `72000876268` | `96696370399` |
| `99123456740` (99-prefix; foreigner-number checksum **unverified**) | |

- **`11111111111`** is GİB's documented placeholder for a consumer without a TCKN on
  e-Arşiv invoices; it fails the checksum. Treat as a known non-PII value.
- ~1 % of random 11-digit strings pass the checksum.
- Online NVİ validation needs name + birth year and would send PII out of L0 — never use it.

## VKN — Vergi Kimlik Numarası (10 digits)

Rules (GİB does not publish the algorithm; python-stdnum's version):

- For each of the first 9 digits `dᵢ` (i = 1..9, left to right): `p = (dᵢ + 10 − i) mod 10`.
- If `p ≠ 0`: `q = (p · 2^(10−i)) mod 9`; if `q = 0` then `q = 9`. If `p = 0`, contributes 0.
- `d10 = (10 − (Σq mod 10)) mod 10`.

Natural persons use their TCKN as tax ID since 2006-07-01.

| Valid | Invalid |
|---|---|
| `4540536920` (stdnum doctest) | `4540536921` |
| `9876543217` | `0000000000` |
| `5555555553` | `1111111111` |
| `1000000000` | `454053692` (length) |
| `0000000019` | |
| `1234567890` (valid! add to known-non-PII list) | |

**~10 % of random 10-digit strings pass** → VKN must require context words; a checksum
pass alone must not produce a confident hit.

## Turkish IBAN (26 characters)

`TR` + 2 check digits + 5-digit bank code + 1 reserved digit (`0`) + 16 alphanumeric
account characters. ISO 7064 MOD 97-10: move first 4 chars to the end, letters → numbers
(A=10 … Z=35), result mod 97 = 1. Strict regex: `TR\d{2}\d{5}0[0-9A-Z]{16}` (after removing
spaces). Source: [TCMB IBAN communiqué 2008/6](https://www.tcmb.gov.tr/wps/wcm/connect/EN/TCMB+EN/Bottom+Menu/IBAN/Communique).

| Valid | Invalid |
|---|---|
| `TR33 0006 1005 1978 6457 8413 26` (public illustrative example) | `TR34 0006 1005 1978 6457 8413 26` (mod 97 = 2) |
| `TR750009900000001234567890` | `TR750090900000001234567890` (transposition) |
| `TR57123450ABCD000000000001` | |

~1 % of random strings pass mod 97.

## Turkish phone numbers

From [libphonenumber metadata](https://github.com/google/libphonenumber/blob/master/resources/PhoneNumberMetadata.xml):
country code `+90`, trunk prefix `0`, international prefix `00`, national significant
number 10 digits.

| Type | Pattern / example |
|---|---|
| Mobile | `5(?:[03-5]\d|1[06]|24|6[24]|7[245]|9[46])\d{7}` (+ small 561/582 ranges) — `+90 501 234 56 78` |
| Fixed line | starts 2/3/4 — `+90 212 345 67 89` |
| VoIP | `850` — `0850 012 34 56` |
| Toll-free | `800`, `811` — `0800 123 45 67` |
| Premium | `900`, `888`, `898` |
| UAN | `444 xxxx` (7 digits) — `444 1 444` |
| Pager / GMPCS | `512` / `592` |

Written forms to support: `+90 532 123 45 67`, `0 (532) 123 45 67`, `0532-123-45-67`,
`532 123 45 67`.
Invalid: `0600 123 45 67` (no 6xx), `0532 123 45` (short), `0599 123 45 67`, `0210 123 45 67`
(pattern-shaped but no number type) — checked against metadata regexes, not executed through
`phonenumbers`.

## Vehicle plates

Province code 01–81; civilian formats `99 X 9999`, `99 X 99999`, `99 XX 999`, `99 XX 9999`,
`99 XXX 99`, `99 XXX 999`; letters exclude Ç Ş İ Ö Ü Ğ Q W X
([Wikipedia](https://en.wikipedia.org/wiki/Vehicle_registration_plates_of_Turkey)).

Case-sensitive regex (tested with `regex` 2024.11.6):

```text
(?-i:(?<![\w])(?:0[1-9]|[1-7]\d|8[01])[ -]?(?:[A-PR-VYZ][ -]?\d{4,5}|[A-PR-VYZ]{2}[ -]?\d{3,4}|[A-PR-VYZ]{3}[ -]?\d{2,3})(?![\w]))
```

Matches `34 ABC 123`, `06 A 12345`, `34 TAA 99`, `34-AB-1234`. Rejects `12 eki 2024`,
`10 kg 500`, `90 AB 123`, `34 QA 123`. Presidio's shipped plate regex runs case-insensitive
and matches `12 eki 2024` / `10 kg 500` with score 1.0 — do not use it.

## Payment cards (Luhn)

Double every second digit from the right (subtract 9 if > 9); sum mod 10 = 0.

| Valid | Note |
|---|---|
| `4242424242424242` | Visa (Stripe test) |
| `5555555555554444` | Mastercard (Stripe test) |
| `2223003122003222` | **Mastercard 2-series** — missed by Presidio's default regex |
| `378282246310005` | Amex (Stripe test) |
| `9792000000000003` | **TROY** (Turkish domestic scheme, IIN `9792`) — synthetic; missed by Presidio's default regex |

Invalid: `4242424242424241`. ~10 % of random 16-digit strings pass Luhn → require a known
IIN prefix and/or context.

## Pass rates of random strings (computed, 200k samples)

| Check | Random pass rate | Consequence |
|---|---|---|
| TCKN | 1.02 % | checksum + pattern is a strong signal |
| VKN | **10.0 %** | context required |
| Luhn (16 digits) | 9.96 % | IIN prefix or context required |
| TR IBAN mod 97 | 1.04 % | `TR` prefix + checksum is a strong signal |
