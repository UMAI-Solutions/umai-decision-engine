"""Country identifiers beyond the core: US SSN, Turkish VKN and plates, European national IDs
(docs/architecture/l0-entity-catalog.md; algorithms in docs/reference/eu-identifiers.md and
docs/reference/turkish-identifiers.md).

Scores: a format that is distinctive and checksum-valid passes ``min_score`` 0.5 on its own;
numbers that collide with other digit strings (VKN, BSN, PESEL, DE tax ID — 9 to 11 plain
digits) need context words.
"""

from __future__ import annotations

from collections.abc import Callable

from umai_decision_engine.l0.recognizers.base import Candidate, PatternSpec, RecognizerSpec
from umai_decision_engine.l0.validators import (
    de_tax_id_is_valid,
    digits_only,
    es_nie_is_valid,
    es_nif_is_valid,
    fr_nir_is_valid,
    it_fiscal_code_is_valid,
    nl_bsn_is_valid,
    pl_pesel_is_valid,
    uk_nino_is_valid,
    us_ssn_is_valid,
    vkn_is_valid,
)


def _alnum(value: str) -> str:
    return "".join(ch for ch in value if ch.isalnum()).upper()


def _validated_by(
    check: Callable[[str], bool], canonical: Callable[[str], str] = digits_only
) -> Callable[[str], list[Candidate]]:
    def _refine(fragment: str) -> list[Candidate]:
        return [Candidate(0, len(fragment), True)] if check(canonical(fragment)) else []

    return _refine


# ---------------------------------------------------------------- United States

US_SSN = RecognizerSpec(
    entity="US_SSN",
    patterns=(
        PatternSpec("ssn_dashed", r"(?<![0-9\-])[0-9]{3}-[0-9]{2}-[0-9]{4}(?![0-9\-])", 0.6),
        PatternSpec(
            "ssn_spaced",
            r"(?<![0-9])[0-9]{3} [0-9]{2} [0-9]{4}(?![0-9])",
            0.3,
            requires_context=True,
        ),
        PatternSpec("ssn_compact", r"(?<![0-9])[0-9]{9}(?![0-9])", 0.15, requires_context=True),
    ),
    canonical=digits_only,
    refine=_validated_by(us_ssn_is_valid),
    context=("ssn", "social security", "sosyal güvenlik", "w-2", "w2", "taxpayer"),
    context_boost=0.35,
    priority=4,
)

# ---------------------------------------------------------------- Türkiye

TR_VKN = RecognizerSpec(
    entity="TR_VKN",
    patterns=(
        PatternSpec("vkn", r"(?<![0-9])[0-9]{10}(?![0-9])", 0.2, requires_context=True),
        PatternSpec(
            "vkn_grouped",
            r"(?<![0-9])[0-9]{3}[ .\-][0-9]{3}[ .\-][0-9]{4}(?![0-9])",
            0.2,
            requires_context=True,
        ),
    ),
    canonical=digits_only,
    refine=_validated_by(vkn_is_valid),
    # ~10 % of random 10-digit strings pass the checksum: context is required (+0.45).
    context=(
        "vergi",
        "vkn",
        "vergi no",
        "vergi kimlik",
        "vergi dairesi",
        "v.d",
        "tax id",
        "tax number",
    ),
    context_boost=0.45,
    # Checksum-valid but obviously not tax IDs (docs/reference/turkish-identifiers.md).
    known_non_pii=frozenset({"1234567890", "0000000019", "1000000000"}),
    priority=5,  # beats a phone-shaped reading when "vergi" is next to it
)

_PLATE_LETTERS = "A-PR-VYZ"  # no Ç Ş İ Ö Ü Ğ Q W X

TR_PLATE = RecognizerSpec(
    entity="TR_PLATE",
    patterns=(
        PatternSpec(
            "tr_plate",
            r"(?<![\w])(?:0[1-9]|[1-7][0-9]|8[01])[ \-]?"
            rf"(?:[{_PLATE_LETTERS}][ \-]?[0-9]{{4,5}}|[{_PLATE_LETTERS}]{{2}}[ \-]?[0-9]{{3,4}}"
            rf"|[{_PLATE_LETTERS}]{{3}}[ \-]?[0-9]{{2,3}})(?![\w])",
            0.3,
            requires_context=True,  # "34 TL 100" is an amount, not a plate
        ),
    ),
    canonical=lambda value: _alnum(value),
    context=(
        "plaka",
        "araç",
        "araba",
        "otomobil",
        "kaza",
        "tutanak",
        "ruhsat",
        "otopark",
        "trafik",
        "vehicle",
        "license plate",
        "plate",
    ),
    context_boost=0.35,
    priority=3,
)

# ---------------------------------------------------------------- Europe

UK_NINO = RecognizerSpec(
    entity="UK_NINO",
    patterns=(
        PatternSpec(
            "nino",
            r"(?<![A-Za-z0-9])[A-Z]{2} ?[0-9]{2} ?[0-9]{2} ?[0-9]{2} ?[A-D](?![A-Za-z0-9])",
            0.5,
        ),
    ),
    canonical=_alnum,
    refine=_validated_by(uk_nino_is_valid, _alnum),
    context=("national insurance", "ni number", "nino", "nin"),
    context_boost=0.3,
    priority=5,
)

