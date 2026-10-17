"""Non-Turkish phone numbers (`PHONE_NUMBER`) via libphonenumber.

Turkish numbers stay `TR_PHONE` (catalog.py); a number that parses as a valid Turkish number
is never reported here. International format (`+CC …` / `00CC …`) validated by
libphonenumber is enough on its own. National formats are ambiguous across countries and
collide with dates, amounts and codes, so they need phone context words; with context a
number that is merely *possible* for a region (e.g. regulators' fiction ranges) counts too.
"""

from __future__ import annotations

import phonenumbers

from umai_decision_engine.l0.recognizers.base import Candidate, PatternSpec, RecognizerSpec
from umai_decision_engine.l0.validators import digits_only

# Regions tried for national formats: the US and European markets (ADR-0017).
NATIONAL_REGIONS: tuple[str, ...] = (
    "US",
    "GB",
    "DE",
    "FR",
    "ES",
    "IT",
    "NL",
    "BE",
    "AT",
    "CH",
    "PL",
    "PT",
    "IE",
    "SE",
    "DK",
    "NO",
    "FI",
    "GR",
    "CZ",
    "RO",
    "HU",
    "CA",
)

_INTL_SCORE = 0.6
_NATIONAL_VALID_SCORE = 0.3  # + context 0.3 → 0.6
_NATIONAL_POSSIBLE_SCORE = 0.2  # + context 0.3 → 0.5


def _is_turkish(fragment: str) -> bool:
    try:
        return phonenumbers.is_valid_number(phonenumbers.parse(fragment, "TR"))
    except phonenumbers.NumberParseException:
        return False


def _refine_international(fragment: str) -> list[Candidate]:
    value = "+" + fragment[2:] if fragment.startswith("00") else fragment
    try:
        number = phonenumbers.parse(value, None)
    except phonenumbers.NumberParseException:
        return []
    if number.country_code == 90:
        return []
    if phonenumbers.is_valid_number(number):
        return [Candidate(0, len(fragment), True, score=_INTL_SCORE)]
    if phonenumbers.is_possible_number(number):
        return [Candidate(0, len(fragment), False, score=_NATIONAL_POSSIBLE_SCORE)]
    return []


# Which regions can produce a national number of a given shape: a trunk "0" (most of Europe)
# or a leading digit 1–9 by length (NANP 10 digits, ES/PT/PL/CZ 9, DK/NO 8, IT/GR mobiles 10).
_TRUNK_ZERO_REGIONS = ("GB", "DE", "FR", "NL", "BE", "AT", "CH", "IE", "FI", "RO", "HU", "SE", "IT")
_NO_TRUNK_REGIONS = {
    10: ("US", "CA", "IT", "GR"),
    9: ("ES", "PT", "PL", "CZ", "IT"),
    8: ("DK", "NO"),
}


def _candidate_regions(digits: str) -> tuple[str, ...]:
    if digits.startswith("0"):
        return _TRUNK_ZERO_REGIONS
    return _NO_TRUNK_REGIONS.get(len(digits), ())


def _refine_national(fragment: str) -> list[Candidate]:
    digits = digits_only(fragment)
    if len(digits) < 7 or _is_turkish(fragment):
        return []
    possible = False
    for region in _candidate_regions(digits):
        try:
            number = phonenumbers.parse(fragment, region)
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_valid_number(number):
            return [Candidate(0, len(fragment), True, score=_NATIONAL_VALID_SCORE)]
        possible = possible or phonenumbers.is_possible_number(number)
    if possible:
        return [Candidate(0, len(fragment), False, score=_NATIONAL_POSSIBLE_SCORE)]
    return []


def _refine_phone(fragment: str) -> list[Candidate]:
    if fragment.startswith(("+", "00")):
        return _refine_international(fragment)
    return _refine_national(fragment)


def _canonical(value: str) -> str:
    candidate = "+" + value[2:] if value.startswith("00") else value
    for region in (None, *NATIONAL_REGIONS):
        try:
            number = phonenumbers.parse(candidate, region)
        except phonenumbers.NumberParseException:
            continue
        return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    return digits_only(value)


_SEP = r"[ .\-]?"

PHONE_NUMBER = RecognizerSpec(
    entity="PHONE_NUMBER",
    patterns=(
        PatternSpec(
            "phone_international",
            r"(?<![0-9A-Za-z+])(?:\+|00)(?!90)[1-9][0-9]{0,2}"
            + _SEP
            + r"(?:\(?[0-9]{1,4}\)?"
            + _SEP
            + r")?[0-9]{2,4}(?:"
            + _SEP
            + r"[0-9]{2,4}){1,4}(?![0-9])",
            _INTL_SCORE,
        ),
        PatternSpec(
            "phone_national",
            # at least one separator or parenthesis: a contiguous digit run is a code, not a phone
            r"(?<![0-9A-Za-z+.,\-/])(?=[0-9()]*[ .\-()])\(?0?[1-9][0-9]{0,3}\)?"
            + _SEP
            + r"[0-9]{2,4}(?:"
            + _SEP
            + r"[0-9]{2,4}){1,3}(?![0-9]|[.,][0-9])",
            _NATIONAL_VALID_SCORE,
            requires_context=True,
        ),
    ),
    canonical=_canonical,
    refine=_refine_phone,
    context=(
        "phone",
        "tel",
        "telephone",
        "mobile",
        "cell",
        "call",
        "ring",
        "whatsapp",
        "fax",
        "telefon",  # not "numara": "sipariş numarası" is an order number
        "cep",
        "gsm",
        "téléphone",
        "portable",
        "appelez",
        "appeler",
        "appelle",
        "rufen",
        "anrufen",
        "handy",
        "telefonnummer",
        "llámame",
        "llamar",
        "teléfono",
        "móvil",
        "chiamami",
        "chiamare",
        "telefono",
        "cellulare",
        "bel",
        "bellen",
        "telefoon",
        "mobiel",
        "zadzwoń",
        "telefonu",
    ),
    context_boost=0.3,
    priority=3,
)
