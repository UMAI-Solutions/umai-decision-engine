"""Core entity catalogue (docs/architecture/l0-entity-detection.md, "core" scope).

Patterns use ``[0-9]`` (never ``\\d``) and digit lookarounds instead of ``\\b`` so that
suffixed Turkish words (``10000000146dır``) still match. Scores are calibrated so that a
strong checksum passes ``min_score`` 0.5 alone, while weak ones need context.
"""

from __future__ import annotations

import phonenumbers

from umai_decision_engine.l0.recognizers.base import Candidate, PatternSpec, RecognizerSpec
from umai_decision_engine.l0.validators import (
    card_is_valid,
    digits_only,
    iban_mod97_is_valid,
    tckn_is_valid,
    tr_iban_is_valid,
)

# ---------------------------------------------------------------- TCKN


# Score of an 11-digit number that fails the TCKN checksum. Below min_score on its own; the
# TCKN context boost (+0.3) lifts it to 0.5, so "kimlik no <typo or fake ID>" is still caught
# while a random 11-digit number without ID context is not.
_TCKN_UNVALIDATED_SCORE = 0.2


def _refine_tckn(fragment: str) -> list[Candidate]:
    digits = digits_only(fragment)
    if tckn_is_valid(digits):
        return [Candidate(0, len(fragment), True)]
    if len(set(digits)) <= 2:  # 11111111111, 10000000000 … are placeholders, not IDs
        return []
    return [Candidate(0, len(fragment), False, score=_TCKN_UNVALIDATED_SCORE)]


TR_TCKN = RecognizerSpec(
    entity="TR_TCKN",
    patterns=(
        PatternSpec("tckn_contiguous", r"(?<![0-9])[1-9][0-9]{10}(?![0-9])", 0.6),
        PatternSpec(
            "tckn_grouped",
            r"(?<![0-9])[1-9][0-9]{2}(?:[ .\-][0-9]{3}){2}[ .\-][0-9]{2}(?![0-9])",
            0.4,
        ),
    ),
    canonical=digits_only,
    refine=_refine_tckn,
    context=(
        "tc",
        "t.c.",
        "tckn",
        "kimlik",
        "nüfus",
        "vatandaşlık",
        "national id",
        "identity",
        "id no",
    ),
    context_boost=0.3,
    # GİB e-Arşiv placeholder for consumers without a TCKN (fails the checksum anyway).
    known_non_pii=frozenset({"11111111111"}),
    priority=4,
)

# ---------------------------------------------------------------- IBAN


def _iban_canonical(value: str) -> str:
    return "".join(ch for ch in value if not ch.isspace()).upper()


def _alnum_end_offsets(fragment: str) -> list[tuple[int, int]]:
    """(end offset in fragment, number of alphanumerics up to it), longest first."""

    ends: list[tuple[int, int]] = []
    count = 0
    for i, ch in enumerate(fragment):
        if not ch.isspace():
            count += 1
            ends.append((i + 1, count))
    return list(reversed(ends))


def _refine_tr_iban(fragment: str) -> list[Candidate]:
    for end, count in _alnum_end_offsets(fragment):
        if count == 26 and tr_iban_is_valid(_iban_canonical(fragment[:end])):
            return [Candidate(0, end, True)]
    return []


def _refine_iban(fragment: str) -> list[Candidate]:
    # Generic IBANs: try the longest prefix that passes mod 97 (lengths 15–34), so trailing
    # capital letters or digits glued to the IBAN do not make the whole match fail.
    for end, count in _alnum_end_offsets(fragment):
        if 15 <= count <= 34 and iban_mod97_is_valid(_iban_canonical(fragment[:end])):
            return [Candidate(0, end, True)]
    return []


TR_IBAN = RecognizerSpec(
    entity="TR_IBAN",
    patterns=(
        PatternSpec(
            "tr_iban", r"(?<![A-Za-z0-9])(?:TR|tr)[0-9]{2}(?: ?[0-9A-Z]){22,30}(?![a-z0-9])", 0.75
        ),
    ),
    canonical=_iban_canonical,
    refine=_refine_tr_iban,
    context=("iban", "hesap", "banka", "eft", "havale", "account", "bank"),
    context_boost=0.2,
    priority=6,
)

IBAN_CODE = RecognizerSpec(
    entity="IBAN_CODE",
    patterns=(
        PatternSpec(
            "iban",
            r"(?<![A-Za-z0-9])(?!TR|tr)[A-Z]{2}[0-9]{2}(?: ?[0-9A-Z]){11,36}(?![a-z0-9])",
            0.6,
        ),
    ),
    canonical=_iban_canonical,
    refine=_refine_iban,
    context=("iban", "hesap", "banka", "account", "bank", "swift"),
    context_boost=0.2,
    priority=5,
)

# ---------------------------------------------------------------- phone