DE_TAX_ID = RecognizerSpec(
    entity="DE_TAX_ID",
    patterns=(
        PatternSpec("de_tax_id", r"(?<![0-9])[1-9][0-9]{10}(?![0-9])", 0.25, requires_context=True),
        PatternSpec(
            "de_tax_id_grouped",
            r"(?<![0-9])[1-9][0-9] ?[0-9]{3} ?[0-9]{3} ?[0-9]{3}(?![0-9])",
            0.25,
            requires_context=True,
        ),
    ),
    canonical=digits_only,
    refine=_validated_by(de_tax_id_is_valid),
    context=(
        "steuer-id",
        "steuer id",
        "steueridentifikationsnummer",
        "steuerliche",
        "identifikationsnummer",
        "idnr",
        "tin",
        "tax id",
    ),
    context_boost=0.4,
    priority=5,
)

FR_NIR = RecognizerSpec(
    entity="FR_NIR",
    patterns=(
        PatternSpec(
            "fr_nir",
            r"(?<![0-9A-Za-z])[12378] ?[0-9]{2} ?(?:0[1-9]|1[0-2]|[2-9][0-9]) ?(?:[0-9]{2}|2A|2B)"
            r" ?[0-9]{3} ?[0-9]{3} ?[0-9]{2}(?![0-9A-Za-z])",
            0.55,
        ),
    ),
    canonical=_alnum,
    refine=_validated_by(fr_nir_is_valid, _alnum),
    context=(
        "sécurité sociale",
        "securite sociale",
        "nir",
        "insee",
        "numéro de sécu",
        "carte vitale",
    ),
    context_boost=0.25,
    priority=5,
)

ES_NIF = RecognizerSpec(
    entity="ES_NIF",
    patterns=(
        PatternSpec("es_dni", r"(?<![0-9A-Za-z])[0-9]{8}[ \-]?[A-Za-z](?![0-9A-Za-z])", 0.55),
    ),
    canonical=_alnum,
    refine=_validated_by(es_nif_is_valid, _alnum),
    context=("dni", "nif", "documento nacional", "identidad"),
    context_boost=0.25,
    priority=5,
)

ES_NIE = RecognizerSpec(
    entity="ES_NIE",
    patterns=(
        PatternSpec(
            "es_nie", r"(?<![0-9A-Za-z])[XYZxyz][ \-]?[0-9]{7}[ \-]?[A-Za-z](?![0-9A-Za-z])", 0.6
        ),
    ),
    canonical=_alnum,
    refine=_validated_by(es_nie_is_valid, _alnum),
    context=("nie", "extranjero", "identidad"),
    context_boost=0.25,
    priority=5,
)

IT_FISCAL_CODE = RecognizerSpec(
    entity="IT_FISCAL_CODE",
    patterns=(
        PatternSpec(
            "it_cf",
            r"(?i)(?<![0-9A-Za-z])[A-Z]{6}[0-9LMNPQRSTUV]{2}[A-EHLMPRST][0-9LMNPQRSTUV]{2}"
            r"[A-Z][0-9LMNPQRSTUV]{3}[A-Z](?![0-9A-Za-z])",
            0.7,
        ),
    ),
    canonical=_alnum,
    refine=_validated_by(it_fiscal_code_is_valid, _alnum),
    context=("codice fiscale", "cf", "fiscal code"),
    context_boost=0.2,
    priority=6,
)

NL_BSN = RecognizerSpec(
    entity="NL_BSN",
    patterns=(
        PatternSpec("nl_bsn", r"(?<![0-9])[0-9]{9}(?![0-9])", 0.2, requires_context=True),
        PatternSpec(
            "nl_bsn_grouped",
            r"(?<![0-9])[0-9]{4}\.[0-9]{2}\.[0-9]{3}(?![0-9])",
            0.2,
            requires_context=True,
        ),
    ),
    canonical=digits_only,
    refine=_validated_by(nl_bsn_is_valid),
    context=("bsn", "burgerservicenummer", "sofinummer", "citizen service number"),
    context_boost=0.4,
    priority=5,
)

PL_PESEL = RecognizerSpec(
    entity="PL_PESEL",
    patterns=(
        PatternSpec("pl_pesel", r"(?<![0-9])[0-9]{11}(?![0-9])", 0.2, requires_context=True),
    ),
    canonical=digits_only,
    refine=_validated_by(pl_pesel_is_valid),
    context=("pesel", "numer pesel"),
    context_boost=0.4,
    priority=5,
)

REGIONAL_SPECS: tuple[RecognizerSpec, ...] = (
    US_SSN,
    TR_VKN,
    TR_PLATE,
    UK_NINO,
    DE_TAX_ID,
    FR_NIR,
    ES_NIF,
    ES_NIE,
    IT_FISCAL_CODE,
    NL_BSN,
    PL_PESEL,
)
