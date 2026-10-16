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