def _phone_canonical(value: str) -> str:
    try:
        number = phonenumbers.parse(value, "TR")
    except phonenumbers.NumberParseException:
        return digits_only(value)
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


_PHONE_TYPES = {
    phonenumbers.PhoneNumberType.MOBILE,
    phonenumbers.PhoneNumberType.FIXED_LINE,
    phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE,
    phonenumbers.PhoneNumberType.VOIP,
    phonenumbers.PhoneNumberType.TOLL_FREE,
    phonenumbers.PhoneNumberType.PERSONAL_NUMBER,
    phonenumbers.PhoneNumberType.UAN,
}


_MOBILE_SCORE = 0.6


def _refine_tr_phone(fragment: str) -> list[Candidate]:
    try:
        number = phonenumbers.parse(fragment, "TR")
    except phonenumbers.NumberParseException:
        return []
    if number.country_code != 90 or not phonenumbers.is_valid_number(number):
        return []
    number_type = phonenumbers.number_type(number)
    if number_type not in _PHONE_TYPES:
        return []
    # A valid Turkish mobile number (5xx ranges) is specific enough without context, even when
    # written bare ("5321234567"); bare fixed-line numbers keep needing context.
    if number_type == phonenumbers.PhoneNumberType.MOBILE:
        return [Candidate(0, len(fragment), True, score=_MOBILE_SCORE)]
    return [Candidate(0, len(fragment), True)]


_NSN = r"\(?[2-58][0-9]{2}\)?[ .\-]?[0-9]{3}[ .\-]?[0-9]{2}[ .\-]?[0-9]{2}"

TR_PHONE = RecognizerSpec(
    entity="TR_PHONE",
    patterns=(
        PatternSpec(
            "tr_phone_prefixed", r"(?<![0-9+])(?:\+90|0090|90|0)[ .\-]?" + _NSN + r"(?![0-9])", 0.55
        ),
        PatternSpec("tr_phone_bare", r"(?<![0-9+])" + _NSN + r"(?![0-9])", 0.35),
    ),
    canonical=_phone_canonical,
    refine=_refine_tr_phone,
    context=(
        "tel",
        "telefon",
        "cep",
        "gsm",
        "numara",
        "ara",
        "phone",
        "mobile",
        "call",
        "whatsapp",
    ),
    context_boost=0.25,
    priority=3,
)

# ---------------------------------------------------------------- payment cards


def _refine_card(fragment: str) -> list[Candidate]:
    return [Candidate(0, len(fragment), True)] if card_is_valid(digits_only(fragment)) else []


CREDIT_CARD = RecognizerSpec(
    entity="CREDIT_CARD",
    patterns=(
        PatternSpec(
            "card_grouped", r"(?<![0-9])[0-9]{4}(?:[ \-][0-9]{4}){2}[ \-][0-9]{1,7}(?![0-9])", 0.6
        ),
        PatternSpec(
            "card_amex_grouped", r"(?<![0-9])3[47][0-9]{2}[ \-][0-9]{6}[ \-][0-9]{5}(?![0-9])", 0.6
        ),
        PatternSpec("card_contiguous", r"(?<![0-9])[0-9]{13,19}(?![0-9])", 0.55),
    ),
    canonical=digits_only,
    refine=_refine_card,
    context=(
        "kart",
        "kredi",
        "banka kartı",
        "card",
        "credit",
        "visa",
        "mastercard",
        "troy",
        "amex",
        "cvv",
    ),
    context_boost=0.3,
    priority=4,
)

# ---------------------------------------------------------------- e-mail

EMAIL_ADDRESS = RecognizerSpec(
    entity="EMAIL_ADDRESS",
    patterns=(
        PatternSpec(
            "email",
            r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?"
            r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*\.[A-Za-z]{2,24}(?![A-Za-z0-9\-])",
            0.8,
        ),
    ),
    canonical=lambda value: value.strip().lower(),
    context=("e-posta", "eposta", "mail", "email", "e-mail"),
    context_boost=0.1,
    priority=2,
)

CORE_SPECS: tuple[RecognizerSpec, ...] = (
    TR_TCKN,
    TR_IBAN,
    IBAN_CODE,
    TR_PHONE,
    CREDIT_CARD,
    EMAIL_ADDRESS,
)

SPECS_BY_ENTITY: dict[str, RecognizerSpec] = {spec.entity: spec for spec in CORE_SPECS}

SUPPORTED_ENTITIES: frozenset[str] = frozenset(SPECS_BY_ENTITY)


# Tag codes emitted by L0 (docs/contracts/tag-taxonomy.md).
TAG_BY_ENTITY: dict[str, str] = {
    "TR_TCKN": "data.pii.tr_tckn",
    "TR_IBAN": "data.pii.tr_iban",
    "IBAN_CODE": "data.pii.iban",
    "TR_PHONE": "data.pii.tr_phone",
    "CREDIT_CARD": "data.pii.credit_card",
    "EMAIL_ADDRESS": "data.pii.email",
}
