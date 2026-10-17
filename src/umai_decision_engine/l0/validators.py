"""Checksum validators for L0 entities.

Pure functions over canonical values. Algorithms and test vectors:
docs/reference/turkish-identifiers.md. Written in-house on purpose (python-stdnum is LGPL).
"""

from __future__ import annotations


def digits_only(value: str) -> str:
    """Keep ASCII digits only (callers normalize to NFKC first)."""

    return "".join(ch for ch in value if "0" <= ch <= "9")


def tckn_is_valid(value: str) -> bool:
    """T.C. Kimlik No: 11 digits, first non-zero, two check digits."""

    if len(value) != 11 or not value.isascii() or not value.isdigit() or value[0] == "0":
        return False
    d = [int(ch) for ch in value]
    if (7 * sum(d[0:9:2]) - sum(d[1:8:2])) % 10 != d[9]:
        return False
    return sum(d[:10]) % 10 == d[10]


def vkn_is_valid(value: str) -> bool:
    """Vergi Kimlik No: 10 digits, last digit is a check digit."""

    if len(value) != 10 or not value.isascii() or not value.isdigit():
        return False
    total = 0
    for i, ch in enumerate(value[:9]):
        p = (int(ch) + 9 - i) % 10
        if p:
            q = (p * 2 ** (9 - i)) % 9
            total += q or 9
    return (10 - total % 10) % 10 == int(value[9])


def iban_mod97_is_valid(value: str) -> bool:
    """ISO 7064 MOD 97-10 over a canonical (uppercase, no spaces) IBAN."""

    if len(value) < 15 or len(value) > 34 or not value.isascii() or not value.isalnum():
        return False
    if not (value[:2].isalpha() and value[:2].isupper() and value[2:4].isdigit()):
        return False
    rearranged = value[4:] + value[:4]
    try:
        number = "".join(str(int(ch, 36)) for ch in rearranged)
    except ValueError:
        return False
    return int(number) % 97 == 1


def tr_iban_is_valid(value: str) -> bool:
    """Turkish IBAN: TR + 2 check + 5 bank + reserved 0 + 16 alphanumerics, mod 97."""

    if len(value) != 26 or not value.startswith("TR"):
        return False
    if not value[2:9].isdigit() or value[9] != "0":
        return False
    account = value[10:]
    if not all(ch.isdigit() or ("A" <= ch <= "Z") for ch in account):
        return False
    return iban_mod97_is_valid(value)


def luhn_is_valid(value: str) -> bool:
    """Luhn checksum over a digit string."""

    if not value or not value.isascii() or not value.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(value)):
        digit = int(ch)
        if i % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


# Issuer identification prefixes we accept as payment cards. TROY (9792) and the
# Mastercard 2-series are listed because Presidio's default card regex misses them.
_CARD_PREFIXES: tuple[tuple[int, int, int], ...] = (
    # (prefix length, low, high) inclusive ranges
    (1, 4, 4),  # Visa
    (2, 51, 55),  # Mastercard
    (4, 2221, 2720),  # Mastercard 2-series
    (2, 34, 34),  # Amex
    (2, 37, 37),  # Amex
    (4, 6011, 6011),  # Discover
    (3, 644, 649),  # Discover
    (2, 65, 65),  # Discover
    (4, 3528, 3589),  # JCB
    (3, 300, 305),  # Diners
    (2, 36, 36),  # Diners
    (2, 38, 39),  # Diners
    (4, 9792, 9792),  # TROY
    (2, 62, 62),  # UnionPay
    (4, 5018, 5018),  # Maestro
    (4, 5020, 5020),  # Maestro
    (4, 5038, 5038),  # Maestro
    (4, 5893, 5893),  # Maestro
    (4, 6304, 6304),  # Maestro
    (4, 6759, 6759),  # Maestro (UK)
    (4, 6761, 6763),  # Maestro
)


def card_iin_is_known(value: str) -> bool:
    """True when the digit string starts with a known card issuer prefix."""

    for length, low, high in _CARD_PREFIXES:
        if len(value) >= length and low <= int(value[:length]) <= high:
            return True
    return False


def card_is_valid(value: str) -> bool:
    """Payment card: 13–19 digits, known issuer prefix, Luhn."""

    return 13 <= len(value) <= 19 and card_iin_is_known(value) and luhn_is_valid(value)


# ---------------------------------------------------------------- United States


def us_ssn_is_valid(value: str) -> bool:
    """US Social Security number (9 digits): area not 000 / 666 / 900–999, group not 00,
    serial not 0000 (the patterns Lakera documents as "not flagged" too)."""

    if len(value) != 9 or not value.isascii() or not value.isdigit():
        return False
    area, group, serial = int(value[:3]), int(value[3:5]), int(value[5:])
    return area not in (0, 666) and area < 900 and group != 0 and serial != 0


# ---------------------------------------------------------------- Europe
# Algorithms: docs/reference/eu-identifiers.md (canonical examples are unit-tested).

_NINO_BAD_FIRST = frozenset("DFIQUV")
_NINO_BAD_SECOND = frozenset("DFIOQUV")
_NINO_BAD_PREFIXES = frozenset({"BG", "GB", "KN", "NK", "NT", "TN", "ZZ"})


def uk_nino_is_valid(value: str) -> bool:
    """UK National Insurance number: 2 letters, 6 digits, suffix A–D; HMRC prefix rules."""

    if len(value) != 9 or not value.isascii():
        return False
    prefix, digits, suffix = value[:2], value[2:8], value[8]
    if not (prefix.isalpha() and prefix.isupper() and digits.isdigit() and suffix in "ABCD"):
        return False
    return (
        prefix[0] not in _NINO_BAD_FIRST
        and prefix[1] not in _NINO_BAD_SECOND
        and prefix not in _NINO_BAD_PREFIXES
    )


def de_tax_id_is_valid(value: str) -> bool:
    """German Steuerliche Identifikationsnummer: 11 digits, first not 0; in the first ten
    digits exactly one digit occurs two or three times; ISO 7064 MOD 11,10 check digit."""

    if len(value) != 11 or not value.isascii() or not value.isdigit() or value[0] == "0":
        return False
    counts = sorted((value[:10].count(d) for d in set(value[:10])), reverse=True)
    if counts[0] not in (2, 3) or (len(counts) > 1 and counts[1] > 1):
        return False
    product = 10
    for ch in value[:10]:
        total = (int(ch) + product) % 10 or 10
        product = (total * 2) % 11
    check = 11 - product
    return (0 if check == 10 else check) == int(value[10])


def fr_nir_is_valid(value: str) -> bool:
    """French NIR (numéro de sécurité sociale): 13 characters + 2-digit key = 97 − n mod 97.
    Corsica departments 2A / 2B are replaced by 19 / 18 for the computation."""

    if len(value) != 15 or not value.isascii():
        return False
    body, key = value[:13].upper(), value[13:]
    if body[0] not in "12378" or not key.isdigit():
        return False
    body = body.replace("2A", "19", 1) if body[5:7] == "2A" else body
    body = body.replace("2B", "18", 1) if body[5:7] == "2B" else body
    if not body.isdigit():
        return False
    return 97 - int(body) % 97 == int(key)


_ES_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"


def es_nif_is_valid(value: str) -> bool:
    """Spanish DNI/NIF: 8 digits + check letter (number mod 23)."""

    if len(value) != 9 or not value.isascii() or not value[:8].isdigit():
        return False
    return value[8].upper() == _ES_LETTERS[int(value[:8]) % 23]


def es_nie_is_valid(value: str) -> bool:
    """Spanish NIE: X / Y / Z (→ 0 / 1 / 2) + 7 digits + check letter."""

    if len(value) != 9 or not value.isascii() or value[0].upper() not in "XYZ":
        return False
    if not value[1:8].isdigit():
        return False
    number = "XYZ".index(value[0].upper()) * 10**7 + int(value[1:8])
    return value[8].upper() == _ES_LETTERS[number % 23]


_IT_ODD = dict(
    zip(
        "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        [
            1,
            0,
            5,
            7,
            9,
            13,
            15,
            17,
            19,
            21,
            1,
            0,
            5,
            7,
            9,
            13,
            15,
            17,
            19,
            21,
            2,
            4,
            18,
            20,
            11,
            3,
            6,
            8,
            12,
            14,
            16,
            10,
            22,
            25,
            24,
            23,
        ],
        strict=True,
    )
)


def it_fiscal_code_is_valid(value: str) -> bool:
    """Italian codice fiscale: 16 characters (6 letters, 2 digits, letter, 2 digits, letter,
    3 digits, with omocodia letters allowed in digit positions) + check character."""

    value = value.upper()
    if len(value) != 16 or not value.isascii() or not value.isalnum():
        return False
    if not (value[:6].isalpha() and value[8].isalpha() and value[11].isalpha()):
        return False
    total = 0
    for i, ch in enumerate(value[:15], start=1):
        if i % 2:
            total += _IT_ODD[ch]
        else:
            total += int(ch) if ch.isdigit() else ord(ch) - ord("A")
    return value[15] == chr(ord("A") + total % 26)


def nl_bsn_is_valid(value: str) -> bool:
    """Dutch burgerservicenummer: 9 digits, 11-proof (weights 9…2, −1)."""

    if len(value) != 9 or not value.isascii() or not value.isdigit() or value == "000000000":
        return False
    weights = (9, 8, 7, 6, 5, 4, 3, 2, -1)
    return sum(int(c) * w for c, w in zip(value, weights, strict=True)) % 11 == 0


def pl_pesel_is_valid(value: str) -> bool:
    """Polish PESEL: 11 digits, weighted checksum, valid encoded birth date."""

    if len(value) != 11 or not value.isascii() or not value.isdigit():
        return False
    weights = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)
    total = sum(int(c) * w for c, w in zip(value[:10], weights, strict=True))
    if (10 - total % 10) % 10 != int(value[10]):
        return False
    month = int(value[2:4]) % 20  # +20 / +40 / +60 / +80 encode the century
    day = int(value[4:6])
    return 1 <= month <= 12 and 1 <= day <= 31
